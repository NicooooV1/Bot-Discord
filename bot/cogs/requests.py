"""Portail de validation des demandes Seerr.

Toute demande Seerr en attente (PENDING) est postée dans le salon #demandes
(catégorie « 🔒 Lock <SERVER_KEY> », celle de provision.py) avec des boutons
Approuver / Refuser réservés aux tiers M/O du nœud. Le clic appelle l'API Seerr
(/approve ou /decline) — rien n'est téléchargé sans validation ici.
Réconcilie aussi les demandes traitées ailleurs (Seerr UI).

⚠️ INVARIANT (rétabli le 2026-08-11) : « rien n'est téléchargé sans validation »
n'était vrai que tant que tout allait bien. `propose()` renvoyait False aussi bien
pour « refusé » que pour « panne », et l'appelant (/film, /serie) retombait alors sur
une création DIRECTE dans Seerr avec la clé admin — donc auto-approuvée et téléchargée.
`propose()` lève désormais PortalUnavailable : l'appelant doit refuser, jamais créer.

Nécessite l'accès réseau bot->CT120 (firewall Proxmox CT106 déjà ouvert).
Config /opt/discord-bot/servarr-apis.json (clé "seerr").
"""
import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.request

import discord
from discord.ext import commands, tasks

from ..core import channels
from ..core.gates import GatedView
from ..core.http import client_for, load_service_apis

log = logging.getLogger("discord-bot.requests")

GATE_CHANNEL = "demandes"
POLL_SECONDS = 30
TMDB_IMG = "https://image.tmdb.org/t/p/w342"
_FOOTER_RE = re.compile(r"req:(\d+)")
# Purge des propositions orphelines : 1 lot par heure, quelques messages à la fois —
# une boucle de 30 s qui vérifierait TOUTE la file serait un marteau à ratelimit.
PROPS_PURGE_SECONDS = 3600
PROPS_PURGE_BATCH = 10
# Statut Seerr d'une demande : 1 = en attente. Tout le reste = déjà tranchée.
SEERR_PENDING = 1


class PortalUnavailable(RuntimeError):
    """Le portail #demandes n'a PAS pu recevoir la proposition.

    Levée par `propose()` plutôt qu'un `False` indiscernable : l'appelant doit répondre
    « portail indisponible » et surtout PAS créer la demande directement dans Seerr
    (fail-open sur la seule barrière de validation — corrigé 2026-08-11).
    """


class GateView(GatedView):
    """Vue persistante (custom_id statiques) ; l'id de la demande vit dans le footer.

    Porte : tier « owner » = rôle M ou O du nœud (ou propriétaire) + session 2FA, c'est
    à dire EXACTEMENT ce que faisait l'appel à `lock_button_ok` recopié en tête de
    `_decide` — #demandes vit dans la catégorie Lock, ses boutons ont la même exigence
    que les autres boutons de cette catégorie. La porte est désormais déclarée
    (core/gates.py) au lieu d'être réimplémentée. 2026-08-11.
    """

    gate = "owner"
    gate_cap = "requests"

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Approuver", style=discord.ButtonStyle.success,
                       emoji="✅", custom_id="reqgate:approve")
    async def approve(self, itx: discord.Interaction, _btn):
        await self.cog._decide(itx, "approve")

    @discord.ui.button(label="Refuser", style=discord.ButtonStyle.danger,
                       emoji="✖️", custom_id="reqgate:decline")
    async def decline(self, itx: discord.Interaction, _btn):
        await self.cog._decide(itx, "decline")


class Requests(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # None = Seerr non configuré. Sans lui, le portail reste INACTIF (gatepoll jamais
        # démarré) : `core.http.load_service_apis` journalise la cause, sinon la panne est
        # muette et indiagnosticable. 2026-08-11.
        # Ce cog n'appelle QUE Seerr : pas de `self.apis` à conserver (contrairement à
        # medias/servarr, qui relisent l'entrée « qbit »).
        self.seerr = client_for(load_service_apis(), "seerr")
        # Sérialise les décisions : _decide lisait req_props/req_gate_msgs, puis attendait
        # jusqu'à 15 s la réponse de Seerr, puis réécrivait le dict lu AVANT l'attente.
        # Deux clics concurrents postaient donc deux fois et se ressuscitaient l'un
        # l'autre (et écrasaient les propositions ajoutées entre-temps). 2026-08-11.
        self._decide_lock = asyncio.Lock()
        self._props_purge_at = 0.0
        self._props_purge_off = 0

    async def cog_load(self):
        self.bot.add_view(GateView(self))          # boutons persistants au reboot
        if self.seerr is not None:
            self.gatepoll.start()

    def cog_unload(self):
        self.gatepoll.cancel()

    # ------------------------------------------------------------ HTTP (Seerr)
    # `self.seerr` (core.http.ApiClient) porte l'URL, la clé et le TIMEOUT : plus de bloc
    # urllib recopié pour les lectures. Restent deux appels construits à la main, car le
    # client partagé rend `None` pour TOUTE erreur et perd justement ce dont ce cog vit :
    #   - `_post` a besoin du CODE HTTP (409 « déjà demandé » se classe, 401/429/408 se
    #     rejouent — cf. `_decide`) ;
    #   - `_get_sync` a besoin de laisser remonter le 404 (demande supprimée depuis l'UI
    #     Seerr, cas COURANT) pour ne pas le confondre avec une panne réseau.
    # Ils prennent quand même url/headers/timeout de `self.seerr` : une seule source pour
    # les identifiants.
    def _get_sync(self, path):
        req = urllib.request.Request(self.seerr.url(path), headers=self.seerr.headers)
        with urllib.request.urlopen(req, timeout=self.seerr.timeout) as r:
            return json.loads(r.read())

    async def _get(self, path):
        if self.seerr is None:
            return None
        return await self.seerr.aget(path)

    async def _request_state(self, rid):
        """État d'UNE demande Seerr -> ("supprimee"|"connue"|"inconnue", statut).

        `_get` renvoie None aussi bien pour « Seerr injoignable » que pour un 404. Or
        supprimer une demande depuis l'UI Seerr est une opération COURANTE : confondre
        les deux laissait le message « en attente » (et son entrée `req_gate_msgs`) à vie
        dans #demandes avec des boutons qui ne pouvaient plus qu'échouer, tout en
        reloguant un GET 404 toutes les 30 s. 2026-08-11.
        """
        try:
            det = await asyncio.to_thread(self._get_sync, f"/api/v1/request/{rid}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "supprimee", None
            log.warning("GET /api/v1/request/%s: %s", rid, e)
            return "inconnue", None
        except Exception as e:
            log.warning("GET /api/v1/request/%s: %s", rid, e)
            return "inconnue", None
        if not isinstance(det, dict):
            return "inconnue", None
        return "connue", det.get("status")

    def _post_sync(self, path, body):
        data = json.dumps(body).encode() if body is not None else b""
        req = urllib.request.Request(
            self.seerr.url(path), data=data, method="POST",
            headers={**self.seerr.headers, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.seerr.timeout) as r:
            return r.status
    async def _post(self, path, body=None):
        try:
            return await asyncio.to_thread(self._post_sync, path, body)
        except urllib.error.HTTPError as e:
            return e.code
        except Exception as e:
            log.warning("POST %s: %s", path, e)
            return 0

    # ------------------------------------------------------------ état partagé
    # ⚠️ Toujours RELIRE l'état juste avant de l'écrire : réécrire un dictionnaire lu
    # avant un `await` ressuscite les entrées ajoutées entre-temps (une proposition
    # postée par /film pendant qu'une approbation est en vol). 2026-08-11.
    def _props_pop(self, pid):
        props = dict(self.bot.state.get("req_props") or {})
        p = props.pop(pid, None)
        if p is not None:
            self.bot.state.set("req_props", props)
        return p

    def _props_put(self, pid, p):
        props = dict(self.bot.state.get("req_props") or {})
        props[pid] = p
        self.bot.state.set("req_props", props)

    def _gate_msgs_update(self, added=None, removed=()):
        posted = dict(self.bot.state.get("req_gate_msgs") or {})
        posted.update(added or {})
        for rid in removed:
            posted.pop(rid, None)
        self.bot.state.set("req_gate_msgs", posted)
        return posted

    # ------------------------------------------------------------ channel
    async def _channel(self):
        """Salon #demandes, DANS la catégorie Lock provisionnée — ou rien du tout.

        La catégorie est résolue par `core.channels` (id publié par provision, puis nom
        réel « 🔒 Lock <SERVER_KEY> »). Ce cog ne CRÉE plus de catégorie : chercher
        « Lock » tout court ne trouvait jamais « 🔒 Lock R820 », une SECONDE catégorie
        naissait — sans overwrites, donc visible de @everyone — et #demandes y publiait
        les titres demandés ET le pseudo du demandeur.

        Pas de catégorie provisionnée -> pas de salon -> portail indisponible :
        `propose()` lève PortalUnavailable et /film REFUSE (fail-closed). L'état se
        répare tout seul : provision recrée la CATÉGORIE (il ne connaît pas #demandes,
        qui n'appartient qu'à ce cog), et le tour suivant de `gatepoll` (30 s) repose le
        salon dedans. 2026-08-11.
        """
        gid = getattr(self.bot.cfg, "guild_id", None)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            log.warning("guild %s introuvable — portail #%s indisponible", gid, GATE_CHANNEL)
            return None
        st = self.bot.state.get("req_gate") or {}
        cat = channels.lock_category(self.bot, guild)
        ch = guild.get_channel(st.get("channel")) if st.get("channel") else None
        if ch is None:
            ch = await channels.ensure_channel(
                self.bot, guild, GATE_CHANNEL, cat,
                topic="Validation des demandes Seerr — Approuver / Refuser (admins)")
            if ch is None:
                log.warning("salon #%s indisponible — portail de validation hors service",
                            GATE_CHANNEL)
                return None
        if channels.is_public(ch) is True:
            # Homonyme resté hors de la catégorie Lock : on le remet sous clé. Si le
            # déplacement échoue, on refuse de publier qui demande quoi devant tout le
            # serveur — portail déclaré indisponible, donc /film refuse.
            if not await channels.seal_if_public(
                    self.bot, ch, cat, why="titres demandés + pseudo du demandeur"):
                log.error("#%s reste lisible de @everyone — portail indisponible "
                          "(rien ne sera posté en public)", GATE_CHANNEL)
                return None
        if st.get("channel") != ch.id:
            st = dict(self.bot.state.get("req_gate") or {})
            st["channel"] = ch.id
            self.bot.state.set("req_gate", st)
        return ch

    # ------------------------------------------------------------ embed
    async def _details(self, req):
        media = req.get("media") or {}
        tmdb = media.get("tmdbId")
        is_tv = req.get("type") == "tv"
        d = await self._get(f"/api/v1/{'tv' if is_tv else 'movie'}/{tmdb}") if tmdb else None
        title = (d or {}).get("title") or (d or {}).get("name") or f"tmdb {tmdb}"
        year = ((d or {}).get("releaseDate") or (d or {}).get("firstAirDate") or "")[:4]
        poster = (d or {}).get("posterPath")
        ov = " ".join(((d or {}).get("overview") or "").split())
        return title, year, poster, ov

    async def _embed(self, req, rid, title, year, poster, ov):
        who = (req.get("requestedBy") or {})
        name = who.get("jellyfinUsername") or who.get("displayName") or who.get("username") or "?"
        is_tv = req.get("type") == "tv"
        emb = discord.Embed(
            title=f"{'📺' if is_tv else '🎬'} {title}" + (f" ({year})" if year else ""),
            description=(ov[:350] + "…") if len(ov) > 350 else ov,
            color=0xE5A50A)  # ambre = en attente
        emb.add_field(name="Type", value="Série" if is_tv else "Film")
        emb.add_field(name="Demandé par", value=name)
        if req.get("is4k"):
            emb.add_field(name="Qualité", value="4K")
        if poster:
            emb.set_thumbnail(url=TMDB_IMG + poster)
        # rid vient de l'appelant : un payload Seerr sans « id » ne doit pas tuer la boucle
        emb.set_footer(text=f"req:{rid} · en attente de validation")
        return emb

    # ------------------------------------------------------------ bot proposal
    async def propose(self, itx, kind, result):
        """Appelé par /film /serie : poste une PROPOSITION dans #demandes (rien n'est
        créé dans Seerr tant que ce n'est pas approuvé ici). Renvoie True si posté.

        ⚠️ Lève PortalUnavailable si la proposition n'a PAS pu être postée. L'appelant
        NE DOIT PAS retomber sur une création directe : ce repli « historique »
        contournait la seule barrière de validation du workflow. 2026-08-11.
        """
        ch = await self._channel()
        if ch is None:
            raise PortalUnavailable(f"salon #{GATE_CHANNEL} indisponible")
        tmdb = result["id"]
        title = result.get("title") or result.get("name") or f"tmdb {tmdb}"
        year = (result.get("releaseDate") or result.get("firstAirDate") or "")[:4]
        poster = result.get("posterPath")
        ov = " ".join((result.get("overview") or "").split())
        if kind == "serie":
            det = await self._get(f"/api/v1/tv/{tmdb}")
            seasons = [s["seasonNumber"] for s in (det or {}).get("seasons", [])
                       if s.get("seasonNumber", 0) > 0] or "all"
            body = {"mediaType": "tv", "mediaId": int(tmdb), "seasons": seasons}
        else:
            body = {"mediaType": "movie", "mediaId": int(tmdb)}
        who = itx.user.display_name
        emb = discord.Embed(
            title=f"{'📺' if kind == 'serie' else '🎬'} {title}" + (f" ({year})" if year else ""),
            description=(ov[:350] + "…") if len(ov) > 350 else ov, color=0xE5A50A)
        emb.add_field(name="Type", value="Série" if kind == "serie" else "Film")
        emb.add_field(name="Demandé par", value=who)
        if poster:
            emb.set_thumbnail(url=TMDB_IMG + poster)
        emb.set_footer(text="prop · en attente de validation")
        try:
            msg = await ch.send(embed=emb, view=GateView(self))
        except discord.HTTPException as e:
            log.exception("proposition « %s » non postée dans #%s", title, GATE_CHANNEL)
            raise PortalUnavailable(str(e)) from e
        # ts : horodatage informatif (diagnostic d'une file qui stagne). La purge, elle,
        # se fonde sur l'EXISTENCE du message Discord, pas sur l'ancienneté — expirer une
        # proposition par le temps changerait la sémantique de la file (cf. _purge_props).
        self._props_put(str(msg.id), {"body": body, "who": who, "title": title,
                                      "ts": int(time.time())})
        e = msg.embeds[0]; e.set_footer(text=f"prop:{msg.id} · en attente de validation")
        try:
            await msg.edit(embed=e)
        except discord.HTTPException:
            log.warning("proposition %s : footer « prop:<id> » non posé — le clic "
                        "répondra « pas encore enregistrée »", msg.id)
        return True

    # ------------------------------------------------------------ poll loop
    @tasks.loop(seconds=POLL_SECONDS)
    async def gatepoll(self):
        ch = await self._channel()
        if ch is None:
            return
        data = await self._get("/api/v1/request?take=40&filter=pending&sort=added")
        if not isinstance(data, dict):
            return
        pages = ((data.get("pageInfo") or {}).get("pages") or 1)
        if pages > 1:
            # On ne lit qu'UNE page : sans ce log la troncature est invisible (des
            # demandes ne sont jamais postées tant qu'elles restent hors fenêtre).
            log.warning("Seerr : %s pages de demandes en attente, seule la 1re est lue", pages)
        pending = {}
        for r in (data.get("results") or []):
            rid = r.get("id")
            if rid is None:
                continue          # payload partiel : ne pas tuer la boucle sur un KeyError
            pending[str(rid)] = r
        posted = dict(self.bot.state.get("req_gate_msgs") or {})  # reqId -> messageId
        added, removed = {}, []

        # 1) new pending -> post
        for rid, req in pending.items():
            if rid in posted:
                continue
            title, year, poster, ov = await self._details(req)
            emb = await self._embed(req, rid, title, year, poster, ov)
            try:
                msg = await ch.send(embed=emb, view=GateView(self))
                added[rid] = msg.id
                log.info("demande %s postee dans #demandes", rid)
            except discord.HTTPException as e:
                log.warning("post demande %s: %s", rid, e)

        # 2) posted but no longer pending (résolu ailleurs) -> reconcile
        for rid in list(posted):
            if rid in pending:
                continue
            # ⚠️ take=40 ne renvoie qu'UNE page : l'absence de la demande dans cette page
            # ne prouve PAS qu'elle est traitée. On demande son statut à Seerr, et en cas
            # de doute (Seerr injoignable) on ne touche à rien — sinon une coupure
            # réseau repeindrait tout le salon en « traité ». 2026-08-11.
            etat, statut = await self._request_state(rid)
            if etat == "inconnue" or (etat == "connue" and statut in (None, SEERR_PENDING)):
                continue
            # 404 = supprimée dans l'UI Seerr : elle ne reviendra jamais dans `pending`,
            # il FAUT la classer ici sinon son message reste « en attente » à vie.
            note = ("demande supprimée dans Seerr" if etat == "supprimee"
                    else "traité directement dans Seerr")
            try:
                msg = await ch.fetch_message(posted[rid])
                if msg.embeds and (msg.embeds[0].footer and "en attente" in (msg.embeds[0].footer.text or "")):
                    e = msg.embeds[0]; e.color = 0x9AA0A6
                    e.set_footer(text=f"req:{rid} · {note}")
                    await msg.edit(embed=e, view=None)
            except discord.NotFound:
                pass
            except discord.HTTPException:
                continue
            removed.append(rid)

        if added or removed:
            # relecture sous verrou : _decide a pu retirer une entrée pendant les awaits
            # ci-dessus, réécrire le snapshot la ressusciterait.
            async with self._decide_lock:
                self._gate_msgs_update(added, removed)
        await self._purge_props(ch)

    async def _purge_props(self, ch):
        """Retire les propositions dont le message n'existe plus dans #demandes.

        Sans ça `req_props` ne décroît JAMAIS : seul un clic Approuver/Refuser en retire
        une entrée, et une proposition dont le message a été supprimé à la main y reste à
        vie. Vérification lente et par lots (1 passage par heure) : vérifier toute la file
        toutes les 30 s serait un marteau à ratelimit. Pas d'expiration par ANCIENNETÉ :
        ce serait un changement de sémantique de la file de validation (décision de
        Nico), pas une correction de bug. 2026-08-11.
        """
        now = time.time()
        if now - self._props_purge_at < PROPS_PURGE_SECONDS:
            return
        self._props_purge_at = now
        keys = list(self.bot.state.get("req_props") or {})
        if not keys:
            return
        off = self._props_purge_off if self._props_purge_off < len(keys) else 0
        batch = keys[off:off + PROPS_PURGE_BATCH]
        self._props_purge_off = 0 if off + PROPS_PURGE_BATCH >= len(keys) else off + PROPS_PURGE_BATCH
        gone = []
        for pid in batch:
            try:
                await ch.fetch_message(int(pid))
            except discord.NotFound:
                gone.append(pid)
            except (discord.HTTPException, ValueError):
                continue
        if gone:
            async with self._decide_lock:
                props = dict(self.bot.state.get("req_props") or {})
                for pid in gone:
                    props.pop(pid, None)
                self.bot.state.set("req_props", props)
            log.info("req_props : %d proposition(s) orpheline(s) purgée(s)", len(gone))

    @gatepoll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ decision
    async def _decide(self, itx: discord.Interaction, action):
        # La porte (tier M/O du nœud + session 2FA, comme les autres boutons de la
        # catégorie Lock) est DÉCLARÉE par GateView.gate = "owner" — core/gates.py
        # l'applique avant d'entrer ici, plus besoin de la recopier. 2026-08-11.
        msg = itx.message
        if msg is None or not msg.embeds:
            # Répondre AVANT le defer : l'ancien `embeds[0]` sans garde levait une
            # IndexError juste après le defer, et le clic restait sans AUCUNE réponse
            # (un defer de composant n'affiche même pas « échec »). 2026-08-11.
            await itx.response.send_message(
                "Message illisible (embed absent) — impossible d'identifier la demande.",
                ephemeral=True)
            return
        foot = (msg.embeds[0].footer.text or "")
        mreq = re.search(r"req:(\d+)", foot)
        mprop = re.search(r"prop:(\d+)", foot)
        if not mreq and not mprop:
            # Footer sans identifiant : fenêtre entre l'envoi de la proposition et son
            # ré-étiquetage « prop:<id> » (ou échec de cette édition).
            await itx.response.send_message(
                "Proposition pas encore enregistrée — réessaie dans quelques secondes.",
                ephemeral=True)
            return
        await itx.response.defer()
        async with self._decide_lock:
            e = msg.embeds[0]
            if mreq:                                # demande Seerr existante (UI) -> approve/decline API
                rid = mreq.group(1)
                st = await self._post(f"/api/v1/request/{rid}/{action}")
                if st not in (200, 201):
                    await itx.followup.send(f"⚠️ Échec Seerr (code {st}).", ephemeral=True); return
                self._gate_msgs_update(removed=[rid])
            else:                                   # proposition du bot -> créer la demande SEULEMENT si approuvée
                pid = mprop.group(1)
                if action == "approve":
                    # Retrait PERSISTÉ avant le POST (jusqu'à 15 s) : sinon un second clic
                    # lisait la même entrée et postait une seconde fois, puis la
                    # réinjectait sur le 409. 2026-08-11.
                    p = self._props_pop(pid)
                    if not p:
                        await itx.followup.send(
                            "Proposition introuvable (déjà traitée, ou état perdu) — "
                            "relance /film si besoin.", ephemeral=True); return
                    st = await self._post("/api/v1/request", p["body"])   # crée -> télécharge
                    if st == 409:
                        # Seerr connaît déjà ce média (demandé entre-temps, ou déjà
                        # disponible) : la proposition n'a plus d'objet. On la CLASSE au
                        # lieu d'afficher « échec » sur une approbation qui n'avait rien
                        # à créer — et on retire les boutons. 2026-08-11.
                        e.color = 0x9AA0A6
                        e.set_footer(text=f"ℹ️ déjà demandé ou disponible dans Seerr — "
                                          f"classé par {itx.user.display_name}")
                        try:
                            await msg.edit(embed=e, view=None)
                        except discord.HTTPException:
                            pass
                        await itx.followup.send(
                            "ℹ️ Déjà demandé ou disponible dans Seerr — proposition "
                            "classée, rien de nouveau n'a été créé.", ephemeral=True)
                        return
                    if st not in (200, 201):
                        # Le 409 (déjà demandé) est traité au-dessus et ne se rejoue
                        # jamais ; TOUT le reste est réessayable et doit revenir dans la
                        # file. Ne réinjecter que sur 0/5xx perdait DÉFINITIVEMENT la
                        # proposition sur un 401/403 (clé Seerr tournée), un 429 ou un
                        # 408 : le message restait « en attente » dans #demandes mais
                        # l'état ne le connaissait plus. 2026-08-11.
                        self._props_put(pid, p)
                        await itx.followup.send(f"⚠️ Échec création Seerr (code {st}).",
                                                ephemeral=True); return
                else:
                    self._props_pop(pid)
            if action == "approve":
                e.color = 0x2ECC71
                # 2026-08-20 : seul le 200 Seerr est vérifié ici — Radarr/Sonarr peuvent
                # ne trouver aucune release, donc ne pas affirmer « téléchargement lancé ».
                e.set_footer(text=f"✅ approuvé par {itx.user.display_name} — transmis à "
                                  f"Seerr (téléchargement dès qu'une release est trouvée)")
            else:
                e.color = 0xE74C3C
                e.set_footer(text=f"✖️ refusé par {itx.user.display_name}")
            try:
                await msg.edit(embed=e, view=None)
            except discord.HTTPException:
                log.warning("décision %s : boutons non retirés du message %s "
                            "(ils resteront cliquables)", action, msg.id)


async def setup(bot):
    await bot.add_cog(Requests(bot))
