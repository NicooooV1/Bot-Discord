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

from ..core.gates import GatedView

log = logging.getLogger("discord-bot.requests")

APIS_FILE = "/opt/discord-bot/servarr-apis.json"
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


def _load_apis():
    """Charge servarr-apis.json.

    Une lecture ratée désactive TOUT le portail (gatepoll n'est jamais démarré, /film
    n'a plus de barrière) : la cause doit apparaître dans les logs, sinon la panne est
    strictement muette et indiagnosticable (corrigé 2026-08-11).
    """
    try:
        with open(APIS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        log.warning("%s absent — portail de validation des demandes INACTIF", APIS_FILE)
    except PermissionError:
        log.error("%s illisible (droits du fichier) — portail de validation des "
                  "demandes INACTIF", APIS_FILE)
    except Exception:
        log.exception("%s illisible (JSON invalide ?) — portail de validation des "
                      "demandes INACTIF", APIS_FILE)
    return {}


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
        self.apis = _load_apis()
        # Sérialise les décisions : _decide lisait req_props/req_gate_msgs, puis attendait
        # jusqu'à 15 s la réponse de Seerr, puis réécrivait le dict lu AVANT l'attente.
        # Deux clics concurrents postaient donc deux fois et se ressuscitaient l'un
        # l'autre (et écrasaient les propositions ajoutées entre-temps). 2026-08-11.
        self._decide_lock = asyncio.Lock()
        self._props_purge_at = 0.0
        self._props_purge_off = 0

    async def cog_load(self):
        self.bot.add_view(GateView(self))          # boutons persistants au reboot
        if "seerr" in self.apis:
            self.gatepoll.start()

    def cog_unload(self):
        self.gatepoll.cancel()

    # ------------------------------------------------------------ HTTP (Seerr)
    def _get_sync(self, path):
        a = self.apis["seerr"]
        req = urllib.request.Request(a["url"] + path, headers={"X-Api-Key": a["key"]})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    async def _get(self, path):
        try:
            return await asyncio.to_thread(self._get_sync, path)
        except Exception as e:
            log.warning("GET %s: %s", path, e)
            return None

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
        a = self.apis["seerr"]
        data = json.dumps(body).encode() if body is not None else b""
        req = urllib.request.Request(a["url"] + path, data=data,
                                     headers={"X-Api-Key": a["key"], "Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
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
    def _lock_category_name(self):
        """Nom RÉEL de la catégorie verrouillée, comme provision.py la fabrique.

        Chercher « Lock » tout court ne trouvait jamais « 🔒 Lock R820 » : le cog créait
        alors une SECONDE catégorie, sans overwrites — donc visible de @everyone — et y
        posait #demandes (titres demandés + pseudo du demandeur). 2026-08-11.
        """
        return f"🔒 Lock {getattr(self.bot.cfg, 'server_key', 'R820')}"

    def _fallback_overwrites(self, guild):
        """Overwrites minimaux si le cog Provision est absent : @everyone ne voit rien,
        le bot et les rôles M/O du nœud voient. Une catégorie « nue » est PUBLIQUE."""
        cfg = self.bot.cfg
        ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        me = guild.me
        if me is not None:
            ow[me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True,
                manage_messages=True, manage_channels=True, read_message_history=True)
        for rid in (getattr(cfg, "node_mod_role_id", 0), getattr(cfg, "node_owner_role_id", 0)):
            r = guild.get_role(rid) if rid else None
            if r is not None:
                ow[r] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    use_application_commands=True)
        return ow

    async def _lock_category(self, guild):
        """Catégorie Lock de PROVISION (jamais un doublon nu, qui serait public).

        Ordre : id publié par le cog Provision -> id mémorisé dans l'état -> nom réel.
        En dernier recours seulement, on la crée — avec ses overwrites.
        """
        prov = self.bot.get_cog("Provision")
        pid = ((getattr(prov, "prov", None) or {}).get("categories") or {}).get("lock")
        cat = guild.get_channel(pid) if pid else None
        if not isinstance(cat, discord.CategoryChannel):
            cid = self.bot.state.get("lock_category_id")
            cat = guild.get_channel(cid) if cid else None
        if not isinstance(cat, discord.CategoryChannel):
            cat = discord.utils.get(guild.categories, name=self._lock_category_name())
        if cat is None:
            ow = None
            if prov is not None:
                try:
                    ow = prov._lock_overwrites(guild)     # source de vérité (rôles M/O)
                except Exception:
                    log.exception("overwrites Lock via Provision — repli local")
            try:
                cat = await guild.create_category(
                    self._lock_category_name(),
                    overwrites=ow or self._fallback_overwrites(guild),
                    reason="portail demandes")
                log.info("catégorie %s créée (overwrites posés)", cat.name)
            except discord.HTTPException:
                log.exception("création de la catégorie %s — portail #%s indisponible",
                              self._lock_category_name(), GATE_CHANNEL)
                return None
        if self.bot.state.get("lock_category_id") != cat.id:
            self.bot.state.set("lock_category_id", cat.id)
        return cat

    async def _channel(self):
        gid = getattr(self.bot.cfg, "guild_id", None)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            log.warning("guild %s introuvable — portail #%s indisponible", gid, GATE_CHANNEL)
            return None
        st = self.bot.state.get("req_gate") or {}
        ch = guild.get_channel(st.get("channel")) if st.get("channel") else None
        if ch is not None:
            return ch
        cat = await self._lock_category(guild)
        if cat is None:
            return None
        ch = discord.utils.get(cat.text_channels, name=GATE_CHANNEL)
        if ch is None:
            # Un homonyme AILLEURS dans le serveur était adopté tel quel, donc laissé
            # public : on le rapatrie dans la catégorie Lock et on resynchronise ses
            # permissions plutôt que d'y publier qui demande quoi. 2026-08-11.
            stray = discord.utils.get(guild.text_channels, name=GATE_CHANNEL)
            if stray is not None:
                try:
                    await stray.edit(category=cat, sync_permissions=True,
                                     reason="#demandes doit vivre dans la catégorie Lock")
                    ch = stray
                    log.warning("#%s trouvé hors de « %s » — rapatrié, permissions "
                                "resynchronisées", GATE_CHANNEL, cat.name)
                except discord.HTTPException:
                    log.exception("#%s hors de la catégorie Lock et non déplaçable — "
                                  "portail indisponible (rien ne sera posté en public)",
                                  GATE_CHANNEL)
                    return None
        if ch is None:
            try:
                ch = await guild.create_text_channel(
                    GATE_CHANNEL, category=cat,
                    topic="Validation des demandes Seerr — Approuver / Refuser (admins)")
                log.info("salon #%s créé dans « %s »", GATE_CHANNEL, cat.name)
            except discord.HTTPException:
                log.exception("création du salon #%s", GATE_CHANNEL)
                return None
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
                e.set_footer(text=f"✅ approuvé par {itx.user.display_name} — téléchargement lancé")
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
