"""Persistent 'Refresh' button for the live dashboard (survives bot restarts)."""
import logging

import discord

from ..core.permissions import admin_button_ok

log = logging.getLogger("discord-bot.refresh")


class RefreshView(discord.ui.View):
    def __init__(self, bot=None):
        super().__init__(timeout=None)  # persistent
        self.bot = bot

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        # /dashboard est @admin_check() ; sans ceci le bouton offrait la meme
        # charge (6 requetes Flux + rendu matplotlib) a n'importe qui, en rafale.
        # admin_button_ok = rôle Gestion + session 2FA (répond lui-même sur refus).
        return await admin_button_ok(itx)

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
        if file is not None:
            kwargs["attachments"] = [file]
        await itx.message.edit(**kwargs)
