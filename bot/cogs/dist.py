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
        """Grise les boutons et appose le badge sur le message d'origine."""
        for c in self.children:
            c.disabled = True
        try:
            emb = itx.message.embeds[0]
            emb.add_field(name="Décision", value=badge, inline=False)
            await itx.message.edit(embed=emb, view=self)
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
        cog.mark_ignored(ip)
        itx.client.audit.record(user=f"{itx.user} ({itx.user.id})", action="dist-ignore",
                                target=ip, result="ok")
        await self._finish(itx, f"🔕 Ignorée par {itx.user.display_name} (24 h)")
        await itx.response.send_message(
            f"🔕 `{ip}` ignorée : plus de notification pendant 24 h "
            "(ses tentatives restent refusées et journalisées).", ephemeral=True)


class Dist(commands.Cog):
    """Whitelist du serveur de distribution Fronote + veille des refus."""

    dist = app_commands.Group(name="dist",
                              description="Serveur de distribution Fronote (whitelist d'IP)")

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(RefusedActionView(self))
        guard_cog_loops(self)
        if self.enabled:
            secs = getattr(self.bot.cfg, "dist_poll_seconds", 120)
            self.refus_watch.change_interval(seconds=secs)
            self.refus_watch.start()
        else:
            log.warning("DIST_URL/DIST_ADMIN_TOKEN absents : /dist et la veille des "
                        "refus sont inactifs")

    async def cog_unload(self):
        self.refus_watch.cancel()

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
        self.bot.state.set("dist_refused_cursor",
                           max(cursor, *(a["max_id"] for a in agg.values())))
        now = time.time()
        to_notify = []
        for ip, info in agg.items():
            if self._suppressed(ip, now):
                continue
            st = await self.api("GET", "admin_list", params={"ip": ip}, timeout=10)
            if st and st.get("status") == "ok" and (st.get("ip") or {}).get("allowed"):
                continue                # whitelistée entre-temps : rien à signaler
            to_notify.append((ip, info))
        if not to_notify:
            return
        cid = (getattr(self.bot.cfg, "dist_alert_channel_id", 0)
               or self.bot.cfg.alert_channel_id)
        chan = self.bot.get_channel(cid) if cid else None
        if chan is None:
            log.warning("refus dist à signaler mais aucun salon d'alertes configuré")
            return
        self._remember_notified([ip for ip, _ in to_notify], now)
        for ip, info in to_notify:
            try:
                await chan.send(embed=self._refused_embed(ip, info),
                                view=RefusedActionView(self))
            except discord.HTTPException as e:
                log.warning("notification refus %s impossible: %s", ip, e)

    @refus_watch.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

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


async def setup(bot):
    await bot.add_cog(Dist(bot))
