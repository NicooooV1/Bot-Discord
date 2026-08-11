"""Per-CT live channels (Option A): the bot maintains one auto-refreshing pinned
message per mapped Discord channel, showing that guest's live status (statut, RAM/CPU,
IP/tags, PSI, extras Jellyfin/Docker). Mapping comes from CT_CHANNELS
(ctname:channelid,...). Message ids persist in state so it edits in place across
restarts instead of spamming.

⚠️ Plus AUCUN graphe PNG n'est joint : l'upload d'image à chaque cycle saturait la
limite de débit Discord (le graphe est resté sur le bouton 📈, à la demande). Le
docstring annonçait encore « + CPU/RAM graph » (corrigé 2026-08-11)."""
import asyncio
import logging
import time

import discord
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.gates import GatedView
from ..core.http import ApiClient
from ..core.permissions import is_guild_owner
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

# Auto-limitation des renommages de salon. Discord limite à 2 renommages / 10 min PAR
# SALON et discord.py ne LÈVE PAS sur un 429 : il DORT jusqu'à expiration (aucun
# max_ratelimit_timeout n'est configuré). Un `except HTTPException` n'attrape donc rien —
# c'est ce sommeil, en série dans la boucle, qui figerait la mise à jour de TOUS les
# autres salons. Marge volontaire au-dessus de 300 s (2/600 s pile) pour ne jamais
# toucher le quota en bordure de fenêtre. Même garde-fou que node_channel._sync_emoji
# (2026-08-11).
RENAME_MIN_INTERVAL = 360

# Sentinelle « serveur de cet invité non résolu ». À NE JAMAIS confondre avec None, qui
# signifie « invité du R820 » : retomber sur None ferait garder les boutons d'un salon
# AVEYRON par les rôles Gestion du R820 (fail-open de PÉRIMÈTRE d'autorisation).
# is_admin() étant fail-closed sur une clé absente de GESTION_SERVERS, cette sentinelle
# refuse par construction ; les handlers la traitent avec un message clair (2026-08-11).
_SRV_UNRESOLVED = "__non-résolu__"


class CtChannels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.map = bot.cfg.ct_channels
        self._config_cache = {}   # vmid -> (ts, {ip, tags, cores, memory})
        # nom d'invité -> clé serveur (« AVY-PVE »…), mémorisée à chaque cycle : les
        # boutons en ont besoin AVANT l'ACK des 3 s, où toute I/O PVE est interdite.
        self._srv_of = {}
        self._rename_ts = {}      # id de salon -> dernier renommage (time.monotonic)
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
        """cores/mémoire/tags/IP (config PVE) — cache 30 min (voir CONFIG_CACHE_TTL).

        ⚠️ On ne met en cache QUE les succès : l'écriture était auparavant hors du `try`,
        donc un échec (5xx, timeout, AvyUnreachable pendant le coupe-circuit Aveyron)
        mémorisait un `{}` pour 30 MINUTES et le champ « Config » (IP, cœurs, RAM, tags)
        disparaissait des salons alors que le lien était revenu deux minutes plus tard.
        Sur échec on ressert le dernier instantané connu (même périmé) et le re-essai a
        lieu au cycle suivant, 2 min plus tard (corrigé 2026-08-11)."""
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
        except Exception as e:
            # debug et non warning : pendant le backoff Aveyron l'échec est ATTENDU et
            # se répète pour chaque invité, à chaque cycle -> ce serait du bruit pur.
            log.debug("config de %s illisible (%s) — on garde l'instantané précédent",
                      vmid, type(e).__name__)
            return hit[1] if hit else {}
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
            # [:1024] : la liste de tags d'un invité est libre côté PVE et un champ
            # d'embed est refusé au-delà de 1024 caractères (l'embed entier partirait
            # alors en erreur, pas seulement ce champ).
            emb.add_field(name="Config", value="  ".join(line)[:1024], inline=False)

        if not running:
            return
        try:
            rows = await self.bot.pve.acall(self.bot.pve.guest_rrd, vmid, gtype, "hour")
            last = rows[-1] if rows else {}
        except Exception:
            last = {}
        pc, pi = last.get("pressurecpusome") or 0, last.get("pressureiosome") or 0
        if pc >= PSI_SHOW_PCT or pi >= PSI_SHOW_PCT:
            emb.add_field(name="⚠️ Pression", value=f"CPU {pc:.0f} % · IO {pi:.0f} %",
                          inline=True)

        if gtype == "qemu":
            try:
                ag = await self.bot.pve.acall(self.bot.pve.agent_info, vmid)
            except Exception:
                ag = None
            if ag:
                fsbits = [f"`{mp}` {fmt.pct_of(u, t)}" for mp, u, t in ag["fs"][:3]]
                val = ag["os"] + (" · " + " · ".join(fsbits) if fsbits else "")
                if not meta.get("ip") and ag.get("ips"):
                    val = f"🌐 `{ag['ips'][0]}` · " + val
                emb.add_field(name="🧬 Système", value=val[:1024], inline=False)

    async def build_ct(self, name):
        """Embed live d'un invité. Ne renvoie QUE l'embed : plus aucun graphe n'est
        joint depuis que l'upload d'image saturait la limite de débit Discord — la
        signature `(emb, file)` invitait à le réintroduire (nettoyé 2026-08-11)."""
        bot = self.bot
        emb = discord.Embed(title=f"📦 {name}", color=fmt.BLURPLE)
        emb.timestamp = discord.utils.utcnow()
        described = False
        if bot.pve.enabled:
            vmid = await bot.pve.avmid_of(name)
            if vmid:
                # ⚠️ router VM/LXC : ct_status (API /lxc) LÈVE sur une VM QEMU (110/111/112)
                # -> embed vide. Les VM passent par vm_status (API /qemu).
                gtype = await bot.pve.aguest_type(name)
                try:
                    cur = await bot.pve.aguest_status(vmid, gtype)
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
        return emb

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
                    # liste + join : le « · » était collé en tête quand la piste audio
                    # n'était pas identifiée (aucun flux MediaStreams ne correspond à
                    # PlayState.AudioStreamIndex) alors qu'un sous-titre l'était ->
                    # ligne « 　 · 💬 Français » (corrigé 2026-08-11).
                    bits = []
                    if w.get("audio"):
                        bits.append(f"🔊 {w['audio']}")
                    bits.append(f"💬 {w['subtitle']}" if w.get("subtitle") else "💬 aucun")
                    sub = " · ".join(bits)
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
        # Jellyfin 10.11 : header MediaBrowser Token (ignore X-Emby-Token / api_key=).
        jf = ApiClient(url, {"Authorization": f'MediaBrowser Token="{key}"'},
                       timeout=6, label="jellyfin")
        # quiet=True : Jellyfin muet = cas nominal quand CT120 est arrêté, et on retombe
        # proprement sur InfluxDB — mais l'erreur exacte (401 = clé périmée) doit rester
        # trouvable dans le journal de debug au lieu d'être avalée en silence (2026-08-11).
        data = await jf.aget("/Sessions", {"ActiveWithinSeconds": 600}, quiet=True)
        # None = appel EN ÉCHEC, et rien d'autre : c'est ce qui déclenche le repli sur
        # InfluxDB chez l'appelant. Une liste VIDE, elle, veut dire « personne ne
        # regarde » et doit être affichée comme telle.
        # /Sessions renvoie un TABLEAU : tout le reste est une réponse anormale (corps
        # vide rendu en {} par request_json, page d'erreur JSON d'un reverse-proxy…) et
        # doit valoir « API indispo » -> repli, comme avant l'adoption de core.http.
        # Sans ce test, itérer un dict donnerait ses CLÉS (str) et `s.get` lèverait,
        # faisant échouer tout l'embed du salon (relecture 2026-08-11).
        if not isinstance(data, list):
            return None
        out = []
        for s in data:
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
        try:
            return await self.bot.pve.acall(self.bot.pve.running_vzdump_vmids)
        except Exception:
            # lecture réseau faillible : une liste de tâches illisible ne doit pas
            # avorter TOUT le cycle (embeds non rafraîchis) — au pire l'emoji 🟠
            # manque pendant un cycle (2026-08-11).
            log.warning("tâches vzdump illisibles ce cycle — emoji 🟠 ignoré",
                        exc_info=True)
            return set()

    async def _sync_channel_emoji(self, ch, name, gm, backup_vmids):
        """Nomme le salon « {emoji}-{nom PVE} » : emoji de statut (🟢 allumé / 🟠 backup /
        🔴 éteint) + base = EXACTEMENT le nom du guest sur le PVE (`name` = clé du mapping).

        La base est TOUJOURS re-dérivée du nom PVE (et non du nom actuel du salon) : ça
        corrige les noms dérivés/abrégés (ex. « web » -> « server-web ») et suit un
        renommage côté PVE. Renomme seulement si ça change (Discord limite à
        2 renommages / 10 min / salon) et jamais plus d'une fois par RENAME_MIN_INTERVAL.

        ⚠️ ABSENT ≠ ÉTEINT (corrigé 2026-08-11) : un invité absent de `gm` (échec de
        `resources()`, ou lien Aveyron coupé au-delà d'AVY_STALE_MAX -> `_avy_resources`
        renvoie [] et les ~12 invités -avy disparaissent) donnait `status=None` donc 🔴.
        Une indisponibilité de la SUPERVISION s'affichait ainsi comme une panne totale
        des machines, qui tournaient parfaitement. On garde l'emoji précédent : c'est la
        dernière information vraie, et ça évite un aller-retour de renommages au retour
        du lien. Pas d'emoji « inconnu » : il faudrait l'ajouter à fmt.STATUS_EMOJI,
        sinon strip_status_emoji ne retrouve plus la base et provision recrée le salon."""
        if name not in gm:
            return
        info = gm.get(name) or {}
        vmid = info.get("vmid")
        if vmid is not None and str(vmid) in backup_vmids:
            emoji = "🟠"
        elif info.get("status") == "running":
            emoji = "🟢"
        else:
            emoji = "🔴"
        desired = f"{emoji}-{fmt.slug(name)}"
        if ch.name == desired:
            return
        # Auto-limitation PAR SALON : discord.py DORT sur un 429 de renommage au lieu de
        # lever (le `except HTTPException` d'origine n'attrapait donc rien), et l'appel
        # est en série -> un seul salon en attente de quota gèlerait la mise à jour de
        # tous les autres. Le statut réel reste visible dans l'embed pendant ce temps.
        now = time.monotonic()
        if now - self._rename_ts.get(ch.id, -RENAME_MIN_INTERVAL) < RENAME_MIN_INTERVAL:
            return
        self._rename_ts[ch.id] = now
        try:
            await ch.edit(name=desired, reason="sync nom de salon = nom PVE + statut")
        except discord.HTTPException as e:
            # informatif : une permission manquante (Gérer les salons) se répéterait
            # silencieusement à chaque cycle sans laisser la moindre trace.
            log.warning("renommage de %s impossible (%s) — re-tenté plus tard", ch.id, e)

    def _remember_servers(self, gm):
        """Mémorise la clé serveur (« AVY-PVE »…) de chaque invité vu dans `guest_map`.

        Les boutons ont besoin de cette clé AVANT l'ACK des 3 s, là où toute I/O PVE est
        interdite : la calculer à la volée (`pve.server_of_name`) faisait un GET
        proxmoxer DANS la boucle d'événements (2026-08-11). On ne RETIRE jamais une
        entrée connue — un invité momentanément absent de `gm` (lien Aveyron coupé) doit
        rester gardé par les rôles de SON serveur, jamais retomber sur ceux du R820."""
        pve = self.bot.pve
        for name, info in (gm or {}).items():
            try:
                if not pve.is_avy_name(name):
                    continue
                node = (info or {}).get("node")
                if node:
                    self._srv_of[name] = pve.avy_server_key(node)
            except Exception:  # noqa: BLE001 — un nom exotique ne doit pas tuer le cycle
                log.debug("clé serveur indéterminable pour %s", name)

    @tasks.loop(minutes=2)
    async def refresh(self):
        states = dict(self.bot.state.get("ct_messages", {}) or {})
        # statut + backups en cours (une passe) pour l'emoji des salons
        try:
            gm = await self.bot.pve.aguest_map() if self.bot.pve.enabled else {}
        except Exception:
            # gm vide = trou de DONNÉES, pas « tout est éteint » : _sync_channel_emoji
            # ne renomme rien sur un invité absent. On journalise, sinon une panne de
            # lecture PVE reste totalement invisible (embeds simplement plus pauvres).
            log.warning("guest_map indisponible ce cycle — emojis de salon inchangés",
                        exc_info=True)
            gm = {}
        self._remember_servers(gm)
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
            try:
                emb = await self.build_ct(name)
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
                    except discord.HTTPException as e:
                        # 50 épingles max par salon : l'échec est bénin mais doit
                        # rester traçable (message live non épinglé = introuvable).
                        log.debug("épinglage impossible dans %s: %s", cid, e)
                    states[str(cid)] = msg.id
                    # Persistance IMMÉDIATE (pas en fin de boucle, ~20+ salons) : un
                    # redémarrage du bot EN COURS DE CYCLE (déploiement, crash, restart
                    # systemd) faisait perdre l'id des messages déjà créés ce cycle-là ->
                    # au redémarrage suivant, le mapping rechargé ne les connaît plus et
                    # un SECOND message est créé, laissant l'ancien orphelin (épinglé,
                    # jamais nettoyé) -> doublons observés 2026-07-17 sur 12 salons -avy.
                    self.bot.state.set("ct_messages", states)
                else:
                    # attachments=[] : purge les pièces jointes des anciens messages
                    # épinglés, créés du temps où un graphe PNG était joint.
                    await msg.edit(embed=emb, view=CtControlView(self), attachments=[])
            except discord.HTTPException as e:
                # on n'abandonne pas le salon pour autant : l'emoji du NOM reste la
                # seule information visible quand le message live n'est pas publiable.
                log.warning("message live de %s non publié (%s)", name, e)
            # Renommage du salon APRÈS l'embed (2026-08-11) : l'état live ne doit jamais
            # attendre le nom du salon — si Discord fait dormir un renommage (quota
            # 2/10 min par salon), au moins l'information est déjà à jour. Même ordre
            # que node_channel.
            await self._sync_channel_emoji(ch, name, gm, backup_vmids)

    @refresh.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


class CtControlView(GatedView):
    """Boutons persistants sous le dashboard de chaque invité : Rafraîchir + actions
    (Start/Stop/Reboot/Backup). L'invité est résolu par le salon du clic. Les ACTIONS
    sont RÉSERVÉES aux gestionnaires (rôle M/O DU SERVEUR de l'invité + session 2FA,
    porte déclarée `gate = "mod"` et appliquée par GatedView.interaction_check) et
    demandent une confirmation (comme /ctctl)."""

    gate = "mod"

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    def _guest(self, itx):
        return next((n for n, cid in self.cog.map.items() if cid == itx.channel_id), None)

    def _server(self, name):
        """Clé serveur de l'invité (« AVY-PVE »…), None pour le R820, ou la sentinelle
        _SRV_UNRESOLVED — le tout SANS AUCUNE I/O.

        ⚠️ Deux pièges corrigés le 2026-08-11 :
        1. cette clé est évaluée dans `interaction_check`, donc AVANT l'ACK des 3 s :
           l'ancien `pve.server_of_name()` descendait jusqu'à `resources()` (GET
           proxmoxer, 15 s de timeout R820 + Aveyron) DANS la boucle d'événements —
           bot entièrement figé et « interaction failed » côté client. La clé est
           désormais celle mémorisée par la boucle refresh (`cog._srv_of`) ;
        2. l'ancien repli `except -> None` signifiait « invité du R820 » : une simple
           erreur de lecture faisait garder les boutons d'un salon AVEYRON par les rôles
           Gestion du R820 (fail-open de périmètre). On renvoie maintenant une sentinelle
           qui REFUSE (is_admin est fail-closed sur une clé inconnue)."""
        if not name:
            return None
        srv = self.cog._srv_of.get(name)
        if srv:
            return srv
        try:
            # simple test de suffixe (« -avy »), aucune I/O : un invité d'Aveyron dont on
            # ignore encore le NŒUD (donc les rôles) doit être refusé, pas rattaché au R820.
            if self.cog.bot.pve.is_avy_name(name):
                return _SRV_UNRESOLVED
        except Exception:  # noqa: BLE001 — une décision d'autorisation ne s'échoue jamais en ouvert
            log.exception("clé serveur de %s indéterminable — refus", name)
            return _SRV_UNRESOLVED
        return None

    async def resolve_server(self, interaction):
        """Serveur DYNAMIQUE : il dépend de l'invité du salon cliqué, pas de la vue."""
        return self._server(self._guest(interaction))

    def _breakglass(self, interaction):
        """Propriétaire du guild / ADMIN_IDS : les deux replis que `is_admin` honore
        AVANT tout contrôle de serveur.

        ⚠️ Sans ce test, le refus « serveur non résolu » ci-dessous verrouillerait AUSSI
        le propriétaire, et ce n'est pas un cas de bord : `_srv_of` est vide tant que la
        boucle refresh n'a pas tourné (donc à chaque redémarrage du bot), et il le reste
        indéfiniment si le lien Aveyron est coupé au-delà d'AVY_STALE_MAX au moment du
        démarrage (`_avy_resources()` renvoie [] -> aucun invité -avy dans `guest_map`).
        Le modèle écrit dans core/permissions dit l'inverse : « aucune configuration
        cassée ne peut verrouiller totalement le bot ». Aucune porte n'est rouverte pour
        autant — `is_admin` accorde déjà ces deux replis quelle que soit la clé serveur
        (relecture 2026-08-11)."""
        cfg = getattr(interaction.client, "cfg", None)
        return (is_guild_owner(interaction)
                or (cfg is not None
                    and interaction.user.id in (getattr(cfg, "admin_ids", None) or ())))

    async def interaction_check(self, interaction) -> bool:
        # message explicite sur le cas « serveur non résolu » : sans ça l'utilisateur
        # lirait « rôle Gestion __non-résolu__ » (fail-closed, mais incompréhensible).
        if (await self.resolve_server(interaction) == _SRV_UNRESOLVED
                and not self._breakglass(interaction)):
            try:
                await interaction.response.send_message(
                    "⏳ Serveur de cet invité non résolu (supervision AVEYRON en cours "
                    "de synchronisation) — réessaie dans une minute.", ephemeral=True)
            except discord.HTTPException:
                pass
            return False
        return await super().interaction_check(interaction)

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="ctchannels:refresh")
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        # porte (rôle Gestion DU SERVEUR de l'invité + 2FA) posée par GatedView, donc
        # AVANT ce corps — pas le rôle A « vision »
        name = self._guest(itx)
        await itx.response.defer()
        if name is None:
            return
        try:
            emb = await self.cog.build_ct(name)
            await itx.message.edit(embed=emb, view=self, attachments=[])
        except discord.HTTPException:
            pass
        try:
            await self.cog.bot.action_feed(action="refresh", target=name, user=str(itx.user))
        except Exception:
            # le flux d'activité est un confort : son échec ne doit pas casser le
            # rafraîchissement, mais il doit rester traçable (2026-08-11).
            log.debug("action_feed refresh %s en échec", name, exc_info=True)

    async def _action(self, itx, act, verb):
        # ⛔ ACTIONS RÉSERVÉES : rôle Gestion DU SERVEUR de l'invité + session 2FA (les
        # boutons restent visibles du rôle A, mais inopérants pour lui) — appliqué par
        # GatedView.interaction_check, qui répond lui-même sur refus.
        bot = self.cog.bot
        name = self._guest(itx)
        if name is None:
            await itx.response.send_message("VM/conteneur introuvable pour ce salon.", ephemeral=True)
            return
        if not bot.pve.actions_enabled:
            await itx.response.send_message("Token d'action PVE non configuré "
                                            "(`PVE_ACTION_TOKEN_SECRET`).", ephemeral=True)
            return
        # ACK dans les 3 s AVANT toute I/O PVE (sinon "interaction failed" si PVE lent/down) :
        # la porte elle-même n'en fait plus aucune (cf. _server), c'est ici que commence
        # le réseau, et tout passe par les enveloppes async de pve.
        await itx.response.defer(ephemeral=True)
        try:
            vmid = await bot.pve.avmid_of(name)
            gtype = await bot.pve.aguest_type(name)
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
                upid = await bot.pve.acall(bot.pve.backup, vmid)
            else:
                upid = await bot.pve.acall((vm if gtype == "qemu" else lxc)[act], vmid)
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
        # suivi partagé (Pve.apoll_task) : cadence de 3 s conservée, et « lost » ≠ échec —
        # c'est NOTRE suivi qui s'arrête, pas la tâche (rendu par fmt.outcome_text).
        outcome = await bot.pve.apoll_task(str(upid), timeout=45, poll_every=3)
        await itx.followup.send(
            f"**{name}** — {verb.lower()} : {fmt.outcome_text(outcome)}", ephemeral=True)

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
        # porte posée par GatedView (rôle M/O du serveur de l'invité + 2FA)
        name = self._guest(itx)
        if name is None:
            await itx.response.send_message("VM/conteneur introuvable pour ce salon.", ephemeral=True)
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
        # couverture du cluster AVEYRON (pas d'équivalent déployé là-bas). Test sur
        # is_avy_name (pur test de chaîne) et non sur la clé serveur : ce garde-fou ne
        # dépend pas du NŒUD, et il ne doit surtout pas déclencher d'I/O PVE avant
        # l'ACK des 3 s — ce bouton n'a même pas de defer (2026-08-11).
        if self.cog.bot.pve.is_avy_name(name):
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
