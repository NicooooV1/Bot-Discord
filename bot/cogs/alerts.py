"""Proactive alerts on the GAPS Grafana does not cover (RAID/SMART/IPMI/backup/thinpool).

Edge-triggered + state-persisted: a persistent condition pages once; a morning restart
after the nightly power-off does not re-page conditions already firing. Disk/RAM/load/
CT-down stay owned by the existing Grafana -> Discord webhook (no duplication).

The same read-only evaluator (`_evaluate`) feeds both the background loop and the
`/alerts` command so thresholds live in exactly one place.

⚠️ L'état de de-dup vit dans un ESPACE DE NOMS propre à ce cog (`state.ns("alerts")`,
clés rangées « alerts:<clé> ») : le cog servarr écrit dans le sien (« servarr:… ») et les
deux ne peuvent plus se marcher dessus. Ce qui COMPTE les alertes (le tableau de bord,
/health, le pied de page de /alerts) doit en revanche lire l'UNION des espaces, via
`state.alerts_active()`. Voir `core/state.AlertSpace` pour le pourquoi.
"""
import asyncio
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
        self._chan_warned = False
        # Espace de de-dup PROPRE à ce cog. `adopt` reprend les clés nues écrites par la
        # version précédente (migration sans perte : sans elle, toute alerte en cours
        # serait re-postée au premier démarrage) — y compris les périmées, sinon elles
        # resteraient non préfixées dans state.json et continueraient d'apparaître dans
        # /health et le tableau de bord.
        # ⚠️ `_alerts` et PAS `alerts` : discord.py installe la commande d'application
        # sur l'instance sous le nom de sa méthode (`Cog.__new__` -> setattr(self,
        # "alerts", <Command>)). Un attribut `self.alerts` écraserait cette référence —
        # la commande resterait enregistrée (elle vit dans __cog_app_commands__), mais
        # tout `self.alerts.autocomplete(...)` / `.error(...)` ajouté plus tard tomberait
        # sur l'espace d'alertes, avec une AttributeError incompréhensible.
        self._alerts = bot.state.ns("alerts", adopt=self.OWNED_KEYS | self.LEGACY_KEYS)
        # Purge des clés de de-dup que la boucle ne maintient PLUS (OWNED_KEYS a été
        # restreint à {ipmi_temp}) : sinon d'anciens criticals persistés (thinpool/
        # backup…) resteraient des faux positifs permanents dans /health et /alerts.
        # Purger sur « pas dans OWNED_KEYS » est désormais SÛR : cet espace ne contient
        # que nos clés. Avant le cloisonnement, le même test effaçait aussi les 6 clés
        # servarr_* bien vivantes du dict partagé — et comme `alerts` est chargé AVANT
        # `servarr`, celui-ci repartait d'un état vide à chaque démarrage : le tunnel VPN
        # coupé depuis 3 jours était re-pagé à chaque reboot matinal, et une condition
        # résolue pendant l'arrêt ne postait jamais son « ✅ Résolu » (2026-08-11).
        for k in self._alerts.keys():
            if k not in self.OWNED_KEYS:
                self._alerts.clear(k)
        self.loop.change_interval(seconds=bot.cfg.alert_poll_seconds)
        self.loop.start()

    async def cog_load(self):
        # UNE vue persistante, réutilisée pour tous les envois : chaque AlertActionView()
        # passée à send(timeout=None) restait à VIE dans le view-store de discord.py —
        # même fuite lente que celle corrigée dans dist.py le 12/08 (revue 2026-08-18).
        self._action_view = AlertActionView()
        self.bot.add_view(self._action_view)

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
            except Exception as e:
                # Un salon d'alertes injoignable = plus AUCUNE alerte postée : ça ne
                # peut pas rester silencieux. Une seule fois par panne, la boucle
                # repasse ici toutes les 60 s (2026-08-11).
                if not self._chan_warned:
                    self._chan_warned = True
                    log.warning("salon d'alertes %s injoignable (%s) — plus aucune "
                                "alerte ne sera postée tant que ça dure", cid, e)
                return None
        self._chan_warned = False
        return ch

    async def _fire(self, ch, key, level, title, desc):
        """Edge-trigger: post on rising/changed level, post recovery on clear."""
        prev = self._alerts.level(key)
        if level and level != prev:
            if alert_snoozed(self.bot.state, key):
                return   # en sommeil (Snooze) : on ne pague pas
            color = fmt.RED if level == "crit" else fmt.YELLOW
            emb = discord.Embed(title=title, description=desc, color=color)
            emb.set_footer(text=f"alerte: {key} [{level}]")
            await ch.send(embed=emb, view=self._action_view)
            self._alerts.set_level(key, level)
        elif not level and prev:
            # Le Snooze doit aussi taire le retour au vert : sinon le dernier message
            # d'une alerte mise en sommeil part quand même (2026-08-11).
            if not alert_snoozed(self.bot.state, key):
                await ch.send(embed=discord.Embed(
                    title=f"✅ Résolu — {title}", description=desc, color=fmt.GREEN))
            self._alerts.clear(key)

    async def _evaluate(self):
        """Read-only: current level of every owned-gap check.
        Returns list of (key, level, title, desc); level is None when nominal.

        « Read-only » = n'écrit rien ; le niveau d'`ipmi_temp` LIT en revanche le niveau
        persisté pour appliquer son hystérésis (cf. `_hysteresis`), il dépend donc du
        cycle précédent — c'est voulu, et /alerts affiche ainsi exactement ce que la
        boucle a décidé."""
        inf = self.bot.influx
        out = []

        # Les 6 lectures sont indépendantes : groupées, /alerts (interactif) répond au
        # coût de la plus lente au lieu de leur somme.
        tp, bs, (ctrl, _), disks, health, temps = await asyncio.gather(
            inf.thinpool(), inf.backup_summary(), inf.raid(), inf.raid_disks(),
            inf.smart_health(), inf.ipmi_temps())

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

        if ctrl:
            offline = [d.get("slot") for d in disks if not d.get("online")]
            bad = (not ctrl.get("vd_optimal")) or (not ctrl.get("batt_good")) or offline
            desc = (f"VD optimal={bool(ctrl.get('vd_optimal'))} · "
                    f"BBU ok={bool(ctrl.get('batt_good'))}")
            if offline:
                desc += f" · slots offline: {offline}"
            out.append(("raid_health", "crit" if bad else None, "🧱 RAID dégradé", desc))

        if health:
            failed = [dev for dev, h in health if not bool(h)]
            out.append(("smart_fail", "crit" if failed else None, "💽 SMART FAIL",
                        f"disques en échec: {failed or '—'}"))

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
            level = self._hysteresis(mx, warn_t, crit_t,
                                     self._alerts.level("ipmi_temp"))
            # Une décimale : le seuil bas est un seuil d'AMBIANCE (32 °C) et la source
            # est un `last()` brut. Arrondi à l'unité, l'alerte « max 32°C » et sa
            # résolution « Résolu — max 32°C » étaient rigoureusement identiques.
            out.append(("ipmi_temp", level, "🌡️ Température élevée",
                        f"{what} max **{mx:.1f}°C**"))

        return out

    #: marge de retour (°C) : un capteur d'ambiance qui bat autour de son seuil ne doit
    #: pas repasser au vert au premier dixième de degré.
    HYSTERESIS_C = 2.0

    @classmethod
    def _hysteresis(cls, mx, warn_t, crit_t, prev):
        """Niveau AVEC hystérésis asymétrique : on monte aux seuils nominaux, on ne
        redescend qu'une fois `HYSTERESIS_C` degrés SOUS le seuil franchi.

        Sans ça, `_fire` étant purement edge-triggered, une entrée d'air oscillant entre
        31,7 et 32,2 °C en août postait « 🌡️ Température élevée » puis « ✅ Résolu »
        jusqu'à une fois par minute toute la nuit — fatigue d'alerte sur le SEUL canal
        d'alerte encore possédé par le bot (2026-08-11).
        """
        w = warn_t - cls.HYSTERESIS_C if prev in ("warn", "crit") else warn_t
        c = crit_t - cls.HYSTERESIS_C if prev == "crit" else crit_t
        return "crit" if mx >= c else ("warn" if mx >= w else None)

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
        # N'écrire que sur CHANGEMENT : state.set() resérialise tout state.json (mkstemp
        # + json.dump + os.replace), et cette valeur ne bouge presque jamais — c'était
        # ~1440 réécritures complètes par jour pour rien. Le test porte sur l'inégalité,
        # pas sur la hausse : un disque remplacé doit faire REDESCENDRE le baseline,
        # sans quoi la prochaine hausse réelle passerait inaperçue (2026-08-11).
        if last is None or last != cur_max:
            self.bot.state.set("raid_grown_max", cur_max)

    # Grafana est désormais la source UNIQUE des alertes infra (thinpool/backup/RAID/
    # SMART) et log-based -> Discord #alertes avec un template soigné. Le bot ne poste
    # plus que l'IPMI (aucune règle Grafana ne couvre les capteurs IPMI) afin d'éviter
    # tout doublon. La commande /alerts continue d'évaluer TOUT (via _evaluate).
    OWNED_KEYS = {"ipmi_temp"}

    #: Clés que la boucle a POSSÉDÉES par le passé et n'émet plus : elles resteraient
    #: sinon persistées « crit » à vie dans state.json. Depuis le cloisonnement, la purge
    #: de démarrage se fait sur « pas dans OWNED_KEYS » (sûre : l'espace n'est qu'à nous) ;
    #: cette liste sert à REPRENDRE les clés nues d'avant la migration, pour qu'elles
    #: entrent dans notre espace et se fassent purger au lieu de traîner à la racine.
    LEGACY_KEYS = {"thinpool_usage", "backup_age", "raid_health", "smart_fail"}

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
        description="Alertes proactives actives (thinpool/backup/RAID/SMART/température/seedbox).")
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
        # Les conditions servarr (VPN, port forwarding, qBittorrent, user-sync, ratio)
        # sont postées dans le MÊME #alertes et persistées dans le MÊME dict : les
        # omettre ici faisait répondre « ✅ Aucune alerte active » alors que le
        # kill-switch AirVPN venait de couper le trafic (2026-08-11).
        srv = bot.get_cog("Servarr")
        if srv is not None:
            try:
                findings += await srv._evaluate()
            except Exception:
                log.exception("/alerts : évaluation servarr impossible")
                findings.append(("servarr_eval", "warn", "📉 Seedbox",
                                 "évaluation impossible — voir les logs du bot"))
        active = [(k, l, t, d) for (k, l, t, d) in findings if l]
        emb = discord.Embed(
            title="🚨 Alertes actives" if active else "✅ Aucune alerte active",
            color=(fmt.RED if any(l == "crit" for _, l, _, _ in active)
                   else fmt.YELLOW if active else fmt.GREEN))
        if active:
            # 25 champs max par embed : au-delà, Discord rejette TOUT l'envoi.
            for k, l, t, d in active[:24]:
                emb.add_field(name=f"{fmt.level_emoji(l)} {t}"[:256],
                              value=(d or "—")[:1024], inline=False)
            if len(active) > 24:
                emb.add_field(name="…", value=f"{len(active) - 24} alertes supplémentaires",
                              inline=False)
        else:
            watched = ", ".join(t for _, _, t, _ in findings) or "—"
            emb.description = f"Tous les indicateurs surveillés sont au vert.\n{watched}"[:4096]
        # Un ✅ vert alors qu'InfluxDB ne répond plus est un feu vert MENSONGER : toutes
        # les requêtes renvoient [] et chaque contrôle se tait. Le drapeau vient de
        # core/influx (getattr : on ne dépend pas de sa présence).
        if getattr(bot.influx, "blind", False):
            # …mais ne JAMAIS adoucir un bilan déjà rouge : l'aveuglement est une
            # incertitude, pas une bonne nouvelle. Repeindre en jaune un embed portant
            # un « crit » ferait passer une alerte critique pour un avertissement.
            if not any(l == "crit" for _, l, _, _ in active):
                emb.color = fmt.YELLOW
            emb.description = ("⚠️ Dernière requête InfluxDB en ÉCHEC — la supervision "
                               "est aveugle, ce bilan est incomplet.\n"
                               + (emb.description or ""))[:4096]
        # UNION des espaces : l'état persisté appartient aussi à servarr (« servarr:… »),
        # et alerts_active() rend les clés NUES — le pied de page affiche donc exactement
        # les mêmes noms d'alerte qu'avant le cloisonnement.
        persisted = bot.state.alerts_active()
        if persisted:
            emb.set_footer(text=("État persisté: "
                                 + ", ".join(f"{k}[{l}]" for k, l in persisted.items()))[:2048])
        await itx.followup.send(embed=emb, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Alerts(bot))
