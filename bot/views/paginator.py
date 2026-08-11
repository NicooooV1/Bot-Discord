"""Pagination ◀ ▶ des sorties longues (logs, listes de CT)."""
import discord

from ..core.gates import GatedView


class Paginator(GatedView):
    # Purement présentation : le contenu paginé a DÉJÀ été autorisé et rendu par la
    # commande. Rien de neuf n'est lu au clic — seule la propriété compte.
    gate = None
    gate_reason = ("navigation dans un contenu déjà autorisé et déjà envoyé ; "
                   "aucune lecture nouvelle au clic")

    def __init__(self, pages, author_id, timeout=120):
        super().__init__(timeout=timeout)
        self.gate_user_id = author_id
        self.pages = pages or ["(vide)"]
        self.i = 0
        self.author_id = author_id
        self._sync()

    def _sync(self):
        self.prev.disabled = self.i <= 0
        self.next.disabled = self.i >= len(self.pages) - 1

    def content(self):
        return f"{self.pages[self.i]}\n`page {self.i + 1}/{len(self.pages)}`"

    async def on_denied(self, interaction, why):
        if why == "user":
            await interaction.response.send_message(
                "Ce n'est pas votre pagination.", ephemeral=True)
            return
        await super().on_denied(interaction, why)

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
