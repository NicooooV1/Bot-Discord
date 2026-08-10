"""Portail de validation des demandes Seerr.

Toute demande Seerr en attente (PENDING) est postée dans le salon #demandes
(catégorie « Lock » 🔒) avec des boutons Approuver / Refuser réservés aux admins.
Le clic appelle l'API Seerr (/approve ou /decline) — rien n'est téléchargé sans
validation ici. Réconcilie aussi les demandes traitées ailleurs (Seerr UI).

Nécessite l'accès réseau bot->CT120 (firewall Proxmox CT106 déjà ouvert).
Config /opt/discord-bot/servarr-apis.json (clé "seerr").
"""
import asyncio
import json
import logging
import re
import urllib.error
import urllib.request

import discord
from discord.ext import commands, tasks

from ..core.permissions import lock_button_ok

log = logging.getLogger("discord-bot.requests")

APIS_FILE = "/opt/discord-bot/servarr-apis.json"
LOCK_CATEGORY = "Lock"
GATE_CHANNEL = "demandes"
POLL_SECONDS = 30
TMDB_IMG = "https://image.tmdb.org/t/p/w342"
_FOOTER_RE = re.compile(r"req:(\d+)")


def _load_apis():
    try:
        with open(APIS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


class GateView(discord.ui.View):
    """Vue persistante (custom_id statiques) ; l'id de la demande vit dans le footer."""
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

    # ------------------------------------------------------------ channel
    async def _channel(self):
        gid = getattr(self.bot.cfg, "guild_id", None)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            return None
        st = self.bot.state.get("req_gate") or {}
        ch = guild.get_channel(st.get("channel")) if st.get("channel") else None
        if ch is not None:
            return ch
        cat = None
        cid = self.bot.state.get("lock_category_id")
        if cid and isinstance(guild.get_channel(cid), discord.CategoryChannel):
            cat = guild.get_channel(cid)
        cat = cat or discord.utils.get(guild.categories, name=LOCK_CATEGORY)
        if cat is None:
            try:
                cat = await guild.create_category(LOCK_CATEGORY, reason="portail demandes")
            except discord.HTTPException:
                cat = None
        if cat is not None:
            self.bot.state.set("lock_category_id", cat.id)
        ch = discord.utils.get(guild.text_channels, name=GATE_CHANNEL)
        if ch is None:
            try:
                ch = await guild.create_text_channel(
                    GATE_CHANNEL, category=cat,
                    topic="Validation des demandes Seerr — Approuver / Refuser (admins)")
                log.info("salon #demandes créé")
            except discord.HTTPException:
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

    async def _embed(self, req, title, year, poster, ov):
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
        emb.set_footer(text=f"req:{req['id']} · en attente de validation")
        return emb

    # ------------------------------------------------------------ bot proposal
    async def propose(self, itx, kind, result):
        """Appelé par /film /serie : poste une PROPOSITION dans #demandes (rien n'est
        créé dans Seerr tant que ce n'est pas approuvé ici). Renvoie True si posté."""
        ch = await self._channel()
        if ch is None:
            return False
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
        except discord.HTTPException:
            return False
        props = dict(self.bot.state.get("req_props") or {})
        props[str(msg.id)] = {"body": body, "who": who, "title": title}
        self.bot.state.set("req_props", props)
        e = msg.embeds[0]; e.set_footer(text=f"prop:{msg.id} · en attente de validation")
        try:
            await msg.edit(embed=e)
        except discord.HTTPException:
            pass
        return True

    # ------------------------------------------------------------ poll loop
    @tasks.loop(seconds=POLL_SECONDS)
    async def gatepoll(self):
        ch = await self._channel()
        if ch is None:
            return
        data = await self._get("/api/v1/request?take=40&filter=pending&sort=added")
        if data is None:
            return
        pending = {str(r["id"]): r for r in data.get("results", [])}
        posted = dict(self.bot.state.get("req_gate_msgs") or {})  # reqId -> messageId

        # 1) new pending -> post
        for rid, req in pending.items():
            if rid in posted:
                continue
            title, year, poster, ov = await self._details(req)
            emb = await self._embed(req, title, year, poster, ov)
            try:
                msg = await ch.send(embed=emb, view=GateView(self))
                posted[rid] = msg.id
                log.info("demande %s postee dans #demandes", rid)
            except discord.HTTPException as e:
                log.warning("post demande %s: %s", rid, e)

        # 2) posted but no longer pending (résolu ailleurs) -> reconcile
        for rid in list(posted):
            if rid in pending:
                continue
            try:
                msg = await ch.fetch_message(posted[rid])
                if msg.embeds and (msg.embeds[0].footer and "en attente" in (msg.embeds[0].footer.text or "")):
                    e = msg.embeds[0]; e.color = 0x9AA0A6
                    e.set_footer(text=f"req:{rid} · traité directement dans Seerr")
                    await msg.edit(embed=e, view=None)
            except discord.NotFound:
                pass
            except discord.HTTPException:
                continue
            posted.pop(rid, None)

        self.bot.state.set("req_gate_msgs", posted)

    @gatepoll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ decision
    async def _decide(self, itx: discord.Interaction, action):
        # #demandes est dans la catégorie Lock -> même exigence que ses autres boutons :
        # session 2FA + rôles Gestion ET O (ou propriétaire). lock_button_ok répond seul.
        if not await lock_button_ok(itx):
            return
        foot = (itx.message.embeds[0].footer.text or "") if itx.message.embeds else ""
        mreq = re.search(r"req:(\d+)", foot)
        mprop = re.search(r"prop:(\d+)", foot)
        await itx.response.defer()
        e = itx.message.embeds[0]
        if mreq:                                    # demande Seerr existante (UI) -> approve/decline API
            rid = mreq.group(1)
            st = await self._post(f"/api/v1/request/{rid}/{action}")
            if st not in (200, 201):
                await itx.followup.send(f"⚠️ Échec Seerr (code {st}).", ephemeral=True); return
            posted = dict(self.bot.state.get("req_gate_msgs") or {}); posted.pop(rid, None)
            self.bot.state.set("req_gate_msgs", posted)
        elif mprop:                                 # proposition du bot -> créer la demande SEULEMENT si approuvée
            pid = mprop.group(1)
            props = dict(self.bot.state.get("req_props") or {})
            p = props.pop(pid, None)
            if action == "approve":
                if not p:
                    await itx.followup.send("Proposition expirée — relance /film.", ephemeral=True); return
                st = await self._post("/api/v1/request", p["body"])   # crée -> télécharge
                if st not in (200, 201):
                    props[pid] = p; self.bot.state.set("req_props", props)
                    await itx.followup.send(f"⚠️ Échec création Seerr (code {st}).", ephemeral=True); return
            self.bot.state.set("req_props", props)
        else:
            await itx.followup.send("Élément introuvable.", ephemeral=True); return
        if action == "approve":
            e.color = 0x2ECC71
            e.set_footer(text=f"✅ approuvé par {itx.user.display_name} — téléchargement lancé")
        else:
            e.color = 0xE74C3C
            e.set_footer(text=f"✖️ refusé par {itx.user.display_name}")
        try:
            await itx.message.edit(embed=e, view=None)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Requests(bot))
