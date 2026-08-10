"""Ephemeral, author-locked confirmation prompt for safe actions."""
import discord


class ConfirmView(discord.ui.View):
    def __init__(self, author_id, timeout=30):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value = None
        self.message = None  # set by caller so on_timeout can grey out the buttons

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id != self.author_id:
            await itx.response.send_message("Ce n'est pas votre action.", ephemeral=True)
            return False
        return True

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
