"""Boutons Ack/Snooze sur les alertes du bot (temp IPMI, seedbox/VPN…).

La clé de l'alerte est lue dans le footer « alerte: <clé> [<niveau>] » posé par _fire.
L'état de sommeil (alert_snooze = {clé: timestamp_fin}) est persisté dans bot.state ;
_fire n'alerte pas tant que l'alerte est en sommeil.

Note : ne couvre QUE les alertes émises par le bot. Les alertes venues de Grafana
(conteneur down, disque plein, OOM, SSH…) se coupent via les silences Grafana.
"""
import logging
import re
import time

import discord

from ..core.permissions import admin_button_ok

log = logging.getLogger("discord-bot.alertaction")


def alert_snoozed(state, key):
    until = (state.get("alert_snooze") or {}).get(key, 0)
    try:
        return bool(until) and time.time() < float(until)
    except (TypeError, ValueError):
        return False


def _snooze(state, key, seconds):
    d = dict(state.get("alert_snooze") or {})
    d[key] = time.time() + seconds
    state.set("alert_snooze", d)


def _key_from_msg(msg):
    try:
        m = re.search(r"alerte:\s*(\S+)", (msg.embeds[0].footer.text or ""))
        return m.group(1) if m else None
    except (IndexError, AttributeError):
        return None


class AlertActionView(discord.ui.View):
    """Persistante : Snooze 1h / 4h sur un message d'alerte du bot."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _act(self, itx, seconds, label):
        # Sans ce controle, tout membre voyant #alertes pouvait rendre le bot muet
        # pendant 4 h sur ipmi_temp — la seule alerte que Grafana ne couvre PAS.
        if not await admin_button_ok(itx):     # rôle Gestion + session 2FA
            return
        key = _key_from_msg(itx.message)
        if not key:
            await itx.response.send_message("Clé d'alerte introuvable.", ephemeral=True)
            return
        _snooze(itx.client.state, key, seconds)
        log.warning("snooze %s pour %s par %s (%s)", key, label, itx.user, itx.user.id)
        await itx.response.send_message(
            f"🔕 Alerte **{key}** mise en sommeil pour {label}.", ephemeral=True)

    @discord.ui.button(label="Snooze 1 h", emoji="🔕",
                       style=discord.ButtonStyle.secondary, custom_id="alert:snooze1h")
    async def s1(self, itx: discord.Interaction, button: discord.ui.Button):
        await self._act(itx, 3600, "1 heure")

    @discord.ui.button(label="Snooze 4 h", emoji="🔕",
                       style=discord.ButtonStyle.secondary, custom_id="alert:snooze4h")
    async def s4(self, itx: discord.Interaction, button: discord.ui.Button):
        await self._act(itx, 14400, "4 heures")
