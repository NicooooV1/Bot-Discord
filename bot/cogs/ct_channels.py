"""Per-CT live channels (Option A): the bot maintains one auto-refreshing pinned
message per mapped Discord channel, showing that container's live status + CPU/RAM
graph. Mapping comes from CT_CHANNELS (ctname:channelid,...). Message ids persist in
state so it edits in place across restarts instead of spamming."""
import asyncio
import json
import logging
import time
import urllib.request

import discord
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.permissions import admin_button_ok
from ..views.confirm import ConfirmView
from .docker import _emoji as _docker_emoji

log = logging.getLogger("discord-bot.ctchannels")

# codes ISO 639-2/B Jellyfin -> libellé FR, pour l'affichage « qui regarde » (langue
# audio/sous-titres réellement utilisée par la session, demande Nico 2026-07-19)
_LANG_NAMES = {
    "fre": "Français", "fra": "Français", "eng": "Anglais", "jpn": "Japonais",
    "spa": "Espagnol", "ger": "Allemand", "deu": "Allemand", "ita": "Italien",
    "por": "Portugais", "kor": "Coréen", "chi": "Chinois", "zho": "Chinois",
    "nld": "Néerlandais", "dut": "Néerlandais", "rus": "Russe", "ara": "Arabe",
    "und": "?",
}


def _lang_label(stream):
    """Libellé humain d'un flux audio/sous-titres Jellyfin : code langue connu ->
    FR, sinon le DisplayTitle brut renvoyé par Jellyfin (déjà lisible en général)."""
    if not stream:
        return None
    code = (stream.get("Language") or "").lower()
    if code in _LANG_NAMES:
        return _LANG_NAMES[code]
    return stream.get("DisplayTitle") or stream.get("Title") or code or "?"

# pression PSI à partir de laquelle on l'affiche (sinon bruit sur un salon calme)
PSI_SHOW_PCT = 5
# cache de la config PVE (tags/cores/mémoire/IP) : change rarement, évite un appel
# API par invité à CHAQUE cycle de rafraîchissement (~2 min, jusqu'à 45+ salons)
CONFIG_CACHE_TTL = 1800

# partagé avec provision.py (salon du nœud) -> défini une seule fois dans core/format
_STATUS_EMOJI = fmt.STATUS_EMOJI
_strip_status_emoji = fmt.strip_status_emoji


class CtChannels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.map = bot.cfg.ct_channels
        self._config_cache = {}   # vmid -> (ts, {ip, tags, cores, memory})
        if self.map:
            self.refresh.change_interval(minutes=bot.cfg.dashboard_interval_min)
            self.refresh.start()

    async def cog_load(self):
        # bouton Rafraîchir persistant (survit aux redémarrages) sur chaque salon CT
        self.bot.add_view(CtControlView(self))

    def cog_unload(self):
        if self.map:
            self.refresh.cancel()

    def _guest_config_cached(self, vmid, gtype):
        """cores/mémoire/tags/IP (config PVE) — cache 30 min (voir CONFIG_CACHE_TTL)."""
        now = time.time()
        hit = self._config_cache.get(vmid)
        if hit and now - hit[0] < CONFIG_CACHE_TTL:
            return hit[1]
        meta = {}
        try:
            c = self.bot.pve.guest_config(vmid, gtype) or {}
            meta["cores"] = c.get("cores")
            meta["memory"] = c.get("memory")
            meta["tags"] = c.get("tags")
            for part in str(c.get("net0", "")).split(","):
                if part.startswith("ip=") and "/" in part:
                    meta["ip"] = part[3:].split("/")[0]
        except Exception:
            pass
        self._config_cache[vmid] = (now, meta)
        return meta

    async def _enrich(self, emb, vmid, gtype, running):
        """IP/tags/cœurs-mémoire/pression PSI/OS+FS — ajouté aux infos de base,
        R820 ET Aveyron (dispatch transparent via pve.guest_config/agent_info/guest_rrd).
        Remplace les salons #invites-X supprimés (Nico, 2026-07-18) : « je veux
        uniquement les informations par salon »."""
        try:
            meta = await asyncio.to_thread(self._guest_config_cached, vmid, gtype)
        except Exception:
            meta = {}
        line = []
        if meta.get("ip"):
            line.append(f"🌐 `{meta['ip']}`")
        if meta.get("cores") or meta.get("memory"):
            # ⚠️ l'API PVE renvoie parfois "memory" en CHAÎNE ('6144') : sans cast
            # explicite, `'6144' * 2**20` répète la chaîne au lieu de multiplier ->
            # float() de ce numéral géant déborde en +inf ("inf Eio", vécu 2026-07-18)
            try:
                mem_mib = int(meta.get("memory") or 0)
            except (TypeError, ValueError):
                mem_mib = 0
            line.append(f"⚙️ {meta.get('cores', '?')}c / "
                        f"{fmt.humanize_bytes(mem_mib * 2**20)}")
        if meta.get("tags"):
            line.append("🏷 " + str(meta["tags"]).replace(";", " · "))
        if line:
            emb.add_field(name="Config", value="  ".join(line), inline=False)

        if not running:
            return
        try:
            rows = await asyncio.to_thread(self.bot.pve.guest_rrd, vmid, gtype, "hour")
            last = rows[-1] if rows else {}
        except Exception:
            last = {}
        pc, pi = last.get("pressurecpusome") or 0, last.get("pressureiosome") or 0
        if pc >= PSI_SHOW_PCT or pi >= PSI_SHOW_PCT:
            emb.add_field(name="⚠️ Pression", value=f"CPU {pc:.0f} % · IO {pi:.0f} %",
                          inline=True)

        if gtype == "qemu":
            try:
                ag = await asyncio.to_thread(self.bot.pve.agent_info, vmid)
            except Exception:
                ag = None
            if ag:
                fsbits = [f"`{mp}` {fmt.pct_of(u, t)}" for mp, u, t in ag["fs"][:3]]
                val = ag["os"] + (" · " + " · ".join(fsbits) if fsbits else "")
                if not meta.get("ip") and ag.get("ips"):
                    val = f"🌐 `{ag['ips'][0]}` · " + val
                emb.add_field(name="🧬 Système", value=val[:1024], inline=False)

    async def build_ct(self, name):
        bot = self.bot
        emb = discord.Embed(title=f"📦 {name}", color=fmt.BLURPLE)
        emb.timestamp = discord.utils.utcnow()
        file = None
        described = False
        if bot.pve.enabled:
            vmid = await asyncio.to_thread(bot.pve.vmid_of, name)
            if vmid:
                # ⚠️ router VM/LXC : ct_status (API /lxc) LÈVE sur une VM QEMU (110/111/112)
                # -> embed vide. Les VM passent par vm_status (API /qemu).
                gtype = await asyncio.to_thread(bot.pve.guest_type, name)
                try:
                    cur = await asyncio.to_thread(bot.pve.guest_status, vmid, gtype)
                except Exception:
                    cur = {}
                running = cur.get("status") == "running"
                emb.color = fmt.GREEN if running else fmt.GREY
                emb.description = (f"{fmt.status_emoji(running)} `{cur.get('status', '?')}`"
                                   f" · vmid {bot.pve.display_vmid(vmid)}")
                described = True
                emb.add_field(name="Uptime", value=fmt.humanize_duration(cur.get("uptime")))
                mm = cur.get("maxmem") or 0
                if mm:
                    emb.add_field(name="RAM", value=fmt.pct_of(cur.get("mem") or 0, mm))
                emb.add_field(name="CPU", value=f"{(cur.get('cpu') or 0) * 100:.0f}%")
                md = cur.get("maxdisk") or 0
                if md:
                    emb.add_field(name="Disque", value=fmt.pct_of(cur.get("disk") or 0, md))
                await self._enrich(emb, vmid, gtype, running)
        if bot.influx.enabled:
            d = await bot.influx.ct_sysinfo(name)
            if d:
                bits = []
                if "node_load1" in d:
                    bits.append(f"load {d['node_load1']:.2f}")
                mt, ma = d.get("node_memory_MemTotal_bytes"), d.get("node_memory_MemAvailable_bytes")
                if mt:
                    bits.append("RAM " + fmt.pct_of(mt - (ma or 0), mt))
                if "node_procs_running" in d:
                    bits.append(f"{int(d['node_procs_running'])} procs")
                if bits:
                    emb.add_field(name="In-guest", value=" · ".join(bits), inline=False)
            # infos UTILES (remplacent l'ancien graphique CPU/RAM plat + suppriment
            # l'upload d'image qui saturait la limite de débit Discord) :
            try:
                rx, tx = await bot.influx.ct_net_rate(name)
                if rx or tx:
                    emb.add_field(name="Réseau ↓ / ↑",
                                  value=f"{fmt.humanize_rate(rx)} / {fmt.humanize_rate(tx)}")
                pk = await bot.influx.ct_peak_24h(name)
                parts = []
                if pk.get("cpu_max_pct") is not None:
                    parts.append(f"CPU {pk['cpu_max_pct']:.0f}%")
                if pk.get("ram_peak_pct") is not None:
                    rb = pk.get("ram_peak_bytes")
                    parts.append(f"RAM {pk['ram_peak_pct']:.0f}%"
                                 + (f" ({fmt.humanize_bytes(rb)})" if rb else ""))
                if parts:
                    emb.add_field(name="Pics sur 24 h", value=" · ".join(parts), inline=False)
            except Exception:
                log.exception("ct extras %s", name)
        # --- salon #jellyfin : stats de streaming (sessions, qui regarde, bibliothèque) ---
        if name == "jellyfin" and bot.influx.enabled:
            await self._add_jellyfin(emb)
        # --- salon #servarr : état des conteneurs Docker de CT120 (même source que /docker) ---
        if name == "servarr":
            await self._add_docker(emb)
        if not described and not bot.influx.enabled:
            emb.description = "données indisponibles (PVE/InfluxDB non configurés)"
        emb.set_footer(text="rafraîchi")
        return emb, file

    async def _add_jellyfin(self, emb):
        """Ajoute les stats Jellyfin (comme sur Grafana) au salon #jellyfin."""
        try:
            js = await self.bot.influx.jellyfin_stats()
            who = await self.bot.influx.jellyfin_who() or []
        except Exception:
            log.exception("jellyfin stats")
            return

        def _i(x):
            try:
                return int(float(x))
            except (TypeError, ValueError):
                return 0

        sess = js.get("sessions") or {}
        lib = js.get("library") or {}
        up = js.get("up")
        if up is not None:
            emb.add_field(name="🎬 Jellyfin", value="🟢 en ligne" if up else "🔴 hors ligne",
                          inline=False)
        active = _i(sess.get("active"))
        emb.add_field(
            name="Sessions",
            value=(f"**{active}** active(s) · ▶️ {_i(sess.get('playing'))} en lecture · "
                   f"🔄 {_i(sess.get('transcoding'))} transcodage · "
                   f"⚡ {_i(sess.get('directplay'))} direct play"),
            inline=False)
        br = _i(sess.get("bitrate_bps"))
        if br:
            emb.add_field(name="Bande passante", value=f"{br / 1_000_000:.1f} Mb/s")
        # « Qui regarde » : titre en cours via l'API Jellyfin (repli sur InfluxDB si indispo)
        now = await self._jellyfin_now_playing()
        if now is not None:
            if now:
                lines = []
                for w in now[:8]:
                    me = (w["method"] or "").lower()
                    tag = "🔄 transcode" if me.startswith("transcode") else ("⚡ direct" if me else "")
                    pp = "⏸️" if w["paused"] else "▶️"
                    head = (f"{pp} **{w['user']}** · {w['client']}"
                            + (f" · {tag}" if tag else "") + f" · {w['progress']:.0f} %")
                    sub = f"🔊 {w['audio']}" if w.get("audio") else ""
                    sub += (f" · 💬 {w['subtitle']}" if w.get("subtitle") else
                           (" · 💬 aucun" if sub else "💬 aucun"))
                    lines.append((head + f"\n　🎬 {w['title']}" + (f"\n　{sub}" if sub else ""))[:300])
                emb.add_field(name="👀 Qui regarde", value="\n".join(lines)[:1024], inline=False)
            else:
                emb.add_field(name="👀 Qui regarde", value="personne ne regarde", inline=False)
        elif who:
            lines = []
            for w in who[:8]:
                u = w.get("user") or "?"
                cl = w.get("client") or "?"
                me = (w.get("method") or "").lower()
                tag = "🔄 transcode" if me.startswith("transcode") else ("⚡ direct" if me else "")
                b = _i(w.get("bitrate_bps"))
                lines.append(f"👤 **{u}** · {cl}" + (f" · {tag}" if tag else "")
                             + (f" · {b / 1_000_000:.1f} Mb/s" if b else ""))
            emb.add_field(name="👀 Qui regarde", value="\n".join(lines)[:1024], inline=False)
        elif active == 0:
            emb.add_field(name="👀 Qui regarde", value="personne connecté", inline=False)
        if lib:
            emb.add_field(
                name="📚 Bibliothèque",
                value=(f"🎞️ {_i(lib.get('movies'))} films · 📺 {_i(lib.get('series'))} séries · "
                       f"🎬 {_i(lib.get('episodes'))} épisodes"),
                inline=False)

    async def _add_docker(self, emb):
        """Stack Docker de CT120 dans le salon #servarr : synthèse x/y up + détail,
        les conteneurs à problème (arrêtés/unhealthy) d'abord avec leur statut complet."""
        dk = self.bot.get_cog("Docker")
        if dk is None:
            return
        items = await dk.list_containers()
        if items is None:
            emb.add_field(name="🐳 Docker", inline=False,
                          value="⚠️ liste indisponible (ytgrab CT120:8770 muet)")
            return
        bad = [it for it in items if (it.get("state") or "").lower() != "running"
               or "unhealthy" in (it.get("status") or "")]
        up = sum(1 for it in items if (it.get("state") or "").lower() == "running")
        lines = [f"{_docker_emoji(it)} **{it.get('name')}** · {it.get('status') or '?'}"
                 for it in sorted(bad, key=lambda x: x.get("name") or "")]
        ok = sorted(it.get("name") or "?" for it in items if it not in bad)
        if ok:
            lines.append("🟢 " + " · ".join(ok))
        emb.add_field(name=f"🐳 Docker — {up}/{len(items)} up",
                      value="\n".join(lines)[:1024] or "aucun conteneur", inline=False)

    async def _jellyfin_now_playing(self):
        """Sessions en lecture via l'API Jellyfin -> titre exact. None si API indispo."""
        cfg = self.bot.cfg
        url = getattr(cfg, "jellyfin_url", "") or ""
        key = getattr(cfg, "jellyfin_api_key", "") or ""
        if not (url and key):
            return None

        def _sync():
            # Jellyfin 10.11 : header MediaBrowser Token (ignore X-Emby-Token / api_key=).
            req = urllib.request.Request(
                url + "/Sessions?ActiveWithinSeconds=600",
                headers={"Authorization": f'MediaBrowser Token="{key}"'})
            with urllib.request.urlopen(req, timeout=6) as r:
                return json.loads(r.read())

        try:
            data = await asyncio.to_thread(_sync)
        except Exception:
            return None
        out = []
        for s in data or []:
            it = s.get("NowPlayingItem")
            if not it:
                continue
            ps = s.get("PlayState") or {}
            rt = it.get("RunTimeTicks") or 0
            pos = ps.get("PositionTicks") or 0
            if it.get("Type") == "Episode":
                title = (f"{it.get('SeriesName', '?')} "
                         f"S{int(it.get('ParentIndexNumber') or 0):02d}"
                         f"E{int(it.get('IndexNumber') or 0):02d} · {it.get('Name', '')}")
            else:
                title = it.get("Name") or "?"
            # langue audio/sous-titres RÉELLEMENT utilisée par cette session (pas la
            # préférence du compte : ce qui est effectivement en train de jouer) —
            # MediaStreams + les index choisis dans PlayState (demande Nico 2026-07-19).
            streams = it.get("MediaStreams") or []
            a_idx, sub_idx = ps.get("AudioStreamIndex"), ps.get("SubtitleStreamIndex")
            audio = _lang_label(next((st for st in streams
                                      if st.get("Type") == "Audio" and st.get("Index") == a_idx), None))
            subtitle = (_lang_label(next((st for st in streams if st.get("Type") == "Subtitle"
                                          and st.get("Index") == sub_idx), None))
                        if sub_idx is not None and sub_idx >= 0 else None)
            out.append({
                "user": s.get("UserName") or "?",
                "client": s.get("Client") or s.get("DeviceName") or "?",
                "method": ps.get("PlayMethod") or "",
                "paused": bool(ps.get("IsPaused")),
                "progress": (pos / rt * 100) if rt else 0,
                "title": title[:120],
                "audio": audio,
                "subtitle": subtitle,
            })
        return out

    async def _running_backup_vmids(self):
        """Set des vmid dont une sauvegarde vzdump est EN COURS (→ emoji 🟠).

        Déléguée à pve.running_vzdump_vmids : la version locale interrogeait `tasks()` sans
        `source='active'` — donc uniquement l'archive des tâches TERMINÉES, dont le `status`
        vaut 'OK'/'ERROR' et jamais 'running'. L'emoji 🟠 n'est ainsi jamais apparu depuis
        l'origine (corrigé 2026-07-15 ; PVE renvoie 'RUNNING' en majuscules)."""
        if not self.bot.pve.enabled:
            return set()
        return await asyncio.to_thread(self.bot.pve.running_vzdump_vmids)

    async def _sync_channel_emoji(self, ch, name, gm, backup_vmids):
        """Nomme le salon « {emoji}-{nom PVE} » : emoji de statut (🟢 allumé / 🟠 backup /
        🔴 éteint) + base = EXACTEMENT le nom du guest sur le PVE (`name` = clé du mapping).

        La base est TOUJOURS re-dérivée du nom PVE (et non du nom actuel du salon) : ça
        corrige les noms dérivés/abrégés (ex. « web » -> « server-web ») et suit un
        renommage côté PVE. Renomme seulement si ça change (Discord limite à
        2 renommages / 10 min / salon)."""
        info = gm.get(name) or {}
        vmid = info.get("vmid")
        if vmid is not None and str(vmid) in backup_vmids:
            emoji = "🟠"
        elif info.get("status") == "running":
            emoji = "🟢"
        else:
            emoji = "🔴"
        desired = f"{emoji}-{fmt.slug(name)}"
        if ch.name != desired:
            try:
                await ch.edit(name=desired, reason="sync nom de salon = nom PVE + statut")
            except discord.HTTPException:
                pass  # rate-limit -> re-tenté au prochain cycle

    @tasks.loop(minutes=2)
    async def refresh(self):
        states = dict(self.bot.state.get("ct_messages", {}) or {})
        # statut + backups en cours (une passe) pour l'emoji des salons
        try:
            gm = await asyncio.to_thread(self.bot.pve.guest_map) if self.bot.pve.enabled else {}
        except Exception:
            gm = {}
        backup_vmids = await self._running_backup_vmids()
        for name, cid in list(self.map.items()):
            ch = self.bot.get_channel(cid)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(cid)
                except discord.NotFound:
                    # salon supprimé côté Discord (ex. invité -avy retiré à la main) : on
                    # cesse de le sonder au lieu de journaliser à chaque cycle. provision le
                    # recréera si l'invité réapparaît (aucune donnée perdue).
                    log.debug("salon %s (%s) supprimé — retiré du suivi de ce cycle", cid, name)
                    self.map.pop(name, None)
                    continue
                except Exception:
                    log.warning("channel %s for CT %s injoignable ce cycle", cid, name)
                    continue
            await self._sync_channel_emoji(ch, name, gm, backup_vmids)
            try:
                emb, file = await self.build_ct(name)
            except Exception:
                log.exception("build_ct failed for %s", name)
                continue
            mid = states.get(str(cid))
            msg = None
            if mid:
                try:
                    msg = await ch.fetch_message(mid)
                except discord.NotFound:
                    msg = None
                except discord.HTTPException:
                    continue
            try:
                if msg is None:
                    msg = await ch.send(embed=emb, view=CtControlView(self))
                    try:
                        await msg.pin()
                    except discord.HTTPException:
                        pass
                    states[str(cid)] = msg.id
                    # Persistance IMMÉDIATE (pas en fin de boucle, ~20+ salons) : un
                    # redémarrage du bot EN COURS DE CYCLE (déploiement, crash, restart
                    # systemd) faisait perdre l'id des messages déjà créés ce cycle-là ->
                    # au redémarrage suivant, le mapping rechargé ne les connaît plus et
                    # un SECOND message est créé, laissant l'ancien orphelin (épinglé,
                    # jamais nettoyé) -> doublons observés 2026-07-17 sur 12 salons -avy.
                    self.bot.state.set("ct_messages", states)
                else:
                    await msg.edit(embed=emb, view=CtControlView(self), attachments=[])
            except discord.HTTPException:
                continue

    @refresh.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


class CtControlView(discord.ui.View):
    """Boutons persistants sous le dashboard de chaque invité : Rafraîchir + actions
    (Start/Stop/Reboot/Backup). L'invité est résolu par le salon du clic. Les ACTIONS
    sont RÉSERVÉES aux administrateurs et demandent une confirmation (comme /ctctl)."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    def _guest(self, itx):
        return next((n for n, cid in self.cog.map.items() if cid == itx.channel_id), None)

    def _server(self, name):
        """Clé serveur de l'invité (« AVEYRON ») ou None (R820) : les boutons d'un salon
        AVEYRON exigent les rôles M/O AVEYRON, pas ceux du R820."""
        try:
            return self.cog.bot.pve.server_of_name(name) if name else None
        except Exception:
            return None

    async def _poll(self, upid, timeout=45):
        for _ in range(max(1, timeout // 3)):
            try:
                st = await asyncio.to_thread(self.cog.bot.pve.task_status, upid)
            except Exception:
                return "unknown"
            if st.get("status") == "stopped":
                return st.get("exitstatus") or "OK"
            await asyncio.sleep(3)
        return "running"

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="ctchannels:refresh")
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        name = self._guest(itx)
        # rôle Gestion (du serveur de l'invité) + 2FA — pas le rôle A « vision »
        if not await admin_button_ok(itx, server=self._server(name)):
            return
        await itx.response.defer()
        if name is None:
            return
        try:
            emb, _ = await self.cog.build_ct(name)
            await itx.message.edit(embed=emb, view=self, attachments=[])
        except discord.HTTPException:
            pass
        try:
            await self.cog.bot.action_feed(action="refresh", target=name, user=str(itx.user))
        except Exception:
            pass

    async def _action(self, itx, act, verb):
        bot = self.cog.bot
        cfg = bot.cfg
        name = self._guest(itx)
        if name is None:
            await itx.response.send_message("VM/conteneur introuvable pour ce salon.", ephemeral=True)
            return
        # ⛔ ACTIONS RÉSERVÉES : rôle Gestion DU SERVEUR de l'invité + session 2FA (les
        # boutons restent visibles du rôle A, mais inopérants pour lui). admin_button_ok
        # répond lui-même sur refus.
        if not await admin_button_ok(itx, server=self._server(name)):
            return
        if not bot.pve.actions_enabled:
            await itx.response.send_message("Token d'action PVE non configuré "
                                            "(`PVE_ACTION_TOKEN_SECRET`).", ephemeral=True)
            return
        # ACK dans les 3 s AVANT toute I/O PVE (sinon "interaction failed" si PVE lent/down)
        await itx.response.defer(ephemeral=True)
        try:
            vmid = await asyncio.to_thread(bot.pve.vmid_of, name)
            gtype = await asyncio.to_thread(bot.pve.guest_type, name)
        except Exception as e:
            await itx.followup.send(f"❌ PVE injoignable : `{e}`", ephemeral=True)
            return
        if not vmid:
            await itx.followup.send("VM/conteneur introuvable.", ephemeral=True)
            return
        kind = "la VM" if gtype == "qemu" else "le conteneur"
        cv = ConfirmView(itx.user.id)
        emb = discord.Embed(
            title="⚠️ Confirmation",
            description=f"**{verb}** {kind} **{name}** (vmid {bot.pve.display_vmid(vmid)}) ?",
            color=fmt.YELLOW)
        cv.message = await itx.followup.send(embed=emb, view=cv, ephemeral=True, wait=True)
        await cv.wait()
        if not cv.value:
            await itx.followup.send("Annulé.", ephemeral=True)
            return
        who = f"{itx.user}({itx.user.id})"
        lxc = {"start": bot.pve.start_ct, "stop": bot.pve.shutdown_ct, "restart": bot.pve.reboot_ct}
        vm = {"start": bot.pve.start_vm, "stop": bot.pve.shutdown_vm, "restart": bot.pve.reboot_vm}
        try:
            if act == "backup":
                upid = await asyncio.to_thread(bot.pve.backup, vmid)
            else:
                upid = await asyncio.to_thread((vm if gtype == "qemu" else lxc)[act], vmid)
        except Exception as e:
            bot.audit.record(user=who, action=act, target=f"{name}/{vmid}", result=f"error:{e}")
            await itx.followup.send(f"❌ Échec : `{e}`", ephemeral=True)
            return
        bot.audit.record(user=who, action=act, target=f"{name}/{vmid}",
                         result="submitted", upid=str(upid))
        if act == "backup":
            await itx.followup.send(
                f"💾 Sauvegarde de **{name}** lancée (suivi dans les tâches PVE / #telechargements).",
                ephemeral=True)
            return
        await itx.followup.send(f"⏳ **{verb}** lancé sur **{name}** — suivi…", ephemeral=True)
        outcome = await self._poll(str(upid))
        res = ("✅ terminé" if outcome == "OK"
               else "⏳ encore en cours" if outcome == "running" else f"⚠️ {outcome}")
        await itx.followup.send(f"**{name}** — {verb.lower()} : {res}", ephemeral=True)

    @discord.ui.button(label="Start", emoji="▶️",
                       style=discord.ButtonStyle.success, custom_id="ctchannels:start")
    async def b_start(self, itx: discord.Interaction, button: discord.ui.Button):
        await self._action(itx, "start", "Démarrer")

    @discord.ui.button(label="Stop", emoji="⏹️",
                       style=discord.ButtonStyle.danger, custom_id="ctchannels:stop")
    async def b_stop(self, itx: discord.Interaction, button: discord.ui.Button):
        await self._action(itx, "stop", "Arrêter")

    @discord.ui.button(label="Reboot", emoji="🔁",
                       style=discord.ButtonStyle.secondary, custom_id="ctchannels:reboot")
    async def b_reboot(self, itx: discord.Interaction, button: discord.ui.Button):
        await self._action(itx, "restart", "Redémarrer")

    @discord.ui.button(label="Backup", emoji="💾",
                       style=discord.ButtonStyle.secondary, custom_id="ctchannels:backup")
    async def b_backup(self, itx: discord.Interaction, button: discord.ui.Button):
        await self._action(itx, "backup", "Sauvegarder")

    @discord.ui.button(label="Graph", emoji="📈",
                       style=discord.ButtonStyle.secondary,
                       custom_id="ctchannels:graph", row=1)
    async def b_graph(self, itx: discord.Interaction, button: discord.ui.Button):
        name = self._guest(itx)
        if name is None:
            await itx.response.send_message("VM/conteneur introuvable pour ce salon.", ephemeral=True)
            return
        if not await admin_button_ok(itx, server=self._server(name)):
            return
        await itx.response.defer(ephemeral=True)
        cog = self.cog.bot.get_cog("Graphs")
        if cog is None:
            await itx.followup.send("Graphes indisponibles.", ephemeral=True)
            return
        try:
            emb, file = await cog.quick_file(name)
        except Exception as e:
            await itx.followup.send(f"❌ Graphe impossible : `{e}`", ephemeral=True)
            return
        if emb is None:
            await itx.followup.send("Aucune donnée sur 24 h.", ephemeral=True)
            return
        await itx.followup.send(embed=emb, file=file, ephemeral=True)

    @discord.ui.button(label="Terminal", emoji="🖥️",
                       style=discord.ButtonStyle.secondary,
                       custom_id="ctchannels:terminal", row=1)
    async def b_terminal(self, itx: discord.Interaction, button: discord.ui.Button):
        name = self._guest(itx)
        # le terminal repose sur termproxy + compte botconsole du PVE R820 : pas de
        # couverture du cluster AVEYRON (pas d'équivalent déployé là-bas)
        if self._server(name):
            await itx.response.send_message(
                "Terminal indisponible pour les VM/conteneurs du cluster AVEYRON.",
                ephemeral=True)
            return
        cog = self.cog.bot.get_cog("Terminal")
        if cog is None:
            await itx.response.send_message("Terminal non activé.", ephemeral=True)
            return
        await cog.open_for(itx, name)


async def setup(bot):
    await bot.add_cog(CtChannels(bot))
