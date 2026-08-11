"""Saisie du code 2FA : modale + bouton de déverrouillage.

Le code passe par une **modale** et jamais par un argument de commande : un argument
resterait visible dans l'historique Discord et dans les logs d'interaction.

Module séparé de la cog pour être importable par le tree (__main__) sans import circulaire.
"""
import discord
from discord import app_commands

from ..core.gates import GatedView


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


class CodeModal(GatedView, discord.ui.Modal):
    """Modale générique de saisie d'un code (TOTP 6 chiffres ou code de secours).

    Exemption assumée : c'est la saisie du 2FA elle-même. La barrer derrière une session
    2FA enfermerait la clé à l'intérieur — plus personne ne pourrait déverrouiller."""

    gate = None
    gate_reason = ("saisie du code 2FA : la barrer derrière une session 2FA rendrait tout "
                   "déverrouillage impossible (poule et œuf)")

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


class UnlockView(GatedView):
    """Bouton « Déverrouiller » joint au refus du tree.

    Exemption assumée : c'est le bouton de déverrouillage 2FA — exiger une session 2FA
    pour y accéder serait circulaire. La PROPRIÉTÉ reste vérifiée (gate_user_id) :
    éphémère et lié à un utilisateur, personne d'autre ne peut s'en servir."""

    gate = None
    gate_reason = ("c'est le bouton de déverrouillage 2FA lui-même ; exiger une session "
                   "2FA pour y accéder serait circulaire")

    def __init__(self, owner_id):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.gate_user_id = owner_id

    async def on_denied(self, interaction, why):
        if why == "user":
            await interaction.response.send_message(
                "⛔ Ce panneau n'est pas le tien.", ephemeral=True)
            return
        await super().on_denied(interaction, why)

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
