"""Cog /dist — whitelist du serveur de distribution Fronote (CT122 fronote-dist).

Le serveur de distribution (dist.nicov1.fr → CT122:80) livre Fronote sous licence :
une IP non whitelistée qui tente d'installer (redeem/download/module) est refusée en
403 ET journalisée côté serveur (`refused_log`). Ce cog ferme la boucle côté Discord :

  - boucle `refus_watch` : relit `GET /admin/refused` depuis un CURSEUR persisté
    (state `dist_refused_cursor`) et poste dans #alertes une notification par IP
    refusée, avec boutons « Autoriser » / « Ignorer » directement sur le message ;
  - `/dist autoriser|retirer|statut|liste|refus` : gestion de la whitelist.

L'IP visée par les boutons est relue dans le FOOTER du message (« ip: <x> »), comme le
Snooze des alertes (views/alertaction.py) : custom_id fixes → vues persistantes au
redémarrage sans état local.

Transport : API d'admin du serveur dist, en-tête X-Admin-Token, corps FORM-ENCODÉ
(server.php lit $_POST — pas de JSON). Client local et non core.http : les réponses
d'erreur portent un corps JSON diagnostique ({"status":"bad_ip"}, 401 unauthorized…)
que `request_json` réduirait à None — même justification que docker._dk.

Anti-spam : une IP refusée n'est notifiée qu'une fois par NOTIFY_COOLDOWN (le curseur
avance quand même), et « Ignorer » la fait taire IGNORE_SECONDS. Une IP devenue
whitelistée entre-temps n'est pas notifiée.
"""
import asyncio
import ipaddress
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core.bg import guard_cog_loops
from ..core.gates import GatedView
from ..core.permissions import admin_check

log = logging.getLogger("discord-bot.dist")

NOTIFY_COOLDOWN = 1800      # s — une notification max par IP par demi-heure
IGNORE_SECONDS = 86400      # s — silence après un clic « Ignorer »
KIND_LABEL = {"redeem": "échange de clé", "download": "téléchargement", "module": "module"}


def _valid_ip(ip):
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def _ip_from_msg(msg):
    """IP portée par une notification de refus (footer « ip: <x> »)."""
    try:
        footer = (msg.embeds[0].footer.text or "")
    except (IndexError, AttributeError):
        return None
    for part in footer.split("·"):
        part = part.strip()
        if part.startswith("ip:"):
            ip = part[3:].strip()
            return ip if _valid_ip(ip) else None
    return None


class RefusedActionView(GatedView):
    """Persistante : Autoriser / Ignorer sur une notification d'IP refusée.

    Tier **mod** : autoriser une IP ouvre l'accès au serveur de licences — même
    exigence (rôle Gestion + session 2FA revalidée au clic) que /dist autoriser."""

    gate = "mod"

    def __init__(self, cog=None):
        super().__init__(timeout=None)
        self._cog = cog

    def _resolve_cog(self, itx):
        return self._cog or itx.client.get_cog("Dist")

    async def _finish(self, itx, badge):
        """Appose le badge et RETIRE les boutons du message traité.

        ⚠️ Surtout ne pas griser `self.children` : la vue est UNIQUE et PARTAGÉE entre
        tous les envois (anti-fuite du view-store, cf. Dist.__init__) — la muter ici
        faisait poster toutes les notifications SUIVANTES avec des boutons déjà
        désactivés, jusqu'au redémarrage (revue 2026-08-18). `view=None` retire les
        composants du message traité sans toucher à l'instance partagée."""
        try:
            emb = itx.message.embeds[0]
            emb.add_field(name="Décision", value=badge, inline=False)
            await itx.message.edit(embed=emb, view=None)
        except (discord.HTTPException, IndexError):
            pass

    @discord.ui.button(label="Autoriser cette IP", emoji="✅",
                       style=discord.ButtonStyle.success, custom_id="dist:refused:allow")
    async def allow(self, itx: discord.Interaction, _b: discord.ui.Button):
        cog = self._resolve_cog(itx)
        ip = _ip_from_msg(itx.message)
        if cog is None or not ip:
            await itx.response.send_message("IP introuvable dans ce message.", ephemeral=True)
            return
        await itx.response.defer(ephemeral=True)
        r = await cog.api("POST", "admin_allow",
                          data={"ip": ip, "note": f"autorisée via #alertes par {itx.user.display_name}"})
        ok = bool(r and r.get("status") == "ok")
        itx.client.audit.record(user=f"{itx.user} ({itx.user.id})", action="dist-allow",
                                target=ip, result="ok" if ok else "échec")
        if ok:
            await self._finish(itx, f"✅ Autorisée par {itx.user.display_name}")
            await itx.followup.send(
                f"✅ IP `{ip}` whitelistée — le client peut relancer son installation.",
                ephemeral=True)
        else:
            await itx.followup.send(
                f"❌ Échec : {(r or {}).get('status', 'serveur dist injoignable')}", ephemeral=True)

    @discord.ui.button(label="Ignorer", emoji="🔕",
                       style=discord.ButtonStyle.secondary, custom_id="dist:refused:ignore")
    async def ignore(self, itx: discord.Interaction, _b: discord.ui.Button):
        cog = self._resolve_cog(itx)
        ip = _ip_from_msg(itx.message)
        if cog is None or not ip:
            await itx.response.send_message("IP introuvable dans ce message.", ephemeral=True)
            return
        # On ACQUITTE avant l'édition REST (_finish) : symétrique de allow(). L'edit du
        # message d'origine peut dépasser la fenêtre de 3 s (bucket d'édition du salon
        # épuisé par des clics rapprochés, retries 502/504) ; le faire avant l'ack
        # ferait expirer le token de l'interaction (constat revue 2026-08-12).
        await itx.response.defer(ephemeral=True)
        cog.mark_ignored(ip)
        itx.client.audit.record(user=f"{itx.user} ({itx.user.id})", action="dist-ignore",
                                target=ip, result="ok")
        await self._finish(itx, f"🔕 Ignorée par {itx.user.display_name} (24 h)")
        await itx.followup.send(
            f"🔕 `{ip}` ignorée : plus de notification pendant 24 h "
            "(ses tentatives restent refusées et journalisées).", ephemeral=True)


class Dist(commands.Cog):
    """Whitelist du serveur de distribution Fronote + veille des refus."""

    dist = app_commands.Group(name="dist",
                              description="Serveur de distribution Fronote (whitelist d'IP)")

    def __init__(self, bot):
        self.bot = bot
        # UNE seule vue persistante réutilisée pour tous les envois : ses custom_id sont
        # fixes, elle dispatche donc les clics de N'IMPORTE quel message. Créer une vue
        # NEUVE à chaque chan.send la ferait s'accumuler dans le view-store de discord.py
        # (timeout=None → jamais retirée) — fuite mémoire lente sur un salon très actif
        # (constat revue 2026-08-12).
        self._refused_view = RefusedActionView(self)

    async def cog_load(self):
        self.bot.add_view(self._refused_view)
        guard_cog_loops(self)
        if self.enabled:
            secs = getattr(self.bot.cfg, "dist_poll_seconds", 120)
            self.refus_watch.change_interval(seconds=secs)
            self.refus_watch.start()
            park_secs = getattr(self.bot.cfg, "dist_park_poll_seconds", 900)
            self.parc_watch.change_interval(seconds=park_secs)
            self.parc_watch.start()
        else:
            log.warning("DIST_URL/DIST_ADMIN_TOKEN absents : /dist et la veille des "
                        "refus sont inactifs")

    async def cog_unload(self):
        self.refus_watch.cancel()
        self.parc_watch.cancel()

    @property
    def enabled(self):
        cfg = self.bot.cfg
        return bool(getattr(cfg, "dist_url", "") and getattr(cfg, "dist_admin_token", ""))

    # ------------------------------------------------------------------ HTTP
    def _call_sync(self, method, action, data=None, params=None, timeout=15):
        cfg = self.bot.cfg
        qs = {"action": action}
        qs.update(params or {})
        url = f"{cfg.dist_url}/?" + urllib.parse.urlencode(qs)
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(
            url, data=body, method=method,
            headers={"X-Admin-Token": cfg.dist_admin_token,
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    async def api(self, method, action, data=None, params=None, timeout=15):
        """Appel de l'API d'admin. Dict même sur erreur HTTP (corps diagnostique),
        None uniquement si le serveur est injoignable / réponse illisible."""
        try:
            return await asyncio.to_thread(self._call_sync, method, action, data,
                                           params, timeout)
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read() or b"{}")
            except ValueError:
                return {"status": f"http_{e.code}"}
        except Exception as e:  # noqa: BLE001 — réseau, DNS, timeout
            log.warning("dist %s %s: %s", method, action, e)
            return None

    # ------------------------------------------------------------------ état local
    def mark_ignored(self, ip):
        now = time.time()
        d = {k: ts for k, ts in (self.bot.state.get("dist_ignored") or {}).items()
             if now - float(ts or 0) < IGNORE_SECONDS}   # purge des silences expirés
        d[ip] = now
        self.bot.state.set("dist_ignored", d)

    def _suppressed(self, ip, now):
        """Notification à taire ? (déjà notifiée récemment, ou ignorée 24 h)."""
        notified = (self.bot.state.get("dist_notified") or {}).get(ip, 0)
        ignored = (self.bot.state.get("dist_ignored") or {}).get(ip, 0)
        return (now - float(notified or 0) < NOTIFY_COOLDOWN
                or now - float(ignored or 0) < IGNORE_SECONDS)

    def _remember_notified(self, ips, now):
        d = {ip: ts for ip, ts in (self.bot.state.get("dist_notified") or {}).items()
             if now - float(ts or 0) < 2 * NOTIFY_COOLDOWN}   # purge des entrées mortes
        for ip in ips:
            d[ip] = now
        self.bot.state.set("dist_notified", d)

    @staticmethod
    def _aggregate(rows):
        """Lignes brutes du journal → {ip: {count, kinds, first, last, max_id}}."""
        agg = {}
        for r in rows:
            ip = r.get("ip") or "?"
            a = agg.setdefault(ip, {"count": 0, "kinds": set(),
                                    "first": r.get("at"), "last": r.get("at"),
                                    "max_id": 0})
            a["count"] += 1
            a["kinds"].add(r.get("kind") or "?")
            a["last"] = r.get("at")
            a["max_id"] = max(a["max_id"], int(r.get("id") or 0))
        return agg

    def _refused_embed(self, ip, info):
        kinds = ", ".join(KIND_LABEL.get(k, k) for k in sorted(info["kinds"]))
        emb = discord.Embed(
            title="🚫 Tentative d'installation refusée",
            description=(f"L'IP **`{ip}`** a tenté d'installer Fronote sans être "
                         f"whitelistée ({info['count']} requête(s) : {kinds})."),
            color=0xE01B24)
        emb.add_field(name="Première", value=f"{info['first']} UTC", inline=True)
        emb.add_field(name="Dernière", value=f"{info['last']} UTC", inline=True)
        emb.add_field(name="Et maintenant ?",
                      value=("Si c'est un client attendu : **✅ Autoriser cette IP**, "
                             "puis il relance son `bootstrap.sh`.\n"
                             "Sinon : **🔕 Ignorer** (le refus reste en place)."),
                      inline=False)
        emb.set_footer(text=f"ip: {ip} · serveur dist CT122")
        return emb

    # ------------------------------------------------------------------ veille
    # Nombre max d'IP notifiées par tick : au-delà, un récapitulatif renvoie vers
    # /dist refus. Sans borne, N IP distinctes = N embeds + N appels admin_list en
    # rafale dans #alertes (endpoint public non limité → amplification possible).
    MAX_NOTIFY_PER_TICK = 10

    @tasks.loop(seconds=120)
    async def refus_watch(self):
        cursor = int(self.bot.state.get("dist_refused_cursor") or 0)
        r = await self.api("GET", "admin_refused",
                           params={"since_id": cursor, "limit": 500}, timeout=10)
        if not r or r.get("status") != "ok":
            return                      # serveur dist injoignable : on réessaiera
        rows = r.get("refused") or []
        if not rows:
            return
        agg = self._aggregate(rows)
        new_cursor = max(cursor, *(a["max_id"] for a in agg.values()))

        # ⚠️ Le salon est résolu AVANT d'avancer le curseur. Le curseur ne recule jamais
        # (max()), donc l'avancer puis échouer à poster = lot PERDU à jamais (salon
        # supprimé/mal configuré, cache vidé pendant un re-IDENTIFY). Si des refus sont à
        # signaler mais que le salon est absent ou n'est pas postable, on NE touche PAS
        # au curseur : le même lot sera relu au prochain tick (constat revue 2026-08-12).
        cid = (getattr(self.bot.cfg, "dist_alert_channel_id", 0)
               or self.bot.cfg.alert_channel_id)
        chan = self.bot.get_channel(cid) if cid else None
        postable = chan is not None and hasattr(chan, "send")  # ni None ni catégorie/forum

        now = time.time()
        # UNE lecture de la whitelist pour tout le tick (revue 2026-08-18 : c'était un
        # appel admin_list PAR IP — N+1 sur un endpoint distant). Whitelist illisible =
        # on notifie quand même : rater une alerte coûte plus qu'un faux positif.
        allow = await self.api("GET", "admin_list", timeout=10)
        allowed = ({x.get("ip") for x in (allow.get("allowlist") or [])}
                   if allow and allow.get("status") == "ok" else set())
        to_notify = []
        for ip, info in agg.items():
            if self._suppressed(ip, now):
                continue
            if ip in allowed:
                continue                # whitelistée entre-temps : rien à signaler
            to_notify.append((ip, info))

        if not to_notify:
            # Rien à annoncer (tout supprimé/déjà whitelisté) : on peut avancer sans risque.
            self.bot.state.set("dist_refused_cursor", new_cursor)
            return
        if not postable:
            log.warning("refus dist à signaler (%d IP) mais salon d'alertes absent/non "
                        "postable (cid=%s) — curseur NON avancé, relance au prochain tick",
                        len(to_notify), cid)
            return

        overflow = to_notify[self.MAX_NOTIFY_PER_TICK:]
        to_notify = to_notify[:self.MAX_NOTIFY_PER_TICK]

        sent_ok = True
        for ip, info in to_notify:
            try:
                await chan.send(embed=self._refused_embed(ip, info),
                                view=self._refused_view)
                self._remember_notified([ip], now)   # mémorisé seulement si RÉELLEMENT posté
            except Exception as e:  # noqa: BLE001 — HTTPException, mais aussi 403/AttributeError
                sent_ok = False
                log.warning("notification refus %s impossible: %s", ip, e)
                break                                 # on n'avance pas le curseur ce tick
        if overflow and sent_ok:
            try:
                await chan.send(f"➕ **{len(overflow)}** autre(s) IP refusée(s) ce cycle — "
                                "`/dist refus` pour le détail et l'autorisation.")
            except Exception:  # noqa: BLE001
                pass
        # Curseur avancé UNIQUEMENT si tout le lot a été posté sans erreur. Un échec en
        # cours de route laisse le curseur en place → le reste sera revu au prochain tick
        # (le cooldown _suppressed évite de re-poster les IP déjà annoncées).
        if sent_ok:
            self.bot.state.set("dist_refused_cursor", new_cursor)

    @refus_watch.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ parc
    # Seuils (dérivés de la cadence de phone-home) : une instance qui n'a pas parlé depuis
    # ~2,5× son intervalle attendu est en « silence radio ». Anti-spam : une alerte par
    # instance par PARK_ALERT_COOLDOWN, et une note de RÉTABLISSEMENT quand elle repart.
    PARK_ALERT_COOLDOWN = 6 * 3600

    @staticmethod
    def _fmt_age(seconds):
        if seconds is None:
            return "jamais"
        s = int(seconds)
        if s < 3600:
            return f"{s // 60} min"
        if s < 86400:
            return f"{s // 3600} h"
        return f"{s // 86400} j"

    def _park_status(self, inst):
        """→ (niveau, court_texte). niveau ∈ ok|warn|crit ; sert au tri et aux alertes."""
        cfg = self.bot.cfg
        silence_limit = getattr(cfg, "dist_phone_home_hours", 24) * 3600 * 2.5
        backup_limit = getattr(cfg, "dist_backup_stale_hours", 48) * 3600
        silence = inst.get("silence_seconds")
        if silence is not None and silence > silence_limit:
            return "crit", f"silence radio ({self._fmt_age(silence)})"
        if int(inst.get("revoked") or 0) == 1:
            return "warn", "licence révoquée"
        if inst.get("healthy") == 0:
            return "warn", "santé dégradée"
        bage = inst.get("backup_age_seconds")
        if bage is None or bage > backup_limit:
            return "warn", "sauvegarde vieillissante" if bage else "aucune sauvegarde"
        return "ok", "à jour"

    @staticmethod
    def _park_dot(level):
        return {"ok": "🟢", "warn": "🟠", "crit": "🔴"}.get(level, "⚪")

    def _push_influx(self, instances):
        """Une ligne line-protocol par instance → dashboard Grafana « Parc Fronote »."""
        if not getattr(self.bot, "influx", None) or not self.bot.influx.enabled:
            return None
        lines = []
        for i in instances:
            aid = int(i.get("activation_id") or 0)
            ver = str(i.get("app_version") or "?").replace(" ", "_").replace(",", "_")
            level, _ = self._park_status(i)
            fields = [
                f"healthy={1 if i.get('healthy') else 0}i",
                f"silence_s={int(i.get('silence_seconds') or 0)}i",
                f"ok={1 if level == 'ok' else 0}i",
            ]
            if i.get("backup_age_seconds") is not None:
                fields.append(f"backup_age_s={int(i['backup_age_seconds'])}i")
            if i.get("disk_pct") is not None:
                fields.append(f"disk_pct={int(i['disk_pct'])}i")
            if i.get("tenants_count") is not None:
                fields.append(f"tenants={int(i['tenants_count'])}i")
            if i.get("accounts_count") is not None:
                fields.append(f"accounts={int(i['accounts_count'])}i")
            lines.append(f"fronote_park,instance={aid},version={ver} " + ",".join(fields))
        return "\n".join(lines) if lines else None

    @tasks.loop(seconds=900)
    async def parc_watch(self):
        r = await self.api("GET", "admin_park", params={"limit": 500}, timeout=15)
        if not r or r.get("status") != "ok":
            return
        instances = r.get("instances") or []
        if not instances:
            return

        # (P3) métriques → InfluxDB (best-effort, jamais bloquant)
        lp = self._push_influx(instances)
        if lp:
            try:
                await self.bot.influx.write(lp)
            except Exception as e:  # noqa: BLE001
                log.warning("push influx parc: %s", e)

        # (P2) alerte silence-radio / souffrance, anti-spam par instance
        cid = (getattr(self.bot.cfg, "dist_alert_channel_id", 0)
               or self.bot.cfg.alert_channel_id)
        chan = self.bot.get_channel(cid) if cid else None
        if chan is None or not hasattr(chan, "send"):
            return
        now = time.time()
        alerted = dict(self.bot.state.get("dist_park_alerted") or {})
        changed = False
        for inst in instances:
            aid = str(inst.get("activation_id"))
            level, txt = self._park_status(inst)
            prev = alerted.get(aid) or {}
            prev_level = prev.get("level", "ok")
            if level in ("warn", "crit"):
                # alerter si nouveau problème, aggravation, ou cooldown écoulé
                due = (level != prev_level
                       or now - float(prev.get("ts", 0)) > self.PARK_ALERT_COOLDOWN)
                if due:
                    try:
                        await chan.send(embed=self._park_alert_embed(inst, level, txt))
                        alerted[aid] = {"level": level, "ts": now}
                        changed = True
                    except Exception as e:  # noqa: BLE001
                        log.warning("alerte parc %s impossible: %s", aid, e)
            elif prev_level in ("warn", "crit"):
                # rétablissement
                try:
                    host = inst.get("hostname") or f"instance #{aid}"
                    await chan.send(f"🟢 **{host}** est de nouveau saine "
                                    f"(vue il y a {self._fmt_age(inst.get('silence_seconds'))}).")
                except Exception:  # noqa: BLE001
                    pass
                alerted.pop(aid, None)
                changed = True
        if changed:
            self.bot.state.set("dist_park_alerted", alerted)

    @parc_watch.before_loop
    async def _wait_ready_parc(self):
        await self.bot.wait_until_ready()

    def _park_alert_embed(self, inst, level, txt):
        host = inst.get("hostname") or f"instance #{inst.get('activation_id')}"
        color = 0xE01B24 if level == "crit" else 0xE5A50A
        emb = discord.Embed(
            title=f"{self._park_dot(level)} Instance Fronote — {txt}",
            description=f"**{host}** ({inst.get('app_url') or 'URL ?'})",
            color=color)
        emb.add_field(name="Version", value=f"`{inst.get('app_version') or '?'}`", inline=True)
        emb.add_field(name="Dernier contact",
                      value=f"il y a {self._fmt_age(inst.get('silence_seconds'))}", inline=True)
        emb.add_field(name="Dernière sauvegarde",
                      value=f"il y a {self._fmt_age(inst.get('backup_age_seconds'))}", inline=True)
        note = inst.get("note")
        if note:
            emb.add_field(name="Licence", value=str(note)[:200], inline=False)
        emb.set_footer(text=f"activation #{inst.get('activation_id')} · parc Fronote")
        return emb

    # ------------------------------------------------------------------ commandes
    async def _guard_enabled(self, itx):
        if self.enabled:
            return True
        await itx.response.send_message(
            "⛔ Serveur de distribution non configuré (DIST_URL / DIST_ADMIN_TOKEN).",
            ephemeral=True)
        return False

    @dist.command(name="autoriser", description="Whitelister une IP sur le serveur de distribution Fronote.")
    @app_commands.describe(ip="IP publique du client", note="Mémo (ex. nom de l'établissement)")
    @admin_check(require_admin_channel=False)
    async def autoriser(self, itx: discord.Interaction, ip: str, note: str = ""):
        if not await self._guard_enabled(itx):
            return
        ip = ip.strip()
        if not _valid_ip(ip):
            await itx.response.send_message(f"❌ `{ip}` n'est pas une IP valide.", ephemeral=True)
            return
        await itx.response.defer()
        r = await self.api("POST", "admin_allow", data={"ip": ip, "note": note})
        ok = bool(r and r.get("status") == "ok")
        self.bot.audit.record(user=f"{itx.user} ({itx.user.id})", action="dist-allow",
                              target=ip, result="ok" if ok else "échec")
        if ok:
            await itx.followup.send(
                f"✅ IP `{ip}` whitelistée{f' — {note}' if note else ''}. "
                "Le client peut lancer `bootstrap.sh` avec sa clé.")
        else:
            await itx.followup.send(
                f"❌ Échec : {(r or {}).get('status', 'serveur dist injoignable')}")

    @dist.command(name="retirer", description="Retirer une IP de la whitelist du serveur de distribution.")
    @app_commands.describe(ip="IP à retirer")
    @admin_check(require_admin_channel=False)
    async def retirer(self, itx: discord.Interaction, ip: str):
        if not await self._guard_enabled(itx):
            return
        ip = ip.strip()
        if not _valid_ip(ip):
            await itx.response.send_message(f"❌ `{ip}` n'est pas une IP valide.", ephemeral=True)
            return
        await itx.response.defer()
        r = await self.api("POST", "admin_disallow", data={"ip": ip})
        ok = bool(r and r.get("status") == "ok")
        self.bot.audit.record(user=f"{itx.user} ({itx.user.id})", action="dist-disallow",
                              target=ip, result="ok" if ok else "échec")
        if ok:
            removed = r.get("removed")
            await itx.followup.send(
                f"✅ IP `{ip}` retirée de la whitelist." if removed else
                f"ℹ️ IP `{ip}` n'était pas whitelistée.")
        else:
            await itx.followup.send(
                f"❌ Échec : {(r or {}).get('status', 'serveur dist injoignable')}")

    @dist.command(name="statut", description="Statut d'une IP sur le serveur de distribution (whitelistée ?).")
    @app_commands.describe(ip="IP à consulter")
    @admin_check(require_admin_channel=False)
    async def statut(self, itx: discord.Interaction, ip: str):
        if not await self._guard_enabled(itx):
            return
        ip = ip.strip()
        if not _valid_ip(ip):
            await itx.response.send_message(f"❌ `{ip}` n'est pas une IP valide.", ephemeral=True)
            return
        await itx.response.defer()
        r = await self.api("GET", "admin_list", params={"ip": ip})
        if not r or r.get("status") != "ok":
            await itx.followup.send(
                f"❌ Échec : {(r or {}).get('status', 'serveur dist injoignable')}")
            return
        s = r.get("ip") or {}
        if s.get("allowed"):
            note = f" · note : {s['note']}" if s.get("note") else ""
            await itx.followup.send(
                f"✅ `{ip}` est **whitelistée** (depuis {s.get('added_at')} UTC{note}).")
        else:
            await itx.followup.send(f"🚫 `{ip}` n'est **pas** whitelistée.")

    @dist.command(name="liste", description="Whitelist complète du serveur de distribution.")
    @admin_check(require_admin_channel=False)
    async def liste(self, itx: discord.Interaction):
        if not await self._guard_enabled(itx):
            return
        await itx.response.defer()
        r = await self.api("GET", "admin_list")
        if not r or r.get("status") != "ok":
            await itx.followup.send(
                f"❌ Échec : {(r or {}).get('status', 'serveur dist injoignable')}")
            return
        rows = r.get("allowlist") or []
        emb = discord.Embed(title="📋 Whitelist — serveur de distribution Fronote",
                            description=f"**{len(rows)}** IP autorisée(s).",
                            color=0x26A269)
        lines = [f"`{x.get('ip')}` — {x.get('note') or '(sans note)'} · {x.get('added_at')}"
                 for x in rows[:40]]
        if lines:
            block = "\n".join(lines)
            emb.add_field(name="IP (40 max affichées)", value=block[:1024], inline=False)
        await itx.followup.send(embed=emb)

    @dist.command(name="refus", description="Dernières tentatives d'installation refusées (IP non whitelistées).")
    @admin_check(require_admin_channel=False)
    async def refus(self, itx: discord.Interaction):
        if not await self._guard_enabled(itx):
            return
        await itx.response.defer()
        r = await self.api("GET", "admin_refused",
                           params={"since_id": 0, "limit": 1000}, timeout=10)
        if not r or r.get("status") != "ok":
            await itx.followup.send(
                f"❌ Échec : {(r or {}).get('status', 'serveur dist injoignable')}")
            return
        agg = self._aggregate(r.get("refused") or [])
        if not agg:
            await itx.followup.send("✅ Aucune tentative refusée au journal.")
            return
        # plus récentes d'abord (le journal est chronologique)
        items = sorted(agg.items(), key=lambda kv: kv[1]["max_id"], reverse=True)[:15]
        emb = discord.Embed(
            title="🚫 Tentatives refusées — serveur de distribution",
            description=f"**{len(agg)}** IP distincte(s) au journal (15 max affichées). "
                        "`/dist autoriser <ip>` pour whitelister.",
            color=0xE01B24)
        for ip, info in items:
            kinds = ", ".join(KIND_LABEL.get(k, k) for k in sorted(info["kinds"]))
            emb.add_field(name=f"`{ip}`",
                          value=f"{info['count']} requête(s) ({kinds})\n"
                                f"dernière : {info['last']} UTC",
                          inline=True)
        await itx.followup.send(embed=emb)

    @dist.command(name="licences", description="Licences émises (usage, révocation, expiration).")
    @admin_check(require_admin_channel=False)
    async def licences(self, itx: discord.Interaction):
        if not await self._guard_enabled(itx):
            return
        await itx.response.defer()
        r = await self.api("GET", "admin_licenses", params={"limit": 200})
        if not r or r.get("status") != "ok":
            await itx.followup.send(
                f"❌ Échec : {(r or {}).get('status', 'serveur dist injoignable')}")
            return
        rows = r.get("licenses") or []
        actives = sum(1 for x in rows if not x.get("revoked"))
        emb = discord.Embed(
            title="🔑 Licences — serveur de distribution Fronote",
            description=f"**{len(rows)}** licence(s) émise(s), {actives} active(s). "
                        "20 plus récentes affichées.",
            color=0x3584E4)
        for x in rows[:20]:
            try:
                prem = ", ".join(json.loads(x.get("modules_json") or "[]")) or "aucun"
            except (ValueError, TypeError):
                prem = "?"
            state = ("🚫 révoquée" if x.get("revoked")
                     else f"{x.get('used_count', 0)}/{x.get('max_activations', 1)} utilisée")
            exp = f" · expire {x['expires_at']}" if x.get("expires_at") else ""
            emb.add_field(
                name=f"`{x.get('key_prefix')}…` — {x.get('tier')}",
                value=f"{state}{exp}\npremium : {prem}\n{x.get('note') or '(sans note)'}",
                inline=True)
        await itx.followup.send(embed=emb)

    @dist.command(name="parc", description="État du parc des écoles installées (versions, santé, sauvegardes).")
    @admin_check(require_admin_channel=False)
    async def parc(self, itx: discord.Interaction):
        if not await self._guard_enabled(itx):
            return
        await itx.response.defer()
        r = await self.api("GET", "admin_park", params={"limit": 500})
        if not r or r.get("status") != "ok":
            await itx.followup.send(
                f"❌ Échec : {(r or {}).get('status', 'serveur dist injoignable')}")
            return
        instances = r.get("instances") or []
        if not instances:
            await itx.followup.send(
                "📭 Aucune instance ne s'est encore signalée. Les écoles installées "
                "appellent `/phone-home` une fois par jour (cron) — le parc se remplira "
                "au premier rapport.")
            return
        # pire d'abord (crit > warn > ok), puis par ancienneté de contact
        order = {"crit": 0, "warn": 1, "ok": 2}
        ranked = sorted(
            ((self._park_status(i), i) for i in instances),
            key=lambda t: (order.get(t[0][0], 3), -(t[1].get("silence_seconds") or 0)))
        crit = sum(1 for (lvl, _t), _i in ranked if lvl == "crit")
        warn = sum(1 for (lvl, _t), _i in ranked if lvl == "warn")
        emb = discord.Embed(
            title="🏫 Parc Fronote — écoles installées",
            description=(f"**{len(instances)}** instance(s) · "
                         f"🔴 {crit} · 🟠 {warn} · 🟢 {len(instances) - crit - warn}"),
            color=0xE01B24 if crit else (0xE5A50A if warn else 0x26A269))
        for (level, txt), i in ranked[:20]:
            host = i.get("hostname") or f"instance #{i.get('activation_id')}"
            emb.add_field(
                name=f"{self._park_dot(level)} {host}",
                value=(f"v`{i.get('app_version') or '?'}` · {txt}\n"
                       f"vu il y a {self._fmt_age(i.get('silence_seconds'))} · "
                       f"sauv. {self._fmt_age(i.get('backup_age_seconds'))}"),
                inline=True)
        await itx.followup.send(embed=emb)


async def setup(bot):
    await bot.add_cog(Dist(bot))
