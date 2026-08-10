"""Simple ◀ ▶ paginator for long text outputs (logs, CT lists)."""
import discord


class Paginator(discord.ui.View):
    def __init__(self, pages, author_id, timeout=120):
        super().__init__(timeout=timeout)
        self.pages = pages or ["(vide)"]
        self.i = 0
        self.author_id = author_id
        self._sync()

    def _sync(self):
        self.prev.disabled = self.i <= 0
        self.next.disabled = self.i >= len(self.pages) - 1

    def content(self):
        return f"{self.pages[self.i]}\n`page {self.i + 1}/{len(self.pages)}`"

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id != self.author_id:
            await itx.response.send_message("Ce n'est pas votre pagination.", ephemeral=True)
            return False
        return True

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, itx: discord.Interaction, button: discord.ui.Button):
        self.i = max(0, self.i - 1)
        self._sync()
        await itx.response.edit_message(content=self.content(), view=self)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary)
    async def next(self, itx: discord.Interaction, button: discord.ui.Button):
        self.i = min(len(self.pages) - 1, self.i + 1)
        self._sync()
        await itx.response.edit_message(content=self.content(), view=self)
