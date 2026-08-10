"""Supervision seedbox : alertes, salon #ratio auto-actualisé (avec deltas + bouton),
commandes /ratio et /langues. Lit InfluxDB (métriques) + Radarr (audit langues).
"""
import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.permissions import read_check, admin_check
from ..views.alertaction import AlertActionView, alert_snoozed

log = logging.getLogger("discord-bot.servarr")

RATIO_WARN = 1.0
LOCK_CATEGORY = "Lock"
RATIO_CHANNEL = "ratio"
APIS_FILE = "/opt/discord-bot/servarr-apis.json"
# Vraies stats C411 (le tracker, ≠ ratio local qBittorrent) — saisies via /setratio.
DEFAULT_C411 = {"ratio": 2.96, "up_to": 2.592, "dl_go": 898.1, "bonus_go": 269.8}


def _load_apis():
    try:
        with open(APIS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _c411d(cur, prev, unit, dec):
    """Delta C411 (upload/download) entre les deux dernières saisies /setratio."""
    if prev is None:
        return ""
    d = cur - prev
    if round(d, dec) == 0:
        return " (=)"
    return f" (▲ +{d:.{dec}f} {unit})" if d > 0 else f" (▼ {d:.{dec}f} {unit})"


def _delta(cur, prev, kind="num"):
    """Chaîne d'évolution (+x) colorée par flèche, depuis le dernier rafraîchissement."""
    if prev is None:
        return ""
    d = cur - prev
    if kind == "bytes":
        if abs(d) < 1:
            return ""
        return f" (▲ {fmt.humanize_bytes(abs(d))})" if d > 0 else f" (▼ {fmt.humanize_bytes(abs(d))})"
    if abs(d) < 1e-9:
        return " (=)"
    return f" (▲ +{d:.3f})" if d > 0 else f" (▼ {d:.3f})"


class Servarr(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.apis = _load_apis()
        self._ratio_cache = None    # dernières données Influx (qb/vpn/sync/fl) — pour le débit live
        self._ratio_msg = None      # message #ratio gardé en cache (édité toutes les 10 s)
        self._qbit_cookie = None    # session qBittorrent réutilisée (débit direct temps réel)
        self._pin_lock = asyncio.Lock()   # sérialise ensure+pin_edit (ratiochan vs /setratio)
        self.loop.change_interval(seconds=bot.cfg.alert_poll_seconds)
        self.loop.start()
        self.ratiochan.start()
        self.ratiodebit.start()

    async def cog_load(self):
        # vue persistante (bouton Rafraîchir survit aux redémarrages)
        self.bot.add_view(RatioRefreshView(self))
        self.bot.add_view(AlertActionView())   # boutons Snooze sur les alertes seedbox

    def cog_unload(self):
        self.loop.cancel()
        self.ratiochan.cancel()
        self.ratiodebit.cancel()

    # ------------------------------------------------------------------ influx
    async def _pivot(self, measurement, row_keys):
        b = self.bot.cfg.influx_bucket
        rk = ", ".join(f'"{k}"' for k in row_keys)
        flux = (f'from(bucket:"{b}") |> range(start:-5m) '
                f'|> filter(fn:(r)=> r._measurement=="{measurement}") '
                f'|> last() |> pivot(rowKey:[{rk}], columnKey:["_field"], valueColumn:"_value")')
        rows = await self.bot.influx.aq(flux)
        return rows[0] if rows else None

    async def _read(self):
        qb = await self._pivot("qbittorrent", ["host"])
        vpn = await self._pivot("servarr_vpn", ["host", "country"])
        sync = await self._pivot("servarr_sync", ["host"])
        return qb, vpn, sync

    async def _freeleech_count(self):
        b = self.bot.cfg.influx_bucket
        r = await self.bot.influx.aq(
            f'from(bucket:"{b}") |> range(start:-30m) '
            f'|> filter(fn:(r)=> r._measurement=="servarr_freeleech" and r._field=="count") |> last()')
        try:
            return int(r[0]["_value"]) if r else None
        except (KeyError, ValueError, TypeError):
            return None

    async def _api_get(self, app, path):
        def _sync():
            a = self.apis[app]
            req = urllib.request.Request(a["url"] + path, headers={"X-Api-Key": a["key"]})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        try:
            return await asyncio.to_thread(_sync)
        except Exception:
            return None

    # --------------------------------------------- débit qBittorrent en direct
    def _qbit_login(self, a):
        data = urllib.parse.urlencode({"username": a["user"], "password": a["pass"]}).encode()
        req = urllib.request.Request(a["url"] + "/api/v2/auth/login", data=data,
                                     headers={"Referer": a["url"], "Origin": a["url"],
                                              "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=8) as r:
            for h in r.headers.get_all("Set-Cookie") or []:
                part = h.split(";", 1)[0]
                if part.startswith("QBT_SID") or part.startswith("SID="):
                    return part
        return None

    def _qbit_transfer_sync(self):
        """Débit instantané (octets/s) lu DIRECTEMENT sur qBittorrent = temps réel,
        alors qu'InfluxDB n'est rafraîchi que toutes les ~30 s. Cookie réutilisé."""
        a = self.apis.get("qbit")
        if not a:
            return None
        for _ in range(2):
            if not self._qbit_cookie:
                try:
                    self._qbit_cookie = self._qbit_login(a)
                except Exception:
                    return None
                if not self._qbit_cookie:
                    return None
            try:
                req = urllib.request.Request(a["url"] + "/api/v2/transfer/info",
                                             headers={"Cookie": self._qbit_cookie})
                with urllib.request.urlopen(req, timeout=8) as r:
                    j = json.loads(r.read())
                return {"up_speed": int(j.get("up_info_speed", 0) or 0),
                        "dl_speed": int(j.get("dl_info_speed", 0) or 0),
                        "up_session": int(j.get("up_info_data", 0) or 0),
                        "dl_session": int(j.get("dl_info_data", 0) or 0)}
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):     # session expirée -> on se reconnecte une fois
                    self._qbit_cookie = None
                    continue
                return None
            except Exception:
                return None
        return None

    async def _qbit_transfer(self):
        try:
            return await asyncio.to_thread(self._qbit_transfer_sync)
        except Exception:
            return None

    # --------------------------------------------- pilotage torrents (pause/reprise)
    def _qbit_req_sync(self, path, data=None):
        """Requête qBittorrent authentifiée, avec relogin auto si la session a expiré.
        `data` = dict -> POST urlencodé ; None -> GET. Retourne le corps (str) ou None."""
        a = self.apis.get("qbit")
        if not a:
            return None
        for _ in range(2):
            if not self._qbit_cookie:
                try:
                    self._qbit_cookie = self._qbit_login(a)
                except Exception:
                    return None
                if not self._qbit_cookie:
                    return None
            try:
                body = urllib.parse.urlencode(data).encode() if data is not None else None
                headers = {"Cookie": self._qbit_cookie, "Referer": a["url"]}
                if body is not None:
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                req = urllib.request.Request(a["url"] + path, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.read().decode()
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):     # session expirée -> on se reconnecte une fois
                    self._qbit_cookie = None
                    continue
                log.warning("qbit %s: HTTP %s", path, e.code)
                return None
            except Exception as e:  # noqa: BLE001
                log.warning("qbit %s: %s", path, e)
                return None
        return None

    async def _qbit_downloads(self):
        """Torrents EN TÉLÉCHARGEMENT (actifs ou en pause). On ne touche pas aux seeds :
        les mettre en pause casserait le ratio, qui est justement ce qu'on soigne."""
        raw = await asyncio.to_thread(self._qbit_req_sync, "/api/v2/torrents/info?filter=downloading")
        if raw is None:
            return None
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            return None
        # `filter=downloading` de qBit exclut les téléchargements stoppés -> on les rajoute
        raw2 = await asyncio.to_thread(self._qbit_req_sync, "/api/v2/torrents/info?filter=stopped")
        try:
            items += [t for t in json.loads(raw2 or "[]") if (t.get("progress") or 0) < 1]
        except (ValueError, TypeError):
            pass
        seen, out = set(), []
        for t in items:
            h = t.get("hash")
            if h and h not in seen:
                seen.add(h)
                out.append(t)
        return sorted(out, key=lambda t: t.get("progress") or 0, reverse=True)

    async def _qbit_ctl(self, action, hashes):
        """action='stop'|'start'. ⚠️ qBittorrent 5.2.3 (WebAPI 2.15.1) a SUPPRIMÉ
        /torrents/pause et /torrents/resume (404 vérifié) : seuls /stop et /start existent."""
        if action not in ("stop", "start") or not hashes:
            return False
        r = await asyncio.to_thread(self._qbit_req_sync, f"/api/v2/torrents/{action}",
                                    {"hashes": "|".join(hashes)})
        return r is not None

    # ------------------------------------------------------------------ alertes
    async def _channel(self):
        cid = self.bot.cfg.alert_channel_id
        if not cid:
            return None
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception:
                return None
        return ch

    async def _fire(self, ch, key, level, title, desc):
        prev = self.bot.state.alert_level(key)
        if level and level != prev:
            if alert_snoozed(self.bot.state, key):
                return   # en sommeil (Snooze)
            color = fmt.RED if level == "crit" else fmt.YELLOW
            emb = discord.Embed(title=title, description=desc, color=color)
            emb.set_footer(text=f"alerte: {key} [{level}]")
            await ch.send(embed=emb, view=AlertActionView())
            self.bot.state.set_alert(key, level)
        elif not level and prev:
            await ch.send(embed=discord.Embed(
                title=f"✅ Résolu — {title}", description=desc, color=fmt.GREEN))
            self.bot.state.clear_alert(key)

    async def _evaluate(self):
        qb, vpn, sync = await self._read()
        out = []
        if qb is None:
            out.append(("servarr_metrics", "warn", "📉 Collecteur métriques muet",
                        "aucune donnée qBittorrent depuis >5 min (service servarr-metrics ?)"))
            return out
        if vpn is not None:
            up = int(vpn.get("up", 0) or 0)
            pf = int(vpn.get("pf_ok", 0) or 0)
            country = vpn.get("country", "?")
            out.append(("servarr_vpn_down", None if up else "crit", "🛑 Tunnel VPN DOWN",
                        "le kill-switch a coupé le trafic torrent (VPN injoignable)"))
            out.append(("servarr_pf_down", None if (pf or not up) else "warn", "🔌 Port forwarding KO",
                        f"aucune connexion entrante ({country}) — ratio bridé, vérifier le port AirVPN"))
        connected = int(qb.get("connected", 0) or 0)
        out.append(("servarr_qbit_down", None if connected else "warn", "🧲 qBittorrent déconnecté",
                    "qBittorrent n'a plus de connectivité réseau"))
        if sync is not None:
            usu = int(sync.get("usersync_up", 0) or 0)
            out.append(("servarr_sync_down", None if usu else "warn", "🔁 Daemon user-sync arrêté",
                        "la synchro des comptes Jellyfin⇄Seerr ne tourne plus"))
        ratio = float(self._c411().get("ratio", 0) or 0)   # ratio C411 réel (via /setratio), pas le local qBit
        out.append(("servarr_ratio_low", "warn" if ratio < RATIO_WARN else None, "📉 Ratio bas",
                    f"ratio **{ratio:.2f}** sous le seuil {RATIO_WARN:.2f} — surveille ton compte C411"))
        return out

    @tasks.loop(seconds=60)
    async def loop(self):
        if not self.bot.influx.enabled:
            return
        ch = await self._channel()
        if ch is None:
            return
        try:
            for key, level, title, desc in await self._evaluate():
                await self._fire(ch, key, level, title, desc)
        except Exception:
            log.exception("servarr alert loop failed")

    @loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------- salon #ratio (Lock)
    async def _ensure_ratio_channel(self):
        gid = getattr(self.bot.cfg, "guild_id", None)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            return None
        info = self.bot.state.get("servarr_ratio") or {}
        ch = guild.get_channel(info["channel"]) if info.get("channel") else None
        if ch is not None:
            return ch
        cat = None
        cat_id = self.bot.state.get("lock_category_id")
        if cat_id:
            c = guild.get_channel(cat_id)
            if isinstance(c, discord.CategoryChannel):
                cat = c
        if cat is None:
            cat = discord.utils.get(guild.categories, name=LOCK_CATEGORY)
        if cat is None:
            try:
                cat = await guild.create_category(LOCK_CATEGORY, reason="salon ratio distant")
            except discord.HTTPException:
                cat = None
        if cat is not None:
            self.bot.state.set("lock_category_id", cat.id)
        ch = discord.utils.get(guild.text_channels, name=RATIO_CHANNEL)
        if ch is None:
            try:
                ch = await guild.create_text_channel(
                    RATIO_CHANNEL, category=cat, topic="Ratio C411 en temps réel — auto + bouton Rafraîchir")
                log.info("salon #ratio créé")
            except discord.HTTPException:
                return None
        cur = self.bot.state.get("servarr_ratio") or {}
        cur["channel"] = ch.id
        self.bot.state.set("servarr_ratio", cur)
        return ch

    async def _pin_edit(self, ch, embed):
        info = self.bot.state.get("servarr_ratio") or {}
        mid = info.get("message")
        msg = None
        if mid:
            try:
                msg = await ch.fetch_message(mid)
            except discord.NotFound:
                msg = None
            except discord.HTTPException:
                return
        view = RatioRefreshView(self)
        try:
            if msg is None:
                msg = await ch.send(embed=embed, view=view)
                try:
                    await msg.pin()
                except discord.HTTPException:
                    pass
                info["message"] = msg.id
                self.bot.state.set("servarr_ratio", info)
            else:
                await msg.edit(embed=embed, view=view)
            self._ratio_msg = msg   # cache pour la boucle débit (10 s), évite un fetch
        except discord.HTTPException:
            pass

    async def _qbit_24h(self):
        """Upload / download RÉELS sur 24 h = amplitude (spread) du compteur monotone
        alltime_ul/alltime_dl stocké dans InfluxDB. Montre un chiffre qui bouge vraiment."""
        b = self.bot.cfg.influx_bucket
        async def _inc(field):
            r = await self.bot.influx.aq(
                f'from(bucket:"{b}") |> range(start:-24h) '
                f'|> filter(fn:(r)=> r._measurement=="qbittorrent" and r._field=="{field}") '
                f'|> spread()')
            try:
                v = float(r[0]["_value"]) if r else None
                return v if (v is not None and v >= 0) else None
            except (KeyError, ValueError, TypeError, IndexError):
                return None
        return await _inc("alltime_ul"), await _inc("alltime_dl")

    async def _c411_official(self):
        """Ratio OFFICIEL lu direct sur C411 (mesure `c411`, collecteur CT120 toutes
        les 15 min). Fenêtre 40 min : si le collecteur est muet, renvoie None et on
        retombe sur l'estimation. up=0 => cookie de session expiré (à re-déposer)."""
        b = self.bot.cfg.influx_bucket
        rows = await self.bot.influx.aq(
            f'from(bucket:"{b}") |> range(start:-40m) '
            f'|> filter(fn:(r)=> r._measurement=="c411") |> last() '
            f'|> pivot(rowKey:["user"], columnKey:["_field"], valueColumn:"_value")')
        if not rows:
            return None
        r = rows[0]
        def num(k, d=0.0):
            try:
                return float(r.get(k, d))
            except (TypeError, ValueError):
                return d
        return {"up": int(num("up")), "ratio": num("ratio"),
                "uploaded": num("uploaded"), "downloaded": num("downloaded"),
                "upload_credit": num("upload_credit"), "min_ratio": num("min_ratio"),
                "warned": int(num("warned")), "user": r.get("user", "C411")}

    async def _fetch_ratio_data(self):
        """Récupère les données « lentes » (Influx) et les met en cache pour la boucle débit."""
        qb, vpn, sync = await self._read()
        fl = await self._freeleech_count()
        up24h, dl24h = await self._qbit_24h()
        off = await self._c411_official()
        # initialise l'ancre live si absente (l'estimation démarre sans attendre un /setratio)
        if not (self.bot.state.get("c411_anchor") or {}).get("ul") and qb:
            c = self._c411()
            await self._set_c411_anchor(c.get("ratio"), c.get("up_to"), c.get("dl_go"), qb=qb)
        self._ratio_cache = {"qb": qb, "vpn": vpn, "sync": sync, "fl": fl,
                             "up24h": up24h, "dl24h": dl24h, "official": off}
        return self._ratio_cache

    def _build_ratio_embed(self, data, speed=None):
        """Construit l'embed. `speed`=dict qBittorrent (up_speed/dl_speed/up_session/dl_session,
        octets & octets/s) => débit + volumes temps réel ; sinon on retombe sur InfluxDB.
        Le reste vient du cache `data`."""
        qb = data.get("qb")
        vpn = data.get("vpn")
        fl = data.get("fl")
        c = self._c411()
        live = self._c411_live(data, speed)
        ratio = live["ratio"]
        up_to = live["up_to"]
        dl_go = live["dl_go"]
        off = data.get("official")
        official = bool(off and off.get("up"))
        if official:
            ratio = off["ratio"]
            up_to = off["uploaded"] / 1e12
            dl_go = off["downloaded"] / 1e9
        color = fmt.GREEN if ratio >= 1 else (fmt.YELLOW if ratio >= RATIO_WARN * 0.9 else fmt.RED)
        emb = discord.Embed(title=f"📈 Ratio C411 : {ratio:.2f}", color=color)
        emb.timestamp = discord.utils.utcnow()
        if official:
            # --- valeurs OFFICIELLES lues direct sur C411 (auto, toutes les 15 min) ---
            emb.add_field(name="Ratio C411 (officiel)",
                          value=f"**{ratio:.3f}**  ·  mini requis {off.get('min_ratio', 0):.2f}",
                          inline=False)
            emb.add_field(name="⬆️ Upload C411", value=f"{up_to:.3f} To")
            emb.add_field(name="⬇️ Download C411", value=f"{dl_go:.1f} Go")
            cr = off.get("upload_credit", 0) / 1e9
            if cr >= 0.1:
                emb.add_field(name="🎁 Crédit d'upload", value=f"{cr:.0f} Go")
            if off.get("warned"):
                emb.add_field(name="⚠️ Avertissement ratio",
                              value="Ratio sous le seuil — seed davantage", inline=False)
        else:
            # --- repli estimation (relève tracker indispo / cookie expiré) ---
            suffix = " (estimé)" if live["has_anchor"] else " (manuel)"
            emb.add_field(name=f"Ratio C411{suffix}",
                          value=f"**{ratio:.3f}**", inline=False)
            up_bonus = f"\n(dont {c.get('bonus_go', 0):.1f} Go bonus)" if c.get("bonus_go") else ""
            dup = f" (▲ +{fmt.humanize_bytes(live['d_ul'])} depuis /setratio)" if live["d_ul"] >= 1 else ""
            emb.add_field(name=f"⬆️ Upload C411{suffix}",
                          value=f"{up_to:.3f} To{dup}{up_bonus}")
            ddl = f" (▲ +{fmt.humanize_bytes(live['d_dl'])})" if live["d_dl"] >= 1 else ""
            emb.add_field(name=f"⬇️ Download C411{suffix}",
                          value=f"{dl_go:.1f} Go{ddl}")
        # --- DÉBIT + VOLUMES RÉELS de la seedbox (lus direct sur qBittorrent) ---
        if isinstance(speed, dict):
            up_s, dl_s = speed.get("up_speed", 0), speed.get("dl_speed", 0)
            up_sess, dl_sess = speed.get("up_session"), speed.get("dl_session")
        else:
            up_s = dl_s = 0
            up_sess = dl_sess = None
        if qb:
            if not up_s and not dl_s:
                up_s, dl_s = (qb.get("up_speed", 0) or 0), (qb.get("dl_speed", 0) or 0)
            if up_sess is None:
                up_sess = qb.get("session_ul")
        emb.add_field(name="⚡ Débit ↑ / ↓ (temps réel)",
                      value=f"{fmt.humanize_rate(up_s)} / {fmt.humanize_rate(dl_s)}")
        # bloc « ça bouge vraiment » : upload réel qui grimpe tout seul (sans /setratio)
        def _fb(x):
            try:
                return fmt.humanize_bytes(float(x))
            except (TypeError, ValueError):
                return None
        real = []
        s_sess = _fb(up_sess)
        if s_sess:
            real.append(f"session **{s_sess}**")
        s_24 = _fb((data or {}).get("up24h"))
        if s_24:
            real.append(f"24 h ▲ {s_24}")
        s_tot = _fb(qb.get("alltime_ul")) if qb else None
        if s_tot:
            real.append(f"total {s_tot}")
        if real:
            emb.add_field(name="📦 Upload réel seedbox (qBit, auto)",
                          value=" · ".join(real), inline=False)
        if qb:
            emb.add_field(name="Seed",
                          value=f"{int(qb.get('seeding',0) or 0)}/{int(qb.get('torrents_total',0) or 0)} torrents")
        if vpn is not None:
            up = int(vpn.get("up", 0) or 0)
            pf = int(vpn.get("pf_ok", 0) or 0)
            emb.add_field(name="VPN / PF",
                          value=f"{fmt.status_emoji(up)} {vpn.get('country','?')} · PF {'✅' if pf else '❌'}")
        if fl is not None:
            emb.add_field(name="🎁 Freeleech dispo", value=str(fl))
        if official:
            emb.set_footer(text="📈 Ratio C411 OFFICIEL lu direct sur le tracker (auto toutes les 15 min) · débit seedbox ~10 s")
        else:
            emb.set_footer(text="⚠️ Relève tracker indispo (cookie expiré ?) → ratio C411 ESTIMÉ = /setratio + progression qBittorrent · débit ~10 s")
        return emb

    async def _emb_ratio(self):
        """Build complet et frais (bouton, /ratio, cycle lent) : Influx + débit direct."""
        data = await self._fetch_ratio_data()
        speed = await self._qbit_transfer()
        return self._build_ratio_embed(data, speed)

    def _c411(self):
        return self.bot.state.get("c411") or dict(DEFAULT_C411)

    @staticmethod
    def _f(x, d=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return d

    async def _set_c411_anchor(self, ratio, up_to, dl_go, qb=None):
        """Fige l'ancre = compteurs all-time qBittorrent + offset de ratio au moment d'un
        /setratio, pour que l'estimation live reparte de la vraie valeur C411 saisie."""
        if qb is None:
            qb = await self._pivot("qbittorrent", ["host"])
        ul = self._f((qb or {}).get("alltime_ul"))
        dl = self._f((qb or {}).get("alltime_dl"))
        up_bytes = self._f(up_to) * 1e12
        dl_bytes = self._f(dl_go) * 1e9
        implied = (up_bytes / dl_bytes) if dl_bytes > 0 else self._f(ratio)
        self.bot.state.set("c411_anchor",
                           {"ul": ul, "dl": dl, "ratio_off": self._f(ratio) - implied})

    def _c411_live(self, data, speed=None):
        """Estimation LIVE des stats C411 = base /setratio + ce que qBittorrent a uploadé/
        téléchargé depuis (ancre = all-time qBit au dernier /setratio). L'upload s'extrapole
        même entre 2 cycles via le compteur de session (bouge toutes les ~10 s)."""
        f = self._f
        c = self._c411()
        base_up_to, base_dl_go, base_ratio = f(c.get("up_to")), f(c.get("dl_go")), f(c.get("ratio"))
        qb = data.get("qb") if isinstance(data, dict) else None
        anchor = self.bot.state.get("c411_anchor") or {}
        a_ul, a_dl, roff = f(anchor.get("ul")), f(anchor.get("dl")), f(anchor.get("ratio_off"))
        has_anchor = a_ul > 0 and bool(qb)
        slow_ul = f((qb or {}).get("alltime_ul"))
        slow_dl = f((qb or {}).get("alltime_dl"))
        slow_sess_ul = f((qb or {}).get("session_ul"))
        live_ul = slow_ul
        if isinstance(speed, dict) and speed.get("up_session") is not None and slow_sess_ul > 0:
            d = f(speed.get("up_session")) - slow_sess_ul
            if d > 0:
                live_ul = slow_ul + d
        d_ul = max(0.0, live_ul - a_ul) if has_anchor else 0.0
        d_dl = max(0.0, slow_dl - a_dl) if has_anchor else 0.0
        up_to = base_up_to + d_ul / 1e12
        dl_go = base_dl_go + d_dl / 1e9
        up_bytes, dl_bytes = up_to * 1e12, dl_go * 1e9
        ratio = (up_bytes / dl_bytes + roff) if dl_bytes > 0 else base_ratio
        return {"ratio": ratio, "up_to": up_to, "dl_go": dl_go,
                "d_ul": d_ul, "d_dl": d_dl, "has_anchor": has_anchor}

    async def c411_snapshot(self):
        """Source de vérité C411 partagée entre #ratio et #seedbox (cog Provision) :
        officiel (relève tracker) > estimé (/setratio + progression qBit) > manuel.
        bonus_go = crédit d'upload (officiel) ou bonus saisi via /setratio (repli)."""
        try:
            off = await self._c411_official()
        except Exception:
            log.exception("c411_snapshot: relevé officiel")
            off = None
        if off and off.get("up"):
            return {"ratio": off["ratio"], "up_to": off["uploaded"] / 1e12,
                    "dl_go": off["downloaded"] / 1e9,
                    "bonus_go": off.get("upload_credit", 0) / 1e9, "source": "officiel"}
        try:
            qb = await self._pivot("qbittorrent", ["host"])
        except Exception:
            qb = None
        live = self._c411_live({"qb": qb})
        return {"ratio": live["ratio"], "up_to": live["up_to"], "dl_go": live["dl_go"],
                "bonus_go": self._f(self._c411().get("bonus_go")),
                "source": "estimé" if live["has_anchor"] else "manuel"}

    async def _push_c411(self):
        """Pousse les vraies stats C411 (saisies via /setratio) dans InfluxDB pour que
        Grafana affiche le ratio/upload/download RÉELS du tracker, pas le local qBit."""
        if not self.bot.influx.enabled:
            return
        c = self._c411()
        try:
            ratio = float(c.get("ratio", 0) or 0)
            up_to = float(c.get("up_to", 0) or 0)     # To (tels que saisis)
            dl_go = float(c.get("dl_go", 0) or 0)     # Go
            bonus = float(c.get("bonus_go", 0) or 0)  # Go
        except (TypeError, ValueError):
            return
        line = ("c411_stats,tracker=c411 "
                f"ratio={ratio},upload_to={up_to},download_go={dl_go},bonus_go={bonus},"
                f"upload_bytes={up_to * 1e12},download_bytes={dl_go * 1e9}")
        try:
            await self.bot.influx.write(line)
        except Exception:
            log.exception("push c411 -> influx")

    @tasks.loop(seconds=600)
    async def ratiochan(self):
        if not self.bot.influx.enabled:
            return
        try:
            async with self._pin_lock:
                ch = await self._ensure_ratio_channel()
                if ch is not None:
                    await self._pin_edit(ch, await self._emb_ratio())
            await self._push_c411()   # série continue C411 -> InfluxDB (Grafana)
        except Exception:
            log.exception("ratio channel refresh failed")

    @ratiochan.before_loop
    async def _before_rc(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=10)
    async def ratiodebit(self):
        """Met à jour UNIQUEMENT le débit (temps réel, lu direct sur qBittorrent) sur
        le message #ratio. Le reste (ratio C411, upload, download, seed, VPN) reste
        géré par le cycle lent ~10 min (inutile de le rafraîchir souvent)."""
        if self._ratio_cache is None or self._ratio_msg is None:
            return  # attend qu'un cycle complet ait créé le message + le cache
        speed = await self._qbit_transfer()
        if speed is None:
            return
        try:
            emb = self._build_ratio_embed(self._ratio_cache, speed)
            await self._ratio_msg.edit(embed=emb, view=RatioRefreshView(self))
        except discord.NotFound:
            self._ratio_msg = None   # message supprimé -> le cycle lent le recréera
        except discord.HTTPException:
            pass

    @ratiodebit.before_loop
    async def _before_debit(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ commandes
    @app_commands.command(description="État de la seedbox : ratio, upload, débits, torrents, VPN.")
    @read_check()
    async def ratio(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        if not self.bot.influx.enabled:
            await itx.followup.send("InfluxDB non configuré — indisponible.", ephemeral=True)
            return
        await itx.followup.send(embed=await self._emb_ratio(), ephemeral=True)

    @app_commands.command(description="Torrents en téléchargement : mettre en pause / reprendre.")
    @admin_check(require_admin_channel=False)
    async def torrents(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        dl = await self._qbit_downloads()
        if dl is None:
            await itx.followup.send("⚠️ qBittorrent injoignable.", ephemeral=True)
            return
        if not dl:
            await itx.followup.send(
                "✅ Aucun torrent en téléchargement. (Les seeds ne sont pas listés : "
                "les mettre en pause casserait le ratio.)", ephemeral=True)
            return
        view = TorrentsView(self, itx.user.id, itx)
        view.refill(dl)
        await itx.followup.send(embed=_emb_torrents(dl), view=view, ephemeral=True)

    @app_commands.command(description="Mettre à jour tes vraies stats C411 (ratio, upload, download, bonus).")
    @app_commands.describe(ratio="Ton ratio C411 (ex. 2.96)", upload_to="Upload en To (ex. 2.592)",
                           download_go="Download en Go (ex. 898.1)", bonus_go="Bonus en Go (optionnel)")
    @admin_check(require_admin_channel=False)
    async def setratio(self, itx: discord.Interaction, ratio: float, upload_to: float,
                       download_go: float, bonus_go: float = 0.0):
        await itx.response.defer(ephemeral=True)
        old = self._c411()
        self.bot.state.set("c411_prev", {"ratio": float(old.get("ratio", 0) or 0),
                                         "up_to": float(old.get("up_to", 0) or 0),
                                         "dl_go": float(old.get("dl_go", 0) or 0)})
        self.bot.state.set("c411", {"ratio": ratio, "up_to": upload_to,
                                    "dl_go": download_go, "bonus_go": bonus_go})
        await self._set_c411_anchor(ratio, upload_to, download_go)   # ré-ancre l'estimation live
        await self._push_c411()   # met à jour la série C411 dans InfluxDB (Grafana) tout de suite
        # rafraîchit le salon #ratio immédiatement
        try:
            async with self._pin_lock:
                ch = await self._ensure_ratio_channel()
                if ch is not None:
                    await self._pin_edit(ch, await self._emb_ratio())
        except Exception:
            log.exception("refresh ratio après setratio")
        # rafraîchit aussi l'embed #seedbox (cog Provision) pour rester synchro
        prov = self.bot.get_cog("Provision")
        if prov is not None:
            try:
                await prov.refresh_seedbox_embed()
            except Exception:
                log.exception("refresh seedbox après setratio")
        await itx.followup.send(
            f"✅ Stats C411 mises à jour : ratio **{ratio:.3f}** · upload {upload_to:.3f} To · "
            f"download {download_go:.1f} Go" + (f" · bonus {bonus_go:.1f} Go" if bonus_go else ""),
            ephemeral=True)

    @app_commands.command(description="Audit des langues de la bibliothèque (films sans piste audio FR).")
    @read_check()
    async def langues(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        if "radarr" not in self.apis:
            await itx.followup.send("API Radarr non configurée.", ephemeral=True)
            return
        mv = await self._api_get("radarr", "/api/v3/movie") or []
        fr, undet, nofr = 0, 0, []
        for m in mv:
            if not m.get("hasFile"):
                continue
            al = (((m.get("movieFile") or {}).get("mediaInfo") or {}).get("audioLanguages") or "").lower()
            if "fre" in al or "fra" in al:
                fr += 1
            elif not al or al == "und":
                undet += 1
            else:
                nofr.append(f"{m['title']} [{al}]")
        emb = discord.Embed(title="🗣️ Langues de la bibliothèque (films)",
                            color=fmt.GREEN if not nofr else fmt.YELLOW)
        emb.add_field(name="🇫🇷 Avec audio VF", value=str(fr))
        emb.add_field(name="Sans VF (autre langue)", value=str(len(nofr)))
        emb.add_field(name="Indéterminé (non tagué)", value=str(undet))
        if nofr:
            emb.add_field(name="Films sans VF",
                          value=("\n".join("• " + t for t in nofr[:20]))[:1024], inline=False)
        await itx.followup.send(embed=emb, ephemeral=True)


class RatioRefreshView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="servarr:ratio:refresh")
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        await itx.response.defer()
        try:
            emb = await self.cog._emb_ratio()
            await itx.message.edit(embed=emb, view=self)
        except discord.HTTPException:
            pass


_TSTATE = {
    "downloading": ("⬇️", "en cours"), "forcedDL": ("⬇️", "forcé"),
    "stalledDL": ("💤", "en attente de sources"), "metaDL": ("🔎", "métadonnées"),
    "queuedDL": ("⏳", "en file"), "checkingDL": ("🔍", "vérification"),
    "allocating": ("📦", "allocation"),
    "stoppedDL": ("⏸️", "EN PAUSE"), "pausedDL": ("⏸️", "EN PAUSE"),
}


def _tstate(s):
    return _TSTATE.get(s, ("•", s))


def _emb_torrents(dl):
    emb = discord.Embed(
        title="🧲 Torrents en téléchargement",
        description="Sélectionne un ou plusieurs torrents, puis ⏸️ ou ▶️.\n"
                    "_Les torrents en seed ne sont pas listés : les stopper casserait le ratio._",
        color=0x5865F2)
    for t in dl[:10]:
        emo, lbl = _tstate(t.get("state"))
        pct = (t.get("progress") or 0) * 100
        size = (t.get("size") or 0) / 1e9
        done = size * (t.get("progress") or 0)
        speed = (t.get("dlspeed") or 0) / 1e6
        eta = t.get("eta") or 0
        line = f"{emo} **{lbl}** · {pct:.1f} % ({done:.2f}/{size:.2f} Go)"
        if speed > 0.01:
            line += f" · {speed:.1f} Mo/s"
        if 0 < eta < 8640000:
            line += f" · ETA {eta // 60} min"
        emb.add_field(name=(t.get("name") or "?")[:80], value=line, inline=False)
    if len(dl) > 10:
        emb.set_footer(text=f"{len(dl)} torrents — 10 premiers affichés")
    return emb


class TorrentsView(discord.ui.View):
    """Sélection multiple + ⏸️/▶️. qBittorrent 5.x : endpoints /stop et /start."""

    def __init__(self, cog, owner_id, itx=None):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self._itx = itx          # interaction d'origine : seule voie pour éditer l'éphémère au timeout
        self.selected = []
        self.select = discord.ui.Select(placeholder="Torrent(s) à piloter…", min_values=1)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id == self.owner_id:
            return True
        await itx.response.send_message("⛔ Ce panneau appartient à quelqu'un d'autre.", ephemeral=True)
        return False

    def refill(self, dl):
        opts = []
        for t in dl[:25]:
            emo, lbl = _tstate(t.get("state"))
            pct = (t.get("progress") or 0) * 100
            opts.append(discord.SelectOption(
                label=(t.get("name") or "?")[:100],
                value=t.get("hash"),
                description=f"{lbl} · {pct:.1f} % · {(t.get('size') or 0) / 1e9:.2f} Go"[:100],
                emoji=emo))
        self.selected = []
        # Discord REFUSE un Select sans option -> option factice + panneau gelé (sauf Rafraîchir)
        if not opts:
            self.select.options = [discord.SelectOption(label="Aucun téléchargement en cours",
                                                        value="none")]
            self.select.disabled = True
            self.select.max_values = 1
            for c in self.children:
                if isinstance(c, discord.ui.Button) and c.label != "Rafraîchir":
                    c.disabled = True
            return
        self.select.options = opts
        self.select.disabled = False
        self.select.max_values = len(opts)
        for c in self.children:
            if isinstance(c, discord.ui.Button):
                c.disabled = False

    async def _on_select(self, itx: discord.Interaction):
        self.selected = list(self.select.values)
        await itx.response.defer()

    async def _apply(self, itx, action, verbe):
        if not self.selected:
            await itx.followup.send("Sélectionne d'abord un torrent.", ephemeral=True)
            return
        ok = await self.cog._qbit_ctl(action, self.selected)
        if not ok:
            await itx.followup.send("⚠️ qBittorrent a refusé l'action.", ephemeral=True)
            return
        log.info("%d torrent(s) %s par %s", len(self.selected), verbe, itx.user.display_name)
        n = len(self.selected)
        await asyncio.sleep(1)          # laisse qBit appliquer avant de relire
        await itx.followup.send(f"✅ {n} torrent(s) {verbe} par **{itx.user.display_name}**.",
                                ephemeral=True)
        await self._repaint(itx)

    @discord.ui.button(label="Mettre en pause", emoji="⏸️", style=discord.ButtonStyle.secondary, row=1)
    async def pause(self, itx: discord.Interaction, _b: discord.ui.Button):
        await itx.response.defer()
        await self._apply(itx, "stop", "mis en pause")

    @discord.ui.button(label="Reprendre", emoji="▶️", style=discord.ButtonStyle.success, row=1)
    async def resume(self, itx: discord.Interaction, _b: discord.ui.Button):
        await itx.response.defer()
        await self._apply(itx, "start", "repris")

    @discord.ui.button(label="Rafraîchir", emoji="🔄", style=discord.ButtonStyle.primary, row=1)
    async def refresh(self, itx: discord.Interaction, _b: discord.ui.Button):
        await itx.response.defer()
        await self._repaint(itx)

    async def _repaint(self, itx):
        """Redessine le panneau.

        ⚠️ itx.message.edit() est INTERDIT ici : le panneau est ÉPHÉMÈRE, et Message.edit()
        route vers PATCH /channels/{cid}/messages/{id}, inutilisable sur un éphémère (l'erreur
        était avalée par le `except` -> « Rafraîchir » ne faisait littéralement rien).
        edit_original_response() passe par la route webhook de l'interaction : c'est la bonne.
        """
        dl = await self.cog._qbit_downloads()
        if dl is None:
            # None = qBittorrent injoignable ; [] = vraiment aucun téléchargement.
            # Les confondre afficherait « aucun torrent » sur une simple panne transitoire.
            await itx.followup.send("⚠️ qBittorrent injoignable, panneau inchangé.", ephemeral=True)
            return
        self.refill(dl)
        try:
            await itx.edit_original_response(embed=_emb_torrents(dl), view=self)
        except discord.HTTPException as e:
            log.warning("repaint /torrents impossible: %s", e)

    async def on_timeout(self):
        """Au bout de 5 min discord.py retire la vue : sans ça les boutons restent
        cliquables et renvoient « Cette interaction a échoué » sans explication."""
        for c in self.children:
            c.disabled = True
        if self._itx is not None:
            try:
                await self._itx.edit_original_response(view=self)
            except discord.HTTPException:
                pass


async def setup(bot):
    await bot.add_cog(Servarr(bot))
