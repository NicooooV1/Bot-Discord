"""Cog 2FA — inscription, déverrouillage, état, désinscription.

Choix de Nico (2026-07-16) : le 2FA barre **toutes** les commandes. Ce groupe `/2fa` est
donc la SEULE porte toujours ouverte (voir TWOFA_EXEMPT dans __main__) — sans exemption,
il serait impossible de s'inscrire, et la clé resterait enfermée à l'intérieur.

Le QR est rendu en **ASCII** dans un bloc de code : pas de fichier image à héberger, donc
le secret ne transite pas par le CDN de Discord où il resterait accessible par URL.
"""
import io
import logging
import time

import discord
import qrcode
from discord import app_commands
from discord.ext import commands

from ..views.twofa_view import CodeModal

log = logging.getLogger("discord-bot.twofa")


def _qr_ascii(uri):
    q = qrcode.QRCode(border=1)
    q.add_data(uri)
    q.make(fit=True)
    b = io.StringIO()
    q.print_ascii(out=b, invert=True)
    return b.getvalue()


async def _audit_2fa(bot, user, event):
    """Journalise un événement 2FA dans #logs-2fa (privé). Best-effort — un journal
    indisponible ne doit jamais casser l'inscription/déverrouillage."""
    cid = bot.state.get("twofa_log_channel_id")
    if not cid:
        return
    ch = bot.get_channel(cid)
    if ch is None:
        return
    try:
        await ch.send(f"🔐 **{event}** — {user} (`{user.id}`) · <t:{int(time.time())}:R>",
                      allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


class TwoFACog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    grp = app_commands.Group(name="2fa", description="Double authentification du bot")

    # ------------------------------------------------------------------ setup
    @grp.command(name="setup", description="S'inscrire au 2FA (QR à scanner).")
    async def setup(self, itx: discord.Interaction):
        tf = self.bot.twofa
        if tf.enrolled(itx.user.id):
            await itx.response.send_message(
                "✅ Tu es déjà inscrit. `/2fa status` pour l'état, "
                "`/2fa disable` pour te désinscrire.", ephemeral=True)
            return

        label = f"{itx.user.name}"
        secret, uri = tf.begin_enroll(itx.user.id, label)
        emb = discord.Embed(
            title="🔐 Inscription 2FA",
            description=(
                "**1.** Scanne ce QR avec ton application d'authentification "
                "(Aegis, Google Authenticator, Bitwarden…)\n"
                f"```\n{_qr_ascii(uri)}\n```\n"
                "**2.** Si le QR ne passe pas, saisis la clé à la main :\n"
                f"||`{secret}`||\n\n"
                "**3.** Clique sur **Confirmer** et entre le code affiché."),
            color=0x5865F2)
        emb.set_footer(text="Ce message n'est visible que par toi. "
                            "Rien n'est enregistré tant que tu n'as pas confirmé.")
        await itx.response.send_message(embed=emb, view=_ConfirmView(itx.user.id),
                                        ephemeral=True)

    # ------------------------------------------------------------------ unlock
    @grp.command(name="unlock", description="Ouvrir une session de confiance avec ton code.")
    async def unlock(self, itx: discord.Interaction):
        tf = self.bot.twofa
        if not tf.enrolled(itx.user.id):
            await itx.response.send_message(
                "🔐 Pas encore inscrit — lance **`/2fa setup`**.", ephemeral=True)
            return

        async def done(i: discord.Interaction, code: str):
            if tf.verify(i.user.id, code):
                await i.response.send_message(
                    f"✅ Session ouverte pour **{tf.session_min} min**.", ephemeral=True)
                log.info("2fa: session ouverte par %s", i.user)
                await _audit_2fa(self.bot, i.user, "Déverrouillage (session ouverte)")
            else:
                await i.response.send_message(
                    "❌ Code invalide ou déjà utilisé.", ephemeral=True)
                log.warning("2fa: code refusé pour %s", i.user)
                await _audit_2fa(self.bot, i.user, "⚠️ Code refusé (déverrouillage)")

        await itx.response.send_modal(CodeModal("Déverrouiller Edmine", done))

    # ------------------------------------------------------------------ status
    @grp.command(name="status", description="État de ton 2FA et de ta session.")
    async def status(self, itx: discord.Interaction):
        tf = self.bot.twofa
        on = self.bot.cfg.twofa_enabled
        emb = discord.Embed(title="🔐 2FA", color=0x5865F2)
        emb.add_field(name="Exigé par le bot",
                      value="✅ oui — toutes les commandes" if on
                      else "⚠️ non (`TWOFA_ENABLED=false`)", inline=False)
        if not tf.enrolled(itx.user.id):
            emb.add_field(name="Toi", value="❌ non inscrit — `/2fa setup`", inline=False)
        else:
            left = tf.session_left(itx.user.id)
            emb.add_field(name="Toi", value="✅ inscrit", inline=True)
            emb.add_field(name="Session",
                          value=f"🔓 {left // 60} min {left % 60} s restantes" if left
                          else "🔒 fermée — `/2fa unlock`", inline=True)
            n = tf.backup_left(itx.user.id)
            emb.add_field(name="Codes de secours",
                          value=f"{n}/8 restants" + (" ⚠️ pense à te réinscrire" if n <= 2 else ""),
                          inline=False)
        await itx.response.send_message(embed=emb, ephemeral=True)

    # ------------------------------------------------------------------ disable
    @grp.command(name="disable", description="Se désinscrire du 2FA (code requis).")
    async def disable(self, itx: discord.Interaction):
        tf = self.bot.twofa
        if not tf.enrolled(itx.user.id):
            await itx.response.send_message("Tu n'es pas inscrit.", ephemeral=True)
            return

        async def done(i: discord.Interaction, code: str):
            if not tf.verify(i.user.id, code):
                await i.response.send_message("❌ Code invalide.", ephemeral=True)
                return
            tf.revoke(i.user.id)
            log.warning("2fa: desinscription de %s", i.user)
            await _audit_2fa(self.bot, i.user, "Désinscription")
            msg = "🔓 2FA désactivé pour ton compte."
            if self.bot.cfg.twofa_enabled:
                msg += ("\n⚠️ Le bot exige toujours le 2FA : sans inscription tu ne pourras "
                        "plus lancer **aucune** commande. Refais `/2fa setup` tout de suite.")
            await i.response.send_message(msg, ephemeral=True)

        await itx.response.send_modal(CodeModal("Confirmer la désinscription", done))


class _ConfirmView(discord.ui.View):
    """Bouton « Confirmer » de l'inscription."""

    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = owner_id

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        return itx.user.id == self.owner_id

    @discord.ui.button(label="Confirmer", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, itx: discord.Interaction, _b: discord.ui.Button):
        tf = itx.client.twofa

        async def done(i: discord.Interaction, code: str):
            backup = tf.confirm_enroll(i.user.id, code)
            if backup is None:
                await i.response.send_message(
                    "❌ Code invalide. Vérifie l'heure de ton téléphone puis relance `/2fa setup`.",
                    ephemeral=True)
                return
            log.info("2fa: inscription confirmee pour %s", i.user)
            await _audit_2fa(i.client, i.user, "Inscription confirmée")
            emb = discord.Embed(
                title="✅ 2FA activé",
                description=(
                    "**Note ces codes de secours MAINTENANT** — ils ne seront plus jamais "
                    "affichés, et ce sont eux qui te sauveront si tu perds ton téléphone.\n\n"
                    "```\n" + "\n".join(backup) + "\n```\n"
                    "Chacun ne sert **qu'une fois**. Garde-les hors du téléphone "
                    "(papier, ou Vaultwarden)."),
                color=0x2ECC71)
            emb.set_footer(text="En dernier recours : TWOFA_ENABLED=false dans "
                                "/opt/discord-bot/config.env + redémarrage du bot.")
            await i.response.send_message(embed=emb, ephemeral=True)

        await itx.response.send_modal(CodeModal("Confirmer l'inscription", done))


async def setup(bot):
    await bot.add_cog(TwoFACog(bot))
