"""Suivi des téléchargements en direct + demandes /film /serie via Seerr.

Nécessite l'accès réseau bot->CT120 (ouvert dans le firewall Proxmox de CT106).
Config /opt/discord-bot/servarr-apis.json (radarr/sonarr/seerr/qbit).
"""
import asyncio
import difflib
import json
import logging
import re
import unicodedata
import urllib.parse
import urllib.request

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import channels
from ..core import format as fmt
from ..core.gates import GatedView
from ..core.http import client_for, load_service_apis
from ..core.permissions import read_check
from ..core.ui import pin_edit

log = logging.getLogger("discord-bot.medias")

DL_CHANNEL = "telechargements"
DL_STATES = ("downloading", "stalledDL", "metaDL", "forcedDL",
             "checkingDL", "allocating", "queuedDL")


def _bar(pct, width=12):
    fill = int(round(pct / 100 * width))
    return "█" * fill + "░" * (width - fill)


def _eta(s):
    s = int(s or 0)
    if s <= 0 or s >= 8640000:
        return "—"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


# Marqueurs de langue des noms de release, du plus précis au plus vague. Les langues
# parsées par Radarr/Sonarr sont trompeuses : « MULTi.VFF » ressort « Japanese » (langue
# originale) et « FRENCH » parfois « English ». Le NOM de la release fait foi. VFQ = VF
# québécoise, à distinguer de la VFF (demande Nico 2026-08-19).
_LANG_MARKERS = (
    (re.compile(r"\b(VFF|TRUEFRENCH|VF2|VOF)\b", re.I), "🇫🇷", "VFF"),
    (re.compile(r"\b(VFQ|VQ)\b|\bFRENCH[ ._-]?CA\b", re.I), "🍁", "VFQ (québécois)"),
    (re.compile(r"\bFRENCH\b", re.I), "🇫🇷", "VF"),
    (re.compile(r"\b(VF|VFI|VFO)\b", re.I), "🇫🇷", "VF"),
    (re.compile(r"\bVOSTFR\b", re.I), "⚠️", "VOSTFR (sous-titres)"),
)
_RX_MULTI = re.compile(r"\bMULTI\b", re.I)
# une liste explicite de langues [En+Hi] / (English-Hindi) SANS marqueur français
_RX_NOFR_LIST = re.compile(
    r"[\[(](?![^\])]*\b(?:fr|fre|fra|french|vf|vf2|vff|vfi|vfq|multi)\b)"
    r"[^\])]*\b(?:en|eng|english|hi|hin|hindi|es|spa|spanish|ita|italian"
    r"|kor|korean|jap|jpn|japanese|rus|russian|ger|german|de)\b[^\])]*[\])]", re.I)


def _release_lang(name, arr_langs):
    """(emoji, libellé) de la langue d'une release, depuis son NOM d'abord.

    Retombe sur les langues parsées par Radarr/Sonarr seulement si le nom ne porte
    aucun marqueur — elles servent alors d'indice, pas de verdict (⚠️, pas 🇫🇷).
    """
    name = name or ""
    for rx, emoji, label in _LANG_MARKERS:
        if rx.search(name):
            if label != "VOSTFR (sous-titres)" and _RX_MULTI.search(name):
                label += " · MULTi"
            return emoji, label
    if _RX_MULTI.search(name):
        if _RX_NOFR_LIST.search(name):
            return "⚠️", "MULTi sans piste FR listée"
        return "🇫🇷", "MULTi (VF incluse)"
    if any((l or "").lower() == "french" for l in arr_langs):
        return "🇫🇷", "/".join(arr_langs[:3])
    return "⚠️", "/".join(arr_langs[:3]) or "langue inconnue"


def _norm_titre(s):
    """minuscule, sans accents ni ponctuation, espaces compactés — pour comparer les titres.

    ⚠️ RIEN À VOIR avec `core.channels.norm`, qui normalise des NOMS DE SALON en collant
    tout l'alphanumérique (« 🔒 Lock R820 » -> « lockr820 »). Ici on retire les accents et
    on GARDE les espaces : ce sont eux qui font le score de `difflib` sur un titre de film.
    Les confondre casserait la recherche approximative. 2026-08-11.
    """
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def _match_score(query_norm, r):
    """Ressemblance [0..1] entre la recherche et un résultat (titre localisé OU original)."""
    tn = _norm_titre(r.get("title") or r.get("name") or "")
    on = _norm_titre(r.get("originalTitle") or r.get("originalName") or "")
    sim = difflib.SequenceMatcher(None, query_norm, tn).ratio()
    if on:
        sim = max(sim, difflib.SequenceMatcher(None, query_norm, on).ratio())
    # un titre qui contient la requête (ou l'inverse) est très probablement le bon
    if query_norm and (query_norm in tn or tn in query_norm or (on and (query_norm in on or on in query_norm))):
        sim = max(sim, 0.92)
    return sim


# statut Seerr mediaInfo.status -> pastille
_SEERR_STATUS = {5: "✅ dispo", 4: "✅ partiel", 3: "⏳ en cours", 2: "📥 déjà demandé"}

# seuils de ressemblance (0..1) pour classer les résultats
_EXACT_FLOOR = 0.72       # au-dessus = on considère qu'on a trouvé le bon titre
_PROBABLE_FLOOR = 0.45    # en dessous, via recherche relâchée = trop loin, on n'affiche pas


def _relax_variants(query):
    """Variantes de requête pour rattraper une faute de frappe / un nom approximatif.
    TMDB (via Seerr) n'est pas tolérant aux fautes : on essaie des formes voisines,
    puis on reclasse tout par ressemblance avec la requête ORIGINALE (seuil de garde)."""
    words = query.split()
    out = []

    def add(v):
        v = " ".join(v.split())
        if v and v.lower() != query.lower() and v not in out:
            out.append(v)

    # 1. chaque mot raccourci d'1 caractère (lettre en trop / faute en fin de mot)
    for i, w in enumerate(words):
        if len(w) >= 4:
            ww = list(words)
            ww[i] = w[:-1]
            add(" ".join(ww))
    # 2. préfixes plus courts du mot le plus long (jusqu'à 5 caractères)
    if words:
        li = max(range(len(words)), key=lambda i: len(words[i]))
        base = words[li]
        for length in range(len(base) - 2, 4, -1):
            w = list(words)
            w[li] = base[:length]
            add(" ".join(w))
    # 3. retirer les mots de fin (mots en trop)
    for i in range(len(words) - 1, 0, -1):
        add(" ".join(words[:i]))
    # 4. chaque mot seul (le plus long d'abord) + son préfixe court
    for w in sorted(words, key=len, reverse=True):
        if len(w) >= 4:
            add(w)
            if len(w) >= 7:
                add(w[:5])
    return out[:12]


class Medias(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.apis = load_service_apis()
        # None = service non configuré (core.http.client_for). Les appels Seerr passent
        # tous par ce client : url, clé et TIMEOUT posés en un seul endroit.
        self.seerr = client_for(self.apis, "seerr")
        if self.apis:
            self.dltracker.start()

    async def cog_load(self):
        self.bot.add_view(DlRefreshView(self))

    def cog_unload(self):
        if self.apis:
            self.dltracker.cancel()

    # ------------------------------------------------------------- HTTP helpers
    # Les lectures Seerr passent par `self.seerr` (core.http.ApiClient) : plus de bloc
    # urllib recopié ici. Il ne reste que qBittorrent, dont l'authentification par COOKIE
    # de session (login -> Set-Cookie -> rappel) n'est pas exprimable avec ApiClient.
    def _qbit_sync(self):
        # Sentinelle : None = injoignable/échec (distinct de [] = succès sans torrent actif).
        a = self.apis.get("qbit")
        if not a:
            return None
        data = urllib.parse.urlencode({"username": a["user"], "password": a["pass"]}).encode()
        req = urllib.request.Request(a["url"] + "/api/v2/auth/login", data=data,
                                     headers={"Referer": a["url"], "Origin": a["url"],
                                              "Content-Type": "application/x-www-form-urlencoded"})
        cookie = None
        with urllib.request.urlopen(req, timeout=10) as r:
            for h in r.headers.get_all("Set-Cookie") or []:
                part = h.split(";", 1)[0]
                if part.startswith("QBT_SID") or part.startswith("SID="):
                    cookie = part
        if not cookie:
            return None
        req2 = urllib.request.Request(a["url"] + "/api/v2/torrents/info", headers={"Cookie": cookie})
        with urllib.request.urlopen(req2, timeout=15) as r:
            out = json.loads(r.read())
        return out if isinstance(out, list) else []

    async def _qbit(self):
        try:
            return await asyncio.to_thread(self._qbit_sync)
        except Exception as e:
            log.warning("qbit: %s", e)
            return None

    def _arr_queue_sync(self):
        """{hash bas-de-casse: {langues, score CF, qualité}} depuis les files Radarr ET
        Sonarr. C'est ce qui permet d'afficher, PAR téléchargement, la langue retenue et
        le score des formats français (phase 3 revue médias 2026-08-19) — l'utilisateur
        voit AVANT la fin si un grab est bien VF. Échec d'une file = on l'omet : l'embed
        reste utile avec les infos de l'autre, et sans aucune ligne enrichie au pire."""
        out = {}
        for svc in ("radarr", "sonarr"):
            a = self.apis.get(svc)
            if not a or not a.get("key"):
                continue
            try:
                req = urllib.request.Request(
                    a["url"] + "/api/v3/queue?pageSize=200",
                    headers={"X-Api-Key": a["key"]})
                with urllib.request.urlopen(req, timeout=10) as r:
                    recs = (json.loads(r.read()) or {}).get("records") or []
            except Exception as e:  # noqa: BLE001 — enrichissement best-effort
                log.debug("file %s illisible: %s", svc, e)
                continue
            for rec in recs:
                did = str(rec.get("downloadId") or "").lower()
                if not did:
                    continue
                out[did] = {
                    "langs": [str(x.get("name")) for x in rec.get("languages") or []
                              if x.get("name")],
                    "score": rec.get("customFormatScore"),
                    "quality": ((rec.get("quality") or {}).get("quality") or {}).get("name"),
                }
        return out

    async def _arr_queue(self):
        try:
            return await asyncio.to_thread(self._arr_queue_sync)
        except Exception:  # noqa: BLE001
            return {}

    # -------------------------------------------------- salon #telechargements
    async def _ensure_dl_channel(self):
        """Salon #telechargements, DANS la catégorie de supervision — jamais ailleurs.

        La résolution de la catégorie appartient à `core.channels` (id publié par
        provision, puis nom réel « 📊 Supervision <SERVER_KEY> »). Pas de catégorie
        résolue -> AUCUNE création : `create_text_channel(category=None)` posait le salon
        à la RACINE du serveur, sans overwrites, c'est-à-dire les noms des torrents
        visibles de @everyone. Mieux vaut pas de salon du tout : provision recrée la
        CATÉGORIE (il ne connaît pas #telechargements, qui n'appartient qu'à ce cog) et
        le tour suivant de `dltracker` (600 s) repose le salon dedans. 2026-08-11.
        """
        gid = getattr(self.bot.cfg, "guild_id", None)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            return None
        info = self.bot.state.get("media_dl") or {}
        cat = channels.supervision_category(self.bot, guild)
        ch = guild.get_channel(info["channel"]) if info.get("channel") else None
        if ch is None:
            ch = await channels.ensure_channel(
                self.bot, guild, DL_CHANNEL, cat,
                topic="Téléchargements en cours (barre de progression, mise à jour ~30 s)")
            if ch is None:
                return None
        # Un homonyme resté hors catégorie hérite des permissions de là où il traîne,
        # souvent aucune : on le remet sous clé s'il est lisible de @everyone. S'il ne
        # l'est pas, on ne le déplace pas — l'utilisateur a pu le ranger exprès.
        await channels.seal_if_public(self.bot, ch, cat, why="noms des torrents en cours")
        # relire l'état : `_pin_edit` a pu y écrire « message » entre-temps
        cur = self.bot.state.get("media_dl") or {}
        if cur.get("channel") != ch.id:
            cur["channel"] = ch.id
            self.bot.state.set("media_dl", cur)
        return ch

    async def _pin_edit(self, ch, embed):
        """Message épinglé de #telechargements — la danse Discord vit dans core.ui.

        Le STOCKAGE de l'id reste ici (`state["media_dl"]["message"]`) : le migrer
        orphelinerait le message déjà épinglé et en ferait poster un DOUBLON.
        """
        info = self.bot.state.get("media_dl") or {}
        _msg, mid = await pin_edit(ch, embed, message_id=info.get("message"),
                                   view=DlRefreshView(self), label=f"#{DL_CHANNEL}", log=log)
        if mid and mid != info.get("message"):
            cur = self.bot.state.get("media_dl") or {}
            cur["message"] = mid
            self.bot.state.set("media_dl", cur)

    async def _build_dl_embed(self):
        torrents = await self._qbit()
        if torrents is None:
            # qBittorrent injoignable : état distinct (rouge) et on NE recalcule PAS la
            # variation ni le baseline media_dl_prev (préservé le temps de la panne).
            emb = discord.Embed(
                title="📥 Téléchargements",
                description="⚠️ **qBittorrent injoignable** — impossible de récupérer "
                            "l'état des téléchargements.",
                color=fmt.RED)
            emb.timestamp = discord.utils.utcnow()
            emb.set_footer(text="mise à jour automatique ~10 min · variation depuis le dernier refresh")
            return emb
        details = await self._arr_queue()   # hash -> langue/score/qualité (best-effort)
        active = [t for t in torrents if t.get("state") in DL_STATES]
        active.sort(key=lambda t: (-(t.get("dlspeed", 0)), -(t.get("progress", 0))))
        if not active:
            desc = "🟢 Aucun téléchargement actif."
            color = fmt.GREEN
        else:
            lines = []
            for t in active[:12]:
                prog = t.get("progress", 0) or 0
                size = t.get("size", 0) or 0
                pct = prog * 100
                done = size * prog          # déjà téléchargé
                left = size - done          # reste à télécharger
                d = details.get(str(t.get("hash", "")).lower()) or {}
                # la langue se lit dans le NOM de la release (VFF/VFQ/MULTi…), les
                # langues parsées par Radarr/Sonarr ne servent que de repli
                fr, langs = _release_lang(t.get("name"), d.get("langs") or [])
                extra = f" · {fr} {langs}"
                if d:
                    if d.get("score") is not None:
                        extra += f" · score {d['score']}"
                    if d.get("quality"):
                        extra += f" · {d['quality']}"
                lines.append(
                    f"`{_bar(pct)}` **{pct:4.1f} %** · {fmt.humanize_bytes(done)} / {fmt.humanize_bytes(size)}"
                    f" · {str(t.get('name','?'))[:42]}\n"
                    f"　↓ {fmt.humanize_rate(t.get('dlspeed', 0))} · ETA {_eta(t.get('eta', 0))}"
                    f" · reste {fmt.humanize_bytes(left)}{extra}")
            desc = "\n".join(lines)
            color = 0x5865F2
        emb = discord.Embed(title=f"📥 Téléchargements ({len(active)} actif{'s' if len(active) != 1 else ''})",
                            description=desc[:4000], color=color)
        emb.timestamp = discord.utils.utcnow()
        # variation depuis le dernier refresh (baisse du "reste à télécharger" = progrès)
        remaining = sum((t.get("size", 0) or 0) * (1 - (t.get("progress", 0) or 0)) for t in active)
        prev = self.bot.state.get("media_dl_prev") or {}
        pr = prev.get("remaining")
        if pr is None:
            var = "— (première mesure)"
        else:
            d = pr - remaining  # positif = téléchargé depuis le refresh
            if d > 1024 * 1024:
                var = f"📉 **{fmt.humanize_bytes(d)}** téléchargés"
            elif d < -1024 * 1024:
                var = f"📥 **{fmt.humanize_bytes(-d)}** ajoutés à la file"
            else:
                var = "= (stable)"
        emb.add_field(name="Variation depuis le refresh", value=var, inline=False)
        self.bot.state.set("media_dl_prev", {"remaining": remaining})
        emb.set_footer(text="mise à jour automatique ~10 min · variation depuis le dernier refresh")
        return emb

    @tasks.loop(seconds=600)
    async def dltracker(self):
        try:
            ch = await self._ensure_dl_channel()
            if ch is not None:
                await self._pin_edit(ch, await self._build_dl_embed())
        except Exception:
            log.exception("dl tracker refresh failed")

    @dltracker.before_loop
    async def _before_dl(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------- /film et /serie
    async def _search_raw(self, query):
        """Résultats bruts Seerr, ou None si l'APPEL A ÉCHOUÉ.

        ⚠️ None ≠ [] (invariant de core.http) : [] = « Seerr a répondu, rien à ce nom »,
        None = « Seerr injoignable ». Écraser l'un sur l'autre enchaînait les 12
        recherches relâchées de `_relax_variants` alors qu'AUCUNE ne pouvait aboutir —
        12 timeouts de 15 s de plus, ~3 min d'attente et un worker du pool partagé
        immobilisé — pour finir sur « vérifie l'orthographe » quand le service était
        simplement HS. C'est le défaut qui rendait `/langues` rassurant. 2026-08-11.
        """
        if self.seerr is None:
            return None
        d = await self.seerr.aget("/api/v1/search", {"query": query})
        if d is None:
            return None
        return (d.get("results") or []) if isinstance(d, dict) else []

    async def _search(self, query, kind):
        """Retourne (résultats classés par probabilité, exact?) — propose les plus
        probables même sans correspondance exacte (fautes de frappe / mots en trop tolérés)."""
        want = "movie" if kind == "film" else "tv"
        qn = _norm_titre(query)
        seen = {}

        def keep(results):
            for r in results or []:          # None = appel en échec, pas « rien trouvé »
                if r.get("mediaType") == want and r.get("id"):
                    seen.setdefault(r["id"], r)

        direct = await self._search_raw(query)
        keep(direct)
        # aucune correspondance directe -> on relâche la requête (fautes / à-peu-près),
        # mais SEULEMENT si Seerr a répondu (`direct` vaut [] et non None) : injoignable,
        # les 12 variantes échoueraient toutes de la même façon (cf. _search_raw).
        fuzzy = False
        if not seen and direct is not None:
            fuzzy = True
            for variant in _relax_variants(query):
                keep(await self._search_raw(variant))
                if len(seen) >= 6:      # assez de candidats, on arrête (latence)
                    break
        if not seen:
            return [], False
        ranked = sorted(
            seen.values(),
            key=lambda r: (round(_match_score(qn, r), 3), float(r.get("popularity") or 0)),
            reverse=True)
        best = _match_score(qn, ranked[0])
        if fuzzy:
            # via recherche relâchée : ne garder que ce qui ressemble vraiment (anti-bruit)
            if best < _PROBABLE_FLOOR:
                return [], False
            ranked = [r for r in ranked if _match_score(qn, r) >= _PROBABLE_FLOOR] or ranked[:5]
        return ranked[:15], best >= _EXACT_FLOOR

    # ⚠️ Ce cog N'A PLUS AUCUN chemin de création de demande dans Seerr : la méthode
    # `_request()` qui postait sur /api/v1/request avec la clé API admin — donc
    # auto-approuvée et téléchargée dans la foulée — a été SUPPRIMÉE (elle n'était plus
    # appelée depuis le 2026-08-11, où elle servait de repli quand le portail était
    # indisponible). Ne pas la réintroduire : le seul chemin légitime est le bouton
    # « Approuver » du portail #demandes (bot/cogs/requests.py), gardé par le tier M/O
    # + 2FA. Ici, on ne fait que PROPOSER (cf. RequestView._on_select).

    async def _flow(self, itx, kind, titre):
        await itx.response.defer(ephemeral=True)
        if self.seerr is None:
            await itx.followup.send("Seerr non configuré.", ephemeral=True)
            return
        res, exact = await self._search(titre, kind)
        noun = "film" if kind == "film" else "série"
        if not res:
            await itx.followup.send(
                f"Aucun {noun} trouvé pour « {titre} », même en approximatif. "
                f"Vérifie l'orthographe ou essaie le titre original.", ephemeral=True)
            return
        if exact:
            title = f"🔎 « {titre} »"
            desc = f"Choisis le {noun} à demander :"
        else:
            title = f"🔎 « {titre} » — suggestions"
            desc = (f"Aucun {noun} exactement à ce nom. Voici les plus probables "
                    f"(triés par ressemblance + popularité) :")
        emb = discord.Embed(title=title, description=desc, color=fmt.BLURPLE)
        # La vue mémorise l'interaction ET le message envoyé : sur un ÉPHÉMÈRE, ce sont
        # les deux seules routes pour griser le menu à l'expiration (Message.edit() est
        # inutilisable, cf. TorrentsView._repaint dans servarr.py).
        # ⚠️ `wait=True` est OBLIGATOIRE : sans lui `followup.send` renvoie None (webhook),
        # `bind(None)` ne mémorise rien et `on_timeout` retombe sur
        # `edit_original_response` de la commande — c'est à dire le message d'ATTENTE du
        # defer, pas le menu, qui resterait cliquable. 2026-08-11.
        view = RequestView(self, kind, res, itx)
        view.bind(await itx.followup.send(embed=emb, view=view, ephemeral=True, wait=True))

    @app_commands.command(description="Demander un film à télécharger (via Seerr).")
    @app_commands.describe(titre="Nom du film")
    @read_check()
    async def film(self, itx: discord.Interaction, titre: str):
        await self._flow(itx, "film", titre)

    @app_commands.command(description="Demander une série à télécharger (via Seerr).")
    @app_commands.describe(titre="Nom de la série")
    @read_check()
    async def serie(self, itx: discord.Interaction, titre: str):
        await self._flow(itx, "serie", titre)


class RequestView(GatedView):
    """Menu éphémère de /film et /serie.

    Porte : « read » (le tier de la commande, cf. read_check) + `gate_user_id` = celui
    qui a lancé la commande. Le message est éphémère, mais la porte déclarée rejoue
    aussi la session 2FA : un panneau resté ouvert après expiration ne doit plus agir.
    """

    gate = "read"

    def __init__(self, cog, kind, results, itx=None):
        super().__init__(timeout=120)
        self.cog = cog
        self.kind = kind
        self._itx = itx
        self._msg = None
        self.gate_user_id = itx.user.id if itx is not None else None
        results = results[:25]  # limite Discord d'un menu déroulant
        self.byid = {str(r["id"]): r for r in results}
        options = []
        for r in results:
            t = r.get("title") or r.get("name") or "?"
            yr = (r.get("releaseDate") or r.get("firstAirDate") or "")[:4]
            label = (f"{t} ({yr})" if yr else t)[:100]
            tag = _SEERR_STATUS.get(((r.get("mediaInfo") or {}) or {}).get("status"))
            ov = " ".join((r.get("overview") or "").split())
            sub = ((tag + " · ") if tag else "") + ov
            sub = sub[:100] if sub.strip() else None
            options.append(discord.SelectOption(label=label, value=str(r["id"]), description=sub))
        sel = discord.ui.Select(placeholder="Sélectionne un titre…", options=options)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _close(self, itx):
        """Grise le menu et arrête la vue.

        Sans ça le menu reste cliquable après la sélection (et après le timeout de 2 min) :
        Discord répond alors « Cette interaction a échoué », sans dire qu'il faut relancer
        la commande. Sur un ÉPHÉMÈRE, seul edit_original_response fonctionne. 2026-08-11.
        """
        for c in self.children:
            c.disabled = True
        try:
            await itx.edit_original_response(view=self)
        except discord.HTTPException:
            pass
        self.stop()

    def bind(self, msg):
        """Mémorise le message éphémère réellement envoyé (followup) : après un defer,
        `edit_original_response` vise le message d'attente, pas forcément celui qui porte
        le menu — on édite donc le message quand on l'a, l'interaction sinon."""
        self._msg = msg

    async def on_timeout(self):
        # Sans ça, passé 2 min la vue est retirée côté bot mais le menu reste cliquable
        # côté client : Discord répond « Cette interaction a échoué », sans explication.
        for c in self.children:
            c.disabled = True
        try:
            if self._msg is not None:
                await self._msg.edit(view=self)
            elif self._itx is not None:
                await self._itx.edit_original_response(view=self)
        except discord.HTTPException:
            pass

    async def _on_select(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        values = (itx.data or {}).get("values") or []
        r = self.byid.get(values[0]) if values else None
        if not r:
            await itx.followup.send("Sélection invalide.", ephemeral=True)
            await self._close(itx)
            return
        t = r.get("title") or r.get("name") or "?"
        # « déjà demandé / déjà disponible » : le dire tout de suite. Ce contrôle vivait
        # dans le repli 409 supprimé ci-dessous ; sans lui, #demandes se remplirait de
        # propositions inutiles. On laisse passer « en cours » et « partiel » : demander
        # les saisons manquantes d'une série est légitime, c'est l'admin qui tranche.
        status = ((r.get("mediaInfo") or {}) or {}).get("status")
        if status in (2, 5):
            await itx.followup.send(
                f"ℹ️ **{t}** est déjà demandé ou disponible ({_SEERR_STATUS[status]}).",
                ephemeral=True)
            await self._close(itx)
            return
        # passer par le PORTAIL de validation : poster une proposition dans #demandes
        # (rien n'est créé/téléchargé tant qu'un admin n'a pas approuvé).
        gate = self.cog.bot.get_cog("Requests")
        posted = False
        if gate is None:
            log.error("cog Requests indisponible — demande « %s » refusée (aucun repli)", t)
        else:
            try:
                posted = bool(await gate.propose(itx, self.kind, r))
            except Exception:
                log.exception("portail #demandes indisponible — « %s » NON demandé", t)
        if posted:
            await itx.followup.send(
                f"✅ **{t}** envoyé pour **validation** dans **#demandes**. "
                f"Une fois approuvé par un admin, suis l'avancement dans **#telechargements**.", ephemeral=True)
        else:
            # fail-CLOSED (2026-08-11) : l'ancien repli créait la demande DIRECTEMENT dans
            # Seerr avec la clé admin — donc auto-approuvée et téléchargée — dès que le
            # portail était indisponible (cog non chargé, salon absent, 500 transitoire).
            # C'était la seule barrière de validation du workflow : on refuse, on alerte.
            await itx.followup.send(
                f"⚠️ Portail de validation indisponible — **{t}** n'a PAS été demandé. "
                f"Réessaie plus tard ou préviens un admin.", ephemeral=True)
        await self._close(itx)


class DlRefreshView(GatedView):
    """Bouton du salon #telechargements : tier « mod » (= l'ancien admin_button_ok,
    rôle Gestion + 2FA), pas le rôle A « vision »."""

    gate = "mod"

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="medias:dl:refresh")
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        await itx.response.defer()
        try:
            emb = await self.cog._build_dl_embed()
            await itx.message.edit(embed=emb, view=self)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Medias(bot))
