"""Confirmation éphémère verrouillée sur son auteur, pour les actions sensibles."""
import discord

from ..core.gates import GatedView


class ConfirmView(GatedView):
    # La porte de tier est celle de la COMMANDE qui ouvre cette confirmation (admin_check
    # / read_check l'ont déjà appliquée) ; ici on ne garde que la PROPRIÉTÉ du prompt.
    gate = None
    gate_reason = ("confirmation d'une action déjà autorisée par la commande appelante ; "
                   "seule la propriété (gate_user_id) est vérifiée")

    def __init__(self, author_id, timeout=30):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.gate_user_id = author_id
        self.value = None
        self.message = None  # set by caller so on_timeout can grey out the buttons

    async def on_denied(self, interaction, why):
        if why == "user":
            await interaction.response.send_message(
                "Ce n'est pas votre action.", ephemeral=True)
            return
        await super().on_denied(interaction, why)

    def _disable(self):
        for c in self.children:
            c.disabled = True

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, itx: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self._disable()
        await itx.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, itx: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self._disable()
        await itx.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        self.value = False
        self._disable()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
