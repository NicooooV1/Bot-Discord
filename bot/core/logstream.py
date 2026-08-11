"""Live syslog stream: UDP/514 listener -> coalesced posts to Discord channels.

Reuses the proven parse + coalesce logic (syslog_lib), but the sink is the gateway
(channel.send) instead of a webhook. The receiver runs in a thread (stdlib socket);
the flusher runs on the asyncio loop so it can await channel.send.

v2: appname extraction, drop filters (regex / per-host severity / muted programs),
cross-window repeat cooldown, overflow digest, send retry queue, per-CT routing
(mirror/move), pause flag, stats counters, graceful async close with final flush.

Thread-safety: the UDP thread touches ONLY self.agg (internally locked), the
counters (via _bump, lock-protected), self.listen_error / self._bound (une chaîne
et un Event, écrits par le thread et lus par la boucle) et reads self.paused /
self.min_sev / filter structures (read-only after __init__ except min_sev, an
atomic int swap from /logstream severity). Everything else (pending, recent,
channel cache) lives on the event loop only.
"""
import asyncio
import logging
import re
import socket
import threading
import time
from collections import OrderedDict, deque

import discord

from .syslog_lib import (Aggregator, MAX_HOST, SEVERITY_NUM,
                         format_group, parse_packet, sanitize_field, sev_fr)

log = logging.getLogger("discord-bot.logstream")

RECENT_HARD_CAP = 512


class LiveLogStream:
    def __init__(self, bot):
        cfg = bot.cfg
        self.bot = bot
        self.cfg = cfg
        self.channel_id = cfg.live_log_channel_id
        self.min_sev = SEVERITY_NUM.get(cfg.live_log_min_severity, 4)
        self.bind = (cfg.live_log_bind_addr, cfg.live_log_bind_port)
        self.flush = cfg.live_log_flush_interval
        self.max_groups = cfg.live_log_max_groups
        self.agg = Aggregator()
        self.stop = threading.Event()
        # Pause persistée (survit au redémarrage / à l'extinction nocturne).
        self.paused = bool(bot.state.get("logstream_paused"))
        self._thread = None
        self._task = None
        self._closed = False
        self._started = False
        # État réel de l'écoute UDP. Sans lui, un bind raté restait invisible :
        # start() renvoyait True, /logstream stats affichait « ▶️ actif » et le
        # salon annonçait « 📡 Flux de logs démarré » alors que RIEN n'écoutait —
        # un silence total se lisait comme une infra calme. (2026-08-11)
        self.listen_error = None
        self._bound = threading.Event()   # posé par le thread : bind tranché (OK ou non)

        # --- filters (built once; read-only from the UDP thread afterwards) ---
        self.ignore_res = []
        for pat in (p.strip() for p in (cfg.log_ignore_regex or "").split(";")):
            if not pat:
                continue
            try:
                self.ignore_res.append(re.compile(pat))
            except re.error as e:
                log.warning("LOG_IGNORE_REGEX: motif invalide ignoré %r (%s)", pat, e)
        self.sev_overrides = {}
        for host, name in cfg.log_min_sev_overrides.items():
            num = SEVERITY_NUM.get(name)
            if num is None:
                log.warning("LOG_MIN_SEV_OVERRIDES: sévérité inconnue %r pour %s (ignoré)",
                            name, host)
            else:
                self.sev_overrides[host] = num
        self.mute_programs = cfg.log_mute_programs

        # --- routing (event loop only) ---
        self.route = cfg.log_route_per_ct
        self.route_mode = cfg.log_route_mode
        self._chan_cache = {}
        self._route_warned = set()

        # --- send retry queue (event loop only) ---
        self.pending = deque(maxlen=cfg.log_retry_queue_max)

        # --- cross-window repeat cooldown (event loop only) ---
        self.cooldown = cfg.log_repeat_cooldown_seconds
        self.recent = OrderedDict()  # coalesce_key -> {last_posted, pending, first_seen, g}

        # --- counters (UDP thread + event loop -> lock) ---
        self._ctr_lock = threading.Lock()
        self.counters = {"received": 0, "filtered": 0, "posted_groups": 0,
                         "suppressed_repeats": 0, "overflow_folded": 0,
                         "send_failures": 0, "retry_dropped": 0}
        self.since_ts = time.time()

    # ------------------------------------------------------------------ utils

    def _bump(self, name, n=1):
        with self._ctr_lock:
            self.counters[name] = self.counters.get(name, 0) + n

    def listening(self):
        """L'écoute UDP est-elle réellement vivante ? (bind OK ET thread en vie)"""
        return bool(self._thread and self._thread.is_alive() and not self.listen_error)

    def stats(self):
        with self._ctr_lock:
            d = dict(self.counters)
        d["since_ts"] = self.since_ts
        # Groupes jetés par le plafond de l'agrégateur (rafale de clés distinctes).
        d["agg_dropped"] = self.agg.dropped
        # Vérité sur l'écoute, pour que /logstream stats cesse d'afficher « actif »
        # quand le socket n'a jamais pu être lié. (2026-08-11)
        d["listening"] = self.listening()
        d["listen_error"] = self.listen_error
        return d

    # ------------------------------------------------------------- lifecycle

    def start(self):
        # On lie TOUJOURS le socket UDP et on lance le flusher, indépendamment de
        # channel_id : sous auto-provisioning, provision._rewire renseigne
        # self.channel_id APRÈS coup (sans rappeler start()). Le flusher tourne
        # déjà et publiera dès que channel_id sera défini ; l'envoi Discord réel
        # reste conditionné à channel_id (cf. _flusher / _flush_once).
        if self._started:
            return False  # garde anti-double-bind (ne pas lier le port 514 deux fois)
        self._started = True
        self.stop.clear()
        self._thread = threading.Thread(target=self._recv, daemon=True)
        self._thread.start()
        self._task = self.bot.loop.create_task(self._flusher())
        # « démarrage » et non « listening » : le bind n'est pas encore tranché ici,
        # c'est le thread qui journalise le succès ou l'échec réel. (2026-08-11)
        if self.channel_id:
            log.info("démarrage du flux de logs udp %s:%s -> salon %s (severity <= %s)",
                     self.bind[0], self.bind[1], self.channel_id, self.min_sev)
        else:
            log.info("démarrage du flux de logs udp %s:%s (aucun salon cible pour "
                     "l'instant — envoi en attente d'un channel_id)",
                     self.bind[0], self.bind[1])
        return True

    async def aclose(self):
        """Graceful shutdown: stop listener + flusher, then final flush (bounded)."""
        if self._closed:
            return
        self._closed = True
        self.stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            try:
                await asyncio.to_thread(self._thread.join, 1.5)
            except Exception:
                pass
        if not self.channel_id:
            return
        # 1) re-try whatever was still queued
        while self.pending:
            cid, content = self.pending.popleft()
            try:
                await asyncio.wait_for(self._post(cid, content), 5)
            except Exception:
                break
        # 2) final aggregator drain -> #logs, prefixed
        groups = self.agg.drain()
        if not groups:
            return
        items = sorted(groups.values(), key=lambda g: (g["sev"], -g["count"]))
        # « bounded » l'était dans le docstring, pas dans le code : on publiait TOUS les
        # groupes restants, soit potentiellement des centaines de messages à l'arrêt (et
        # autant de format_group sur la boucle). Même règle que le flush normal : les
        # max_groups plus graves, le reste en résumé. (2026-08-11)
        digest = None
        if len(items) > self.max_groups:
            folded = items[self.max_groups:]
            items = items[:self.max_groups]
            digest = self._overflow_digest([(g, None) for g in folded])
        lines = [format_group(g) for g in items]
        if digest:
            lines.append(digest)
        prefix = "⏹️ arrêt du flux — "
        buf, size, first = [], len(prefix), True
        for line in lines:
            if size + len(line) > 1900 and buf:
                content = (prefix if first else "") + "\n".join(buf)
                first = False
                try:
                    await asyncio.wait_for(self._post(self.channel_id, content), 5)
                except Exception:
                    return
                buf, size = [], 0
            buf.append(line)
            size += len(line) + 1
        if buf:
            content = (prefix if first else "") + "\n".join(buf)
            try:
                await asyncio.wait_for(self._post(self.channel_id, content), 5)
            except Exception:
                pass

    # ------------------------------------------------------------ UDP thread

    def _recv(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(self.bind)
            s.settimeout(1.0)
        except OSError as e:
            # Port 514 déjà pris, ou CAP_NET_BIND_SERVICE perdue après une retouche de
            # l'unité systemd : on mémorise la panne (le flusher et /logstream stats la
            # relaieront) au lieu de mourir en silence. (2026-08-11)
            self.listen_error = f"bind udp {self.bind[0]}:{self.bind[1]} impossible ({e})"
            log.error("cannot bind udp %s:%s: %s", self.bind[0], self.bind[1], e)
            self._bound.set()      # débloque le flusher, qui lira listen_error
            return
        log.info("écoute udp %s:%s active", self.bind[0], self.bind[1])
        self._bound.set()
        try:
            while not self.stop.is_set():
                try:
                    data, addr = s.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError as e:
                    if not self.stop.is_set():
                        self.listen_error = f"réception UDP interrompue ({e})"
                        log.error("réception udp interrompue: %s", e)
                    break
                if not data:
                    continue
                try:
                    sev, host, app, text = parse_packet(data, addr[0])
                    self._bump("received")
                    if self.paused:
                        self._bump("filtered")
                        continue
                    if sev > self.sev_overrides.get(host, self.min_sev):
                        self._bump("filtered")
                        continue
                    if app and app.lower() in self.mute_programs:
                        self._bump("filtered")
                        continue
                    if self.ignore_res:
                        probe = f"{host} {app}: {text}"
                        if any(r.search(probe) for r in self.ignore_res):
                            self._bump("filtered")
                            continue
                    self.agg.add(sev, host, app, text)
                except Exception:
                    # Un seul paquet pathologique ne doit pas emporter le collecteur :
                    # sans ce filet le thread meurt, plus rien ne remonte, et le seul
                    # signe est une trace sur stderr. (2026-08-11)
                    log.exception("paquet syslog ignoré (traitement en erreur)")
        finally:
            # close() était placé après la boucle : toute sortie par exception laissait
            # le socket ouvert (et le port 514 occupé jusqu'au redémarrage).
            s.close()
            if not self.stop.is_set() and not self.listen_error:
                self.listen_error = "thread d'écoute arrêté"

    # ------------------------------------------------------------ event loop

    async def _get_channel(self, cid):
        ch = self._chan_cache.get(cid)
        if ch is not None:
            return ch
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception:
                return None
        self._chan_cache[cid] = ch
        return ch

    async def _post(self, channel_id, content):
        """Raw single send. True on success; never raises (except CancelledError)."""
        ch = await self._get_channel(channel_id)
        if ch is None:
            log.warning("salon %s introuvable pour le flux de logs", channel_id)
            return False
        try:
            # Le contenu provient de syslog (texte contrôlé par l'émetteur) :
            # on neutralise toute mention pour éviter un ping @everyone/@role/@user.
            await ch.send(content[:2000],
                          allowed_mentions=discord.AllowedMentions.none())
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("échec d'envoi vers le salon %s", channel_id)
            return False

    async def _send(self, channel_id, content):
        """Send; on failure queue (channel_id, content) for retry next flush."""
        ok = await self._post(channel_id, content)
        if not ok:
            self._bump("send_failures")
            if len(self.pending) == self.pending.maxlen:
                self._bump("retry_dropped")  # deque eviction of the oldest entry
            self.pending.append((channel_id, content))
        return ok

    async def _drain_pending(self):
        """Retry queued messages oldest-first; stop at the first re-failure."""
        while self.pending:
            cid, content = self.pending.popleft()
            if not await self._post(cid, content):
                self._bump("send_failures")
                self.pending.appendleft((cid, content))
                break

    async def _flusher(self):
        await self.bot.wait_until_ready()
        # Avis de démarrage uniquement si un salon cible est déjà connu. Sous
        # auto-provisioning il ne l'est pas encore : on garde la boucle vivante
        # et l'envoi démarrera dès que provision._rewire aura posé channel_id.
        if self.channel_id and self.cfg.log_startup_notice:
            # On attend le verdict du bind avant d'annoncer quoi que ce soit : annoncer
            # « flux démarré » alors que le socket n'a pas pu être lié est le signal le
            # plus trompeur du bot (l'exploitant lit ensuite le silence comme une infra
            # calme). Le thread tranche en quelques µs ; 2 s est une marge. (2026-08-11)
            await asyncio.to_thread(self._bound.wait, 2.0)
            sevname = sev_fr(self.min_sev)
            if self.listen_error:
                await self._send(self.channel_id,
                                 f"⛔ Flux de logs : **écoute UDP hors service** — "
                                 f"{self.listen_error}. Aucun log ne remontera ici tant "
                                 f"que ce n'est pas corrigé.")
            else:
                await self._send(self.channel_id,
                                 f"📡 Flux de logs démarré (sévérité ≤ {sevname}, "
                                 f"flush {self.flush:g}s).")
        while not self.stop.is_set():
            try:
                await asyncio.sleep(self.flush)
            except asyncio.CancelledError:
                break
            try:
                await self._flush_once()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("flush du flux de logs échoué")

    async def _flush_once(self):
        if not self.channel_id:
            # Pas encore de salon cible (auto-provisioning) : on draine
            # l'agrégateur pour éviter une croissance non bornée, mais ces groupes
            # sont abandonnés — l'envoi reprend dès que channel_id est renseigné.
            self.agg.drain()
            return
        await self._drain_pending()
        now = time.time()
        groups = self.agg.drain()
        items = self._cooldown_pass(groups, now)
        if not items:
            return
        items.sort(key=lambda it: (it[0]["sev"], -it[0]["count"]))
        digest = None
        if len(items) > self.max_groups:
            folded = items[self.max_groups:]
            items = items[:self.max_groups]
            digest = self._overflow_digest(folded)
            self._bump("overflow_folded", len(folded))
        await self._dispatch(items, digest)

    # --- cross-window repeat cooldown -------------------------------------

    def _cooldown_pass(self, groups, now):
        """Suppress groups re-posted within the cooldown; release backlogs after it.

        Returns a list of (group, note) to post; note is the French annotation
        appended when a suppressed backlog is released.
        """
        if self.cooldown <= 0:
            return [(g, None) for g in groups.values()]
        out, seen = [], set()
        for key, g in groups.items():
            seen.add(key)
            r = self.recent.get(key)
            if r is None:
                self.recent[key] = {"last_posted": now, "pending": 0,
                                    "first_seen": now, "g": g}
                out.append((g, None))
            elif now - r["last_posted"] < self.cooldown:
                r["pending"] += g["count"]
                r["g"] = g
                self._bump("suppressed_repeats")
            else:
                if r["pending"]:
                    total = r["pending"] + g["count"]
                    since = time.strftime("%H:%M", time.localtime(r["first_seen"]))
                    gg = dict(g)
                    gg["count"] = 1  # note replaces the builtin _(xN)_ suffix
                    out.append((gg, f"(x{total}, toujours en cours depuis {since})"))
                else:
                    r["first_seen"] = now  # quiet episode ended; new one starts
                    out.append((g, None))
                r["last_posted"] = now
                r["pending"] = 0
                r["g"] = g
        # backlogs whose cooldown expired without a new arrival this window
        for key, r in list(self.recent.items()):
            if key in seen:
                continue
            if r["pending"] and now - r["last_posted"] >= self.cooldown:
                since = time.strftime("%H:%M", time.localtime(r["first_seen"]))
                gg = dict(r["g"])
                gg["count"] = 1
                out.append((gg, f"(x{r['pending']}, toujours en cours depuis {since})"))
                r["last_posted"] = now
                r["pending"] = 0
            elif not r["pending"] and now - r["last_posted"] > 2 * self.cooldown:
                del self.recent[key]
        while len(self.recent) > RECENT_HARD_CAP:
            self.recent.popitem(last=False)
        return out

    # --- overflow digest ---------------------------------------------------

    @staticmethod
    def _overflow_digest(folded):
        """One compact line summarizing the groups beyond max_groups."""
        per_host = OrderedDict()
        for g, _note in folded:
            h = per_host.setdefault(g["host"], {"count": 0, "sev": g["sev"]})
            h["count"] += g["count"]
            h["sev"] = min(h["sev"], g["sev"])
        hosts = sorted(per_host.items(), key=lambda kv: (kv[1]["sev"], -kv[1]["count"]))
        segs = []
        for hostname, info in hosts:
            sevname = sev_fr(info["sev"])
            # hostname vient du datagramme : même assainissement que format_group.
            # L'échappement seul ne suffisait pas — un hôte RFC5424 peut contenir un
            # saut de ligne et forgeait une ligne de plus dans le résumé. (2026-08-11)
            seg = f"**{sanitize_field(hostname, MAX_HOST)}** x{info['count']} (pire: {sevname})"
            if segs and len(" · ".join(segs + [seg])) > 300:
                break
            segs.append(seg)
        line = f"📦 _+{len(folded)} groupes: " + " · ".join(segs)
        rest = len(hosts) - len(segs)
        if rest > 0:
            line += f" · +{rest} hôtes"
        return line + "_"

    # --- routing + batching --------------------------------------------------

    @staticmethod
    def _format_item(g, note):
        line = format_group(g)
        if note:
            line += f" _{note}_"
        return line

    async def _dispatch(self, items, digest_line):
        """Build per-channel line buffers (#logs + optional per-CT) and send them."""
        main = self.channel_id
        per_chan = OrderedDict()
        for g, note in items:
            line = self._format_item(g, note)
            self._bump("posted_groups")
            targets = [main]
            if self.route:
                cid = self.cfg.ct_channels.get(g["host"])
                if cid:
                    ch = None if cid in self._route_warned else await self._get_channel(cid)
                    if ch is None:
                        if cid not in self._route_warned:
                            self._route_warned.add(cid)
                            log.warning("salon de routage %s introuvable pour %s "
                                        "— repli sur le salon de flux", cid, g["host"])
                    elif self.route_mode == "move":
                        targets = [cid]
                    else:  # mirror
                        targets = [main, cid]
            for cid in targets:
                per_chan.setdefault(cid, []).append(line)
        if digest_line:
            per_chan.setdefault(main, []).append(digest_line)
        for cid, lines in per_chan.items():
            await self._send_batched(cid, lines)

    async def _send_batched(self, cid, lines):
        buf, size = [], 0
        for line in lines:
            if size + len(line) > 1900 and buf:
                await self._send(cid, "\n".join(buf))
                buf, size = [], 0
            buf.append(line)
            size += len(line) + 1
        if buf:
            await self._send(cid, "\n".join(buf))
