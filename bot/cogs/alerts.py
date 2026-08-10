"""Proactive alerts on the GAPS Grafana does not cover (RAID/SMART/IPMI/backup/thinpool).

Edge-triggered + state-persisted: a persistent condition pages once; a morning restart
after the nightly power-off does not re-page conditions already firing. Disk/RAM/load/
CT-down stay owned by the existing Grafana -> Discord webhook (no duplication).

The same read-only evaluator (`_evaluate`) feeds both the background loop and the
`/alerts` command so thresholds live in exactly one place.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.permissions import read_check
from ..views.alertaction import AlertActionView, alert_snoozed

log = logging.getLogger("discord-bot.alerts")


class Alerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Purge des clés de de-dup que la boucle ne maintient plus (OWNED_KEYS a été
        # restreint à {ipmi_temp}) : sinon d'anciens criticals persistés (thinpool/
        # backup…) resteraient des faux positifs permanents dans /health et /alerts.
        for k in list((bot.state.get("alerts", {}) or {}).keys()):
            if k not in self.OWNED_KEYS:
                bot.state.clear_alert(k)
        self.loop.change_interval(seconds=bot.cfg.alert_poll_seconds)
        self.loop.start()

    async def cog_load(self):
        # boutons Snooze persistants sur les alertes du bot
        self.bot.add_view(AlertActionView())

    def cog_unload(self):
        self.loop.cancel()

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
        """Edge-trigger: post on rising/changed level, post recovery on clear."""
        prev = self.bot.state.alert_level(key)
        if level and level != prev:
            if alert_snoozed(self.bot.state, key):
                return   # en sommeil (Snooze) : on ne pague pas
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
        """Read-only: current level of every owned-gap check.
        Returns list of (key, level, title, desc); level is None when nominal."""
        inf = self.bot.influx
        out = []

        tp = await inf.thinpool()
        if tp and tp.get("pool_bytes"):
            # ALARME sur l'USAGE RÉEL (données/pool), PAS sur la surallocation : un thinpool
            # surprovisionné à 193 % est NORMAL en thin provisioning ; le danger (corruption)
            # n'arrive que si les DONNÉES ÉCRITES saturent le pool. Alarmer sur l'overcommit
            # = fausse alerte permanente.
            dp = tp.get("data_percent")
            if dp is None:
                du, pb = tp.get("data_used_bytes"), tp.get("pool_bytes")
                dp = (du / pb * 100) if (du and pb) else 0.0
            dp = float(dp)
            level = "crit" if dp >= 90 else ("warn" if dp >= 85 else None)
            out.append(("thinpool_usage", level, "🗄️ Thinpool presque plein",
                        f"local-lvm usage réel **{dp:.0f}%** (un pool plein corrompt les volumes)"))

        bs = await inf.backup_summary()
        if bs:
            age = bs.get("oldest_age_seconds")
            nob = int(bs.get("guests_without_backup", 0))
            level = None
            if nob > 0:
                level = "warn"
            if age is not None and age >= 180000:            # 50 h
                level = "crit"
            elif age is not None and age >= 108000 and level != "crit":  # 30 h
                level = "warn"
            out.append(("backup_age", level, "🛟 Sauvegardes en retard",
                        f"plus ancienne: {fmt.humanize_duration(age)} · sans backup: {nob}"))

        ctrl, _ = await inf.raid()
        disks = await inf.raid_disks()
        if ctrl:
            offline = [d.get("slot") for d in disks if not d.get("online")]
            bad = (not ctrl.get("vd_optimal")) or (not ctrl.get("batt_good")) or offline
            desc = (f"VD optimal={bool(ctrl.get('vd_optimal'))} · "
                    f"BBU ok={bool(ctrl.get('batt_good'))}")
            if offline:
                desc += f" · slots offline: {offline}"
            out.append(("raid_health", "crit" if bad else None, "🧱 RAID dégradé", desc))

        health = await inf.smart_health()
        if health:
            failed = [dev for dev, h in health if not bool(h)]
            out.append(("smart_fail", "crit" if failed else None, "💽 SMART FAIL",
                        f"disques en échec: {failed or '—'}"))

        temps = await inf.ipmi_temps()
        vals = [(n, v) for n, v in temps if v is not None]
        if vals:
            # The ambient *inlet* sensor is the environmental-health signal; CPU/planar
            # ("Temp") and "Exhaust Temp" normally run 45-55 C and must not trip an
            # ambient alarm. Fall back to a high ceiling on the hottest sensor if there
            # is no inlet sensor.
            inlet = [v for n, v in vals if "inlet" in str(n).lower()]
            if inlet:
                mx = max(inlet)
                what, warn_t, crit_t = "entrée d'air", 32.0, 40.0
            else:
                mx = max(v for _, v in vals)
                what, warn_t, crit_t = "capteur IPMI", 75.0, 85.0
            level = "crit" if mx >= crit_t else ("warn" if mx >= warn_t else None)
            out.append(("ipmi_temp", level, "🌡️ Température élevée",
                        f"{what} max **{mx:.0f}°C**"))

        return out

    async def _check_raid_grown(self, ch):
        """Grown-defects rising delta — one-shot info, not an edge-state alert."""
        disks = await self.bot.influx.raid_disks()
        if not disks:
            return
        cur_max = max(int(d.get("grown_defects", 0) or 0) for d in disks)
        last = self.bot.state.get("raid_grown_max")
        if last is not None and cur_max > last:
            await ch.send(embed=discord.Embed(
                title="⚠️ RAID — grown defects en hausse",
                description=f"max grown defects {last} → **{cur_max}** ; "
                            "surveiller / remplacer le disque concerné.",
                color=fmt.YELLOW))
        self.bot.state.set("raid_grown_max", cur_max)

    # Grafana est désormais la source UNIQUE des alertes infra (thinpool/backup/RAID/
    # SMART) et log-based -> Discord #alertes avec un template soigné. Le bot ne poste
    # plus que l'IPMI (aucune règle Grafana ne couvre les capteurs IPMI) afin d'éviter
    # tout doublon. La commande /alerts continue d'évaluer TOUT (via _evaluate).
    OWNED_KEYS = {"ipmi_temp"}

    @tasks.loop(seconds=60)
    async def loop(self):
        bot = self.bot
        if not bot.influx.enabled:
            return
        ch = await self._channel()
        if ch is None:
            return
        try:
            for key, level, title, desc in await self._evaluate():
                if key in self.OWNED_KEYS:
                    await self._fire(ch, key, level, title, desc)
            # Détecteur de tendance des grown defects RAID (info one-shot, hors OWNED_KEYS).
            # Ne se déclenche que sur une vraie hausse : au 1er passage le baseline est
            # None -> pas de fausse alerte, on ne fait qu'amorcer raid_grown_max.
            await self._check_raid_grown(ch)
        except Exception:
            log.exception("alert loop iteration failed")

    @loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        description="Alertes proactives actives (thinpool/backup/RAID/SMART/température).")
    @read_check()
    async def alerts(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        bot = self.bot
        if not bot.influx.enabled:
            await itx.followup.send(
                "InfluxDB non configuré (`INFLUX_TOKEN`) — alertes indisponibles.",
                ephemeral=True)
            return
        findings = await self._evaluate()
        active = [(k, l, t, d) for (k, l, t, d) in findings if l]
        emb = discord.Embed(
            title="🚨 Alertes actives" if active else "✅ Aucune alerte active",
            color=(fmt.RED if any(l == "crit" for _, l, _, _ in active)
                   else fmt.YELLOW if active else fmt.GREEN))
        if active:
            for k, l, t, d in active:
                emb.add_field(name=f"{fmt.level_emoji(l)} {t}", value=d, inline=False)
        else:
            watched = ", ".join(t for _, _, t, _ in findings) or "—"
            emb.description = f"Tous les indicateurs surveillés sont au vert.\n{watched}"
        persisted = {k: v.get("level")
                     for k, v in (bot.state.get("alerts", {}) or {}).items()
                     if v.get("level") and k in self.OWNED_KEYS}
        if persisted:
            emb.set_footer(text="État persisté: "
                                + ", ".join(f"{k}[{l}]" for k, l in persisted.items()))
        await itx.followup.send(embed=emb, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Alerts(bot))
