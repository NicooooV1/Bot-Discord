"""Saisie du code 2FA : modale + bouton de déverrouillage.

Le code passe par une **modale** et jamais par un argument de commande : un argument
resterait visible dans l'historique Discord et dans les logs d'interaction.

Module séparé de la cog pour être importable par le tree (__main__) sans import circulaire.
"""
import discord
from discord import app_commands


class NeedsTwoFA(app_commands.CheckFailure):
    """Levée par le tree quand la session de confiance est absente ou expirée.

    On lève une exception plutôt que de renvoyer False + répondre soi-même : un
    `interaction_check` qui renvoie False déclenche de toute façon une CheckFailure,
    et le gestionnaire d'erreurs global enverrait alors un SECOND message par-dessus.
    Ici c'est lui, et lui seul, qui répond.
    """

    def __init__(self, enrolled: bool):
        super().__init__("2FA requis")
        self.enrolled = enrolled


class CodeModal(discord.ui.Modal):
    """Modale générique de saisie d'un code (TOTP 6 chiffres ou code de secours)."""

    code = discord.ui.TextInput(
        label="Code à 6 chiffres (ou code de secours)",
        placeholder="123456",
        min_length=6, max_length=16, required=True,
    )

    def __init__(self, title, on_submit):
        super().__init__(title=title, timeout=120)
        self._cb = on_submit

    async def on_submit(self, itx: discord.Interaction):
        await self._cb(itx, str(self.code.value))


class UnlockView(discord.ui.View):
    """Bouton « Déverrouiller » joint au refus du tree.

    Éphémère et lié à un utilisateur : personne d'autre ne peut s'en servir.
    """

    def __init__(self, owner_id):
        super().__init__(timeout=180)
        self.owner_id = owner_id

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id == self.owner_id:
            return True
        await itx.response.send_message("⛔ Ce panneau n'est pas le tien.", ephemeral=True)
        return False

    @discord.ui.button(label="Déverrouiller", emoji="🔓", style=discord.ButtonStyle.primary)
    async def unlock(self, itx: discord.Interaction, _b: discord.ui.Button):
        tf = itx.client.twofa
        if not tf.enrolled(itx.user.id):
            await itx.response.send_message(
                "🔐 Tu n'es pas encore inscrit au 2FA. Lance **`/2fa setup`**.", ephemeral=True)
            return

        async def done(i: discord.Interaction, code: str):
            if tf.verify(i.user.id, code):
                mins = tf.session_min
                await i.response.send_message(
                    f"✅ Session ouverte pour **{mins} min**. Relance ta commande.",
                    ephemeral=True)
            else:
                await i.response.send_message(
                    "❌ Code invalide ou déjà utilisé. Réessaie.", ephemeral=True)

        await itx.response.send_modal(CodeModal("Déverrouiller Edmine", done))
