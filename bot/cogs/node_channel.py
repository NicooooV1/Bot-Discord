"""Salon live de l'HYPERVISEUR (2026-07-15) — pendant de ct_channels.py pour le nœud.

Même principe que les salons d'invités : un message épinglé, réécrit sur place toutes les
`DASHBOARD_INTERVAL_MIN` minutes, avec des boutons persistants. Le salon vit dans la
catégorie « 🔒 Lock » provisionnée par provision.py, visible des tiers **M** et **O** du
nœud et des `node_terminal_owner_ids` (cf. provision._lock_overwrites) — pas du seul
propriétaire.

Différences volontaires avec un salon d'invité :
  - PAS de Start/Stop/Reboot : on ne coupe pas la machine qui héberge tout le reste
    depuis un bouton Discord (demande explicite) ;
  - 💾 Sauvegarder = vzdump de TOUS les invités (le pendant nœud du bouton par invité) ;
  - 🖥️ Terminal = shell root SSH sur l'hôte (cf. core/nodeshell.py) ;
  - portes des boutons (règle du 2026-07-16, en-tête corrigée le 2026-08-11 : elle
    annonçait « propriétaire uniquement » pour les trois, ce que le code n'a jamais
    fait) : 🔄 et 💾 = tier **M** ou **O** du serveur du nœud + session 2FA, via la
    porte « owner » de GatedView (= `may_lock`) ; 🖥️ Terminal ajoute par-dessus une
    vérification propre à Terminal._may_open_node, qui exclut M et n'accepte que
    `node_terminal_owner_ids` ou le rôle O. La porte est ici et pas seulement dans les
    permissions du salon : un membre « Administrateur » de Discord voit le salon malgré
    les overwrites.

Les valeurs vitales (uptime/CPU/charge/RAM/swap/rootfs) viennent de l'API PVE, seule
source qui les ait pour le nœud : dans InfluxDB, `object=="nodes"` ne porte que `uptime`
(vérifié 2026-07-15) — d'où l'absence de « Pics sur 24 h » ici, contrairement aux
invités. InfluxDB ne sert qu'au matériel (IPMI/RAID/SMART/stockages/sauvegardes) et
chaque bloc dégrade proprement s'il manque.
"""
import asyncio
import logging
import time

import discord
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.gates import GatedView
from ..views.confirm import ConfirmView

log = logging.getLogger("discord-bot.nodechannel")


def _f(v):
    """-> float ou None (les valeurs Influx arrivent parfois en str/None)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _temp_flag(v, warn=45, crit=60):
    v = _f(v)
    if v is None:
        return ""
    return " 🔥" if v >= crit else (" ⚠️" if v >= warn else "")


class NodeChannel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self._last_rename = 0.0
        # Non-réentrance du bouton 💾 : la garde `_backup_running()` était évaluée AVANT
        # une confirmation qui dure jusqu'à 30 s, et rien ne resérialisait ensuite —
        # deux M pouvaient soumettre deux vzdump complets (le second attend le lockfile
        # de vzdump puis REJOUE tout). Le verrou ne borne que ce processus : le vrai
        # garde-fou reste côté PVE (2026-08-11).
        self._backup_lock = asyncio.Lock()
        # renseigné par provision._rewire() dès que le salon est résolu/créé
        self.channel_id = (bot.state.get("prov", {}) or {}).get("node")
        if self.channel_id and self.enabled:
            self.refresh.change_interval(minutes=bot.cfg.dashboard_interval_min)
            self.refresh.start()

    @property
    def enabled(self):
        return bool(getattr(self.cfg, "node_channel_enabled", True))

    async def cog_load(self):
        self.bot.add_view(NodeControlView(self))

    def cog_unload(self):
        self.refresh.cancel()

    # --------------------------------------------------------------- collecte

    async def _influx(self, fn, default=None, *a, **kw):
        """Un bloc matériel absent ne doit pas vider tout l'embed.

        `fn` est la MÉTHODE, pas la coroutine déjà construite : avec un Influx désactivé
        on sortait sans l'attendre ni la fermer -> « coroutine was never awaited » à chaque
        cycle. L'appel paresseux supprime la fuite."""
        if not self.bot.influx.enabled:
            return default
        try:
            return await fn(*a, **kw)
        except Exception:
            log.debug("bloc influx indisponible", exc_info=True)
            return default

    async def _guests(self):
        try:
            res = await self.bot.pve.aresources()
        except Exception:
            log.debug("liste des invités indisponible", exc_info=True)
            return None
        run = sum(1 for r in res if r.get("status") == "running")
        lxc = sum(1 for r in res if r.get("type") == "lxc")
        qemu = sum(1 for r in res if r.get("type") == "qemu")
        return {"running": run, "total": len(res), "lxc": lxc, "qemu": qemu}

    async def _backup_running(self):
        """Vrai si un vzdump tourne. Cf. pve.running_vzdump_vmids : il FAUT source='active'
        (le défaut 'archive' ne liste que les tâches finies) et une comparaison insensible
        à la casse ('RUNNING'). Sans ça, la garde anti-double-sauvegarde et l'emoji 🟠
        étaient toujours inactifs."""
        try:
            return bool(await self.bot.pve.acall(self.bot.pve.running_vzdump_vmids))
        except Exception:
            log.debug("état vzdump indisponible", exc_info=True)
            return False

    # ------------------------------------------------------------------ embed

    async def build_node(self):
        node = self.cfg.pve_node
        emb = discord.Embed(title=f"🖥️ {node} — hyperviseur")
        emb.timestamp = discord.utils.utcnow()
        worst = 0.0                       # pire taux de remplissage -> couleur de l'embed

        st = None
        if self.bot.pve.enabled:
            try:
                st = await self.bot.pve.acall(self.bot.pve.host_status)
            except Exception:
                log.warning("host_status indisponible", exc_info=True)
        if not st:
            emb.color = fmt.RED
            emb.description = "🔴 **API Proxmox injoignable** — état du nœud inconnu."
            emb.set_footer(text="rafraîchi")
            return emb

        ci = st.get("cpuinfo") or {}
        pv = st.get("pveversion") or "?"
        emb.description = (f"🟢 `online` · **{pv}** · {ci.get('model', 'CPU ?')}")
        emb.add_field(name="Uptime", value=fmt.humanize_duration(st.get("uptime")))

        cpu_pct = (_f(st.get("cpu")) or 0.0) * 100
        emb.add_field(name="CPU", value=f"{cpu_pct:.0f} % · {ci.get('cpus', '?')} threads "
                                       f"({ci.get('sockets', '?')} sockets)")
        la = st.get("loadavg") or []
        if la:
            # charge rapportée au nombre de threads : « 2.4 » ne veut rien dire seul
            l1 = _f(la[0])
            cpus = _f(ci.get("cpus")) or 0
            suff = f" ({l1 / cpus * 100:.0f} % de {int(cpus)} threads)" if (l1 and cpus) else ""
            emb.add_field(name="Charge", value=" · ".join(str(x) for x in la[:3]) + suff)

        mem = st.get("memory") or {}
        if mem.get("total"):
            worst = max(worst, (mem.get("used") or 0) / mem["total"] * 100)
            emb.add_field(name="RAM", value=fmt.pct_of(mem.get("used"), mem.get("total")))
        sw = st.get("swap") or {}
        if sw.get("total"):
            emb.add_field(name="Swap", value=fmt.pct_of(sw.get("used"), sw.get("total")))
        rfs = st.get("rootfs") or {}
        if rfs.get("total"):
            worst = max(worst, (rfs.get("used") or 0) / rfs["total"] * 100)
            emb.add_field(name="Disque /", value=fmt.pct_of(rfs.get("used"), rfs.get("total")))

        g = await self._guests()
        if g:
            emb.add_field(name="VM/conteneurs",
                          value=f"🟢 **{g['running']}** / {g['total']} démarrés · "
                                f"{g['lxc']} LXC · {g['qemu']} VM")

        # ---- stockages (Influx)
        stors = await self._influx(self.bot.influx.storages, [])
        if stors:
            lines = []
            for s in stors[:6]:
                pct = _f(s.get("pct")) or 0.0
                worst = max(worst, pct)
                mark = "🔥" if pct >= 90 else ("⚠️" if pct >= 80 else "•")
                lines.append(f"{mark} `{s.get('name')}` {pct:.0f} % · "
                             f"{fmt.humanize_bytes(s.get('used'))} / "
                             f"{fmt.humanize_bytes(s.get('total'))}")
            emb.add_field(name="Stockages", value="\n".join(lines)[:1024], inline=False)

        # ---- matériel (Influx) : températures / ventilateurs / conso
        temps = await self._influx(self.bot.influx.ipmi_temps, [])
        temps = [(n, _f(v)) for n, v in (temps or []) if _f(v) is not None]
        if temps:
            hot = max(temps, key=lambda x: x[1])
            named = {n: v for n, v in temps}
            bits = []
            for lbl, key in (("entrée", "Inlet Temp"), ("sortie", "Exhaust Temp")):
                if key in named:
                    bits.append(f"{lbl} {named[key]:.0f} °C{_temp_flag(named[key])}")
            bits.append(f"max {hot[1]:.0f} °C{_temp_flag(hot[1])}")
            emb.add_field(name="Températures", value=" · ".join(bits))
            # La température ne pilote PAS la couleur de l'embed : les seuils 45/60 de
            # /temps sont plats alors que l'IPMI en donne un par capteur (entrée 42/47,
            # sortie 70/75, CPU 85/90). Un CPU à 51 °C — parfaitement normal — rendrait
            # l'embed jaune en permanence, et une couleur toujours jaune ne signale plus
            # rien. Les marqueurs ⚠️/🔥 restent (même convention que /temps) et une vraie
            # surchauffe est déjà couverte par l'alerte ipmi_temp du cog alerts.

        fans = await self._influx(self.bot.influx.ipmi_fans, [])
        fans = [_f(v) for _, v in (fans or []) if _f(v) is not None]
        if fans:
            emb.add_field(name="Ventilateurs",
                          value=f"{len(fans)} · {min(fans):.0f}–{max(fans):.0f} RPM")

        power = await self._influx(self.bot.influx.ipmi_power, [])
        power = [_f(v) for _, v in (power or []) if _f(v) is not None]
        if power:
            val = f"{max(power):.0f} W"
            cost = await self._influx(self.bot.influx.power_cost, None)
            if cost:
                eur = _f(cost.get("eur_month"))
                if eur:
                    val += f" · ~{eur:.0f} €/mois"
            emb.add_field(name="Consommation", value=val)

        # ---- RAID / SMART (Influx)
        raid = await self._influx(self.bot.influx.raid, (None, None))
        ctrl, summ = raid if raid else (None, None)
        if ctrl or summ:
            bits = []
            if ctrl:
                bits.append("VD optimal ✅" if ctrl.get("vd_optimal") else "VD **DÉGRADÉ** 🔥")
                bits.append("BBU ok ✅" if ctrl.get("batt_good") else "BBU **KO** ⚠️")
                if not ctrl.get("vd_optimal"):
                    worst = max(worst, 95)
            if summ:
                bits.append(f"{int(_f(summ.get('disks_online')) or 0)}/"
                            f"{int(_f(summ.get('disks_total')) or 0)} disques online")
            emb.add_field(name="RAID (PERC H710)", value=" · ".join(bits), inline=False)

        health = await self._influx(self.bot.influx.smart_health, [])
        if health:
            bad = [d for d, h in health if not h]
            grown = dict(await self._influx(self.bot.influx.smart_grown, []) or [])
            worst_gd = max((int(_f(v) or 0) for v in grown.values()), default=0)
            txt = (f"❌ **{len(bad)} disque(s) en échec** : {', '.join(bad[:4])}" if bad
                   else f"✅ {len(health)} disque(s) PASS")
            if worst_gd:
                txt += f" · pire réallocation : {worst_gd} secteurs"
            if bad:
                worst = max(worst, 95)
            emb.add_field(name="SMART", value=txt[:1024], inline=False)

        # ---- sauvegardes (Influx)
        bs = await self._influx(self.bot.influx.backup_summary, None)
        if bs:
            bits = [f"{int(_f(bs.get('snapshots_total')) or 0)} snapshots"]
            oa = _f(bs.get("oldest_age_seconds"))
            if oa is not None:
                bits.append(f"plus ancienne : {fmt.humanize_duration(oa)}")
            gwb = int(_f(bs.get("guests_without_backup")) or 0)
            bits.append(f"⚠️ {gwb} VM/conteneur(s) sans sauvegarde" if gwb else "✅ tous sauvegardés")
            df = _f(bs.get("dedup_factor"))
            if df:
                bits.append(f"dédup ×{df:.1f}")
            ru, dt = _f(bs.get("real_used_bytes")), _f(bs.get("datastore_total_bytes"))
            if ru and dt:
                bits.append(f"datastore {fmt.pct_of(ru, dt)}")
            emb.add_field(name="Sauvegardes (PBS)", value=" · ".join(bits)[:1024], inline=False)

        # ---- noyau. Pas de compteur de mises à jour APT : GET /nodes/{node}/apt/update
        # exige Sys.Modify (vérifié : 403 avec le token PVEAuditor du bot), une permission
        # bien trop large à accorder pour un simple compteur. Le shell root l'affiche.
        emb.add_field(
            name="Système",
            # `kversion` peut exister ET valoir None : le défaut de .get() ne suffit pas
            value=(f"noyau `{(st.get('kversion') or '?').split('#')[0].strip()[:60]}` · "
                   f"démarrage {(st.get('boot-info') or {}).get('mode', '?')}")[:1024],
            inline=False)

        emb.color = fmt.health_color(worst)
        # Le pied de page répétait « propriétaire uniquement » — la même phrase périmée que
        # celle corrigée dans l'en-tête du module, sauf que celle-ci est LUE par les
        # utilisateurs. 🔄/💾 acceptent le tier M ; seul 🖥️ est O/node_terminal_owner_ids
        # (relecture 2026-08-11).
        emb.set_footer(text="rafraîchi · boutons réservés aux rôles M/O du nœud "
                            "(🖥️ : O / propriétaire)")
        return emb

    # ------------------------------------------------------------ boucle live

    async def _sync_emoji(self, ch, backup_running, reachable):
        emoji = "🟠" if backup_running else ("🟢" if reachable else "🔴")
        desired = f"{emoji}-{fmt.slug(self.cfg.pve_node)}"
        if ch.name == desired:
            return
        # Garde-fou local : Discord limite à 2 renommages / 10 min PAR SALON, et discord.py
        # ne LÈVE PAS sur un 429 — il DORT jusqu'à expiration. Un `except HTTPException`
        # n'attrape donc rien : la boucle entière serait figée plusieurs minutes. On
        # s'auto-limite pour ne jamais atteindre le 429.
        now = time.monotonic()
        if now - self._last_rename < 300:
            return
        self._last_rename = now
        try:
            await ch.edit(name=desired, reason="sync statut du nœud")
        except discord.HTTPException:
            log.warning("renommage du salon du nœud refusé", exc_info=True)

    @tasks.loop(minutes=2)
    async def refresh(self):
        if not (self.channel_id and self.enabled):
            return
        ch = self.bot.get_channel(self.channel_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(self.channel_id)
            except Exception:
                log.warning("salon du nœud %s introuvable", self.channel_id)
                return
        try:
            emb = await self.build_node()
        except Exception:
            log.exception("build_node")
            return
        reachable = not (emb.description or "").startswith("🔴")

        mid = self.bot.state.get("node_message")
        msg = None
        if mid:
            try:
                msg = await ch.fetch_message(mid)
            except discord.NotFound:
                msg = None
            except discord.HTTPException:
                return
        try:
            if msg is None:
                msg = await ch.send(embed=emb, view=NodeControlView(self))
                try:
                    await msg.pin()
                except discord.HTTPException:
                    pass
                self.bot.state.set("node_message", msg.id)
            else:
                await msg.edit(embed=emb, view=NodeControlView(self))
        except discord.HTTPException:
            return
        # renommage APRÈS l'embed : l'état live ne doit jamais attendre le nom du salon
        await self._sync_emoji(ch, await self._backup_running(), reachable)

    @refresh.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


class NodeControlView(GatedView):
    """Boutons persistants du salon de l'hyperviseur. Rafraîchir / Sauvegarder / Terminal.
    Volontairement SANS Start/Stop/Reboot.

    Porte « owner » = `may_lock` + session 2FA, exactement ce que faisait l'ancien
    `lock_button_ok` appelé en tête de chaque bouton : tier M ou O du nœud, ou
    propriétaire break-glass (2026-07-16)."""

    gate = "owner"

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, itx) -> bool:
        if not await super().interaction_check(itx):     # rôle M/O + 2FA
            return False
        # `node_channel_enabled` doit être un interrupteur RUNTIME : la vue est
        # persistante, donc les boutons d'un message déjà posté survivent à un
        # redémarrage et resteraient cliquables si le drapeau n'était lu qu'au
        # provisioning.
        if not self.cog.enabled:
            await itx.response.send_message(
                "Salon du nœud désactivé (`NODE_CHANNEL_ENABLED=false`).", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="nodechannel:refresh")
    async def b_refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        await itx.response.defer()
        try:
            emb = await self.cog.build_node()
            await itx.message.edit(embed=emb, view=self)
        except discord.HTTPException:
            log.warning("rafraîchissement du salon du nœud impossible", exc_info=True)

    @discord.ui.button(label="Sauvegarder", emoji="💾",
                       style=discord.ButtonStyle.secondary, custom_id="nodechannel:backup")
    async def b_backup(self, itx: discord.Interaction, button: discord.ui.Button):
        bot = self.cog.bot
        if not getattr(self.cog.cfg, "node_backup_enabled", True):
            await itx.response.send_message(
                "Sauvegarde désactivée (`NODE_BACKUP_ENABLED=false`).", ephemeral=True)
            return
        if not bot.pve.actions_enabled:
            await itx.response.send_message(
                "Token d'action PVE non configuré (`PVE_ACTION_TOKEN_SECRET`).",
                ephemeral=True)
            return
        # ACK avant toute I/O PVE (3 s max sinon « interaction failed »)
        await itx.response.defer(ephemeral=True)
        # Non-réentrance : on refuse tout de suite plutôt que de faire patienter un second
        # opérateur les 30 s du ConfirmView. Le verrou couvre confirmation ET soumission,
        # sinon la garde `_backup_running()` d'avant la confirmation ne vaut plus rien au
        # moment du POST (2026-08-11).
        if self.cog._backup_lock.locked():
            await itx.followup.send(
                "💾 Une demande de sauvegarde est **en cours de confirmation** par "
                "quelqu'un d'autre — réessaie dans une minute.", ephemeral=True)
            return
        async with self.cog._backup_lock:
            if await self.cog._backup_running():
                await itx.followup.send(
                    "💾 Une sauvegarde est **déjà en cours** — attends la fin.",
                    ephemeral=True)
                return
            cv = ConfirmView(itx.user.id)
            emb = discord.Embed(
                title="⚠️ Confirmation",
                description=(f"Lancer une sauvegarde de **toutes les VM/conteneurs** vers "
                             f"`{self.cog.cfg.pve_pbs_storage}` ?\n\nJob long (plusieurs "
                             f"dizaines de minutes) qui charge le RAID et le réseau."),
                color=fmt.YELLOW)
            cv.message = await itx.followup.send(embed=emb, view=cv, ephemeral=True,
                                                 wait=True)
            await cv.wait()
            if not cv.value:
                await itx.followup.send("Annulé.", ephemeral=True)
                return
            # re-test après confirmation : best-effort (le job planifié pbs-daily-cts,
            # lui, ne passe pas par ce verrou) mais il rattrape le cas courant.
            if await self.cog._backup_running():
                await itx.followup.send(
                    "💾 Une sauvegarde a démarré entre-temps — annulé.", ephemeral=True)
                return
            who = f"{itx.user}({itx.user.id})"
            try:
                upid = await bot.pve.acall(bot.pve.backup_all)
            except Exception as e:
                bot.audit.record(user=who, action="backup",
                                 target="toutes les VM/conteneurs", result=f"error:{e}")
                await itx.followup.send(f"❌ Échec : `{e}`", ephemeral=True)
                return
            bot.audit.record(user=who, action="backup", target="toutes les VM/conteneurs",
                             result="submitted", upid=str(upid))
        await itx.followup.send(
            "💾 Sauvegarde de **toutes les VM/conteneurs** lancée (suivi dans les tâches PVE "
            "et #sauvegardes).", ephemeral=True)

    @discord.ui.button(label="Terminal", emoji="🖥️",
                       style=discord.ButtonStyle.danger, custom_id="nodechannel:terminal")
    async def b_terminal(self, itx: discord.Interaction, button: discord.ui.Button):
        # la porte de la vue laisse passer M ; Terminal._may_open_node referme derrière
        # (node_terminal_owner_ids ou rôle O uniquement) — c'est voulu.
        cog = self.cog.bot.get_cog("Terminal")
        if cog is None:
            await itx.response.send_message("Terminal non activé.", ephemeral=True)
            return
        await cog.open_node_for(itx)


async def setup(bot):
    await bot.add_cog(NodeChannel(bot))
