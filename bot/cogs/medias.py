"""Suivi des téléchargements en direct + demandes /film /serie via Seerr.

Nécessite l'accès réseau bot->CT120 (ouvert dans le firewall Proxmox de CT106).
Config /opt/discord-bot/servarr-apis.json (radarr/sonarr/seerr/qbit).
"""
import asyncio
import difflib
import json
import logging
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.permissions import admin_button_ok, read_check

log = logging.getLogger("discord-bot.medias")

APIS_FILE = "/opt/discord-bot/servarr-apis.json"
SUPERVISION_CATEGORY = "📊 Supervision"
DL_CHANNEL = "telechargements"
DL_STATES = ("downloading", "stalledDL", "metaDL", "forcedDL",
             "checkingDL", "allocating", "queuedDL")


def _load_apis():
    try:
        with open(APIS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


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


def _norm(s):
    """minuscule, sans accents ni ponctuation, espaces compactés — pour comparer les titres."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def _match_score(query_norm, r):
    """Ressemblance [0..1] entre la recherche et un résultat (titre localisé OU original)."""
    tn = _norm(r.get("title") or r.get("name") or "")
    on = _norm(r.get("originalTitle") or r.get("originalName") or "")
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
        self.apis = _load_apis()
        if self.apis:
            self.dltracker.start()

    async def cog_load(self):
        self.bot.add_view(DlRefreshView(self))

    def cog_unload(self):
        if self.apis:
            self.dltracker.cancel()

    # ------------------------------------------------------------- HTTP helpers
    def _get_sync(self, app, path):
        a = self.apis[app]
        req = urllib.request.Request(a["url"] + path, headers={"X-Api-Key": a["key"]})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    async def _get(self, app, path):
        try:
            return await asyncio.to_thread(self._get_sync, app, path)
        except Exception as e:
            log.warning("GET %s%s: %s", app, path, e)
            return None

    def _post_sync(self, app, path, body):
        a = self.apis[app]
        req = urllib.request.Request(a["url"] + path, data=json.dumps(body).encode(),
                                     headers={"X-Api-Key": a["key"], "Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})

    async def _post(self, app, path, body):
        try:
            return await asyncio.to_thread(self._post_sync, app, path, body)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:250]
        except Exception as e:
            return 0, str(e)

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

    # -------------------------------------------------- salon #telechargements
    async def _ensure_dl_channel(self):
        gid = getattr(self.bot.cfg, "guild_id", None)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            return None
        info = self.bot.state.get("media_dl") or {}
        ch = guild.get_channel(info["channel"]) if info.get("channel") else None
        if ch is not None:
            return ch
        cat = discord.utils.get(guild.categories, name=SUPERVISION_CATEGORY)
        ch = discord.utils.get(guild.text_channels, name=DL_CHANNEL)
        if ch is None:
            try:
                ch = await guild.create_text_channel(
                    DL_CHANNEL, category=cat,
                    topic="Téléchargements en cours (barre de progression, mise à jour ~30 s)")
                log.info("salon #%s créé", DL_CHANNEL)
            except discord.HTTPException:
                return None
        cur = self.bot.state.get("media_dl") or {}
        cur["channel"] = ch.id
        self.bot.state.set("media_dl", cur)
        return ch

    async def _pin_edit(self, ch, embed):
        info = self.bot.state.get("media_dl") or {}
        mid = info.get("message")
        msg = None
        if mid:
            try:
                msg = await ch.fetch_message(mid)
            except discord.NotFound:
                msg = None
            except discord.HTTPException:
                return
        view = DlRefreshView(self)
        try:
            if msg is None:
                msg = await ch.send(embed=embed, view=view)
                try:
                    await msg.pin()
                except discord.HTTPException:
                    pass
                info["message"] = msg.id
                self.bot.state.set("media_dl", info)
            else:
                await msg.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass

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
                lines.append(
                    f"`{_bar(pct)}` **{pct:4.1f} %** · {fmt.humanize_bytes(done)} / {fmt.humanize_bytes(size)}"
                    f" · {str(t.get('name','?'))[:42]}\n"
                    f"　↓ {fmt.humanize_rate(t.get('dlspeed', 0))} · ETA {_eta(t.get('eta', 0))}"
                    f" · reste {fmt.humanize_bytes(left)}")
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
        d = await self._get("seerr", "/api/v1/search?query=" + urllib.parse.quote(query))
        return (d.get("results", []) if d else []) or []

    async def _search(self, query, kind):
        """Retourne (résultats classés par probabilité, exact?) — propose les plus
        probables même sans correspondance exacte (fautes de frappe / mots en trop tolérés)."""
        want = "movie" if kind == "film" else "tv"
        qn = _norm(query)
        seen = {}

        def keep(results):
            for r in results:
                if r.get("mediaType") == want and r.get("id"):
                    seen.setdefault(r["id"], r)

        keep(await self._search_raw(query))
        # aucune correspondance directe -> on relâche la requête (fautes / à-peu-près)
        fuzzy = False
        if not seen:
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

    async def _request(self, kind, tmdb):
        if kind == "film":
            body = {"mediaType": "movie", "mediaId": int(tmdb)}
        else:
            det = await self._get("seerr", f"/api/v1/tv/{tmdb}")
            if det is None:
                # échec du fetch détail : on demande TOUTE la série ("all" accepté par
                # Overseerr/Jellyseerr) plutôt que de retomber par erreur sur la saison 1.
                seasons = "all"
            else:
                seasons = [s["seasonNumber"] for s in det.get("seasons", [])
                           if s.get("seasonNumber", 0) > 0] or [1]
            body = {"mediaType": "tv", "mediaId": int(tmdb), "seasons": seasons}
        return await self._post("seerr", "/api/v1/request", body)

    async def _flow(self, itx, kind, titre):
        await itx.response.defer(ephemeral=True)
        if "seerr" not in self.apis:
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
        await itx.followup.send(embed=emb, view=RequestView(self, kind, res), ephemeral=True)

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


class RequestView(discord.ui.View):
    def __init__(self, cog, kind, results):
        super().__init__(timeout=120)
        self.cog = cog
        self.kind = kind
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

    async def _on_select(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        r = self.byid.get(itx.data["values"][0])
        if not r:
            await itx.followup.send("Sélection invalide.", ephemeral=True)
            return
        t = r.get("title") or r.get("name") or "?"
        # passer par le PORTAIL de validation : poster une proposition dans #demandes
        # (rien n'est créé/téléchargé tant qu'un admin n'a pas approuvé).
        gate = self.cog.bot.get_cog("Requests")
        posted = False
        if gate is not None:
            try:
                posted = await gate.propose(itx, self.kind, r)
            except Exception as e:
                log.warning("propose %s: %s", t, e); posted = False
        if posted:
            await itx.followup.send(
                f"✅ **{t}** envoyé pour **validation** dans **#demandes**. "
                f"Une fois approuvé par un admin, suis l'avancement dans **#telechargements**.", ephemeral=True)
        else:
            # repli : portail indisponible -> création directe (comportement historique)
            st, data = await self.cog._request(self.kind, r["id"])
            if st in (200, 201):
                await itx.followup.send(f"✅ Demande créée : **{t}** (portail indispo).", ephemeral=True)
            elif st == 409 or (isinstance(data, str) and "exist" in data.lower()):
                await itx.followup.send(f"ℹ️ **{t}** est déjà demandé ou disponible.", ephemeral=True)
            else:
                await itx.followup.send(f"⚠️ Échec de la demande pour **{t}** (code {st}).", ephemeral=True)
        self.stop()


class DlRefreshView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="medias:dl:refresh")
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        if not await admin_button_ok(itx):     # rôle Gestion + 2FA (pas le rôle A « vision »)
            return
        await itx.response.defer()
        try:
            emb = await self.cog._build_dl_embed()
            await itx.message.edit(embed=emb, view=self)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Medias(bot))
