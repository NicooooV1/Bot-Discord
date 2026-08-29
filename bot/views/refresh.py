"""Persistent 'Refresh' button for the live dashboard (survives bot restarts)."""
import logging

import discord

from ..core.gates import GatedView

log = logging.getLogger("discord-bot.refresh")


class RefreshView(GatedView):
    """Bouton « Rafraîchir » persistant du tableau de bord.

    Tier **mod** : /dashboard est @admin_check(), et le bouton offre la MÊME charge
    (6 requêtes Flux + un rendu matplotlib) — sans porte, n'importe qui pouvait la
    déclencher en rafale."""

    gate = "mod"
    gate_cap = "refresh"

    def __init__(self, bot=None):
        super().__init__(timeout=None)  # persistante
        self.bot = bot

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="dash:refresh")
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        await itx.response.defer()
        cog = itx.client.get_cog("Dashboard")
        if cog is None:
            await itx.followup.send("Module dashboard indisponible.", ephemeral=True)
            return
        try:
            embed, file = await cog.build_dashboard()
        except Exception:
            log.exception("dashboard refresh failed")
            await itx.followup.send("Échec du rafraîchissement.", ephemeral=True)
            return
        kwargs = {"embed": embed, "view": self}
        # Les DEUX sites doivent poser attachments : sans la liste VIDE quand le rendu
        # a échoué, Discord CONSERVE l'ancien dash.png et le bouton 🔄 réaffiche un
        # graphe périmé sous des champs à « — » (2026-08-11).
        kwargs["attachments"] = [file] if file is not None else []
        await itx.message.edit(**kwargs)
