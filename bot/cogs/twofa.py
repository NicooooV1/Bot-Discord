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

from ..core.durations import clamp_duration, fmt_duration, parse_duration
from ..core.gates import GatedView
from ..core.permissions import deny_2fa, is_admin, session_2fa_ok
from ..core.twofa import TwoFAStoreError
from ..views.twofa_view import CodeModal

log = logging.getLogger("discord-bot.twofa")

# Description d'embed = 4096 caractères max côté Discord. Au-delà, l'envoi part en 400 et
# PERSONNE ne peut plus s'inscrire (seule porte du 2FA) : on garde de la marge pour le
# texte qui entoure le QR et on retombe sur la saisie manuelle de la clé si besoin.
QR_MAX = 3000


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
    except Exception as e:  # noqa: BLE001 — 2026-08-11 : un aiohttp.ClientError ou un
        # TimeoutError n'est PAS une HTTPException et remontait jusqu'au callback de
        # modale, empêchant l'affichage des codes de secours. Le docstring dit
        # « best-effort » : on tient parole, en journalisant quand même.
        log.warning("2fa: journal #logs-2fa indisponible (%s): %s", event, e)


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
        # Le QR est un CONFORT : s'il est trop gros pour un embed (limite 4096) ou si le
        # rendu échoue, on garde la clé à saisir à la main plutôt que de faire tomber
        # l'inscription — c'est la seule porte d'entrée du 2FA (2026-08-11).
        try:
            bloc = f"```\n{_qr_ascii(uri)}\n```\n"
            if len(bloc) > QR_MAX:
                raise ValueError(f"QR trop volumineux ({len(bloc)} caractères)")
        except Exception as e:  # noqa: BLE001
            log.warning("2fa: QR non rendu (%s) — repli sur la clé manuelle", e)
            bloc = "_(QR indisponible — utilise la clé ci-dessous.)_\n"
        emb = discord.Embed(
            title="🔐 Inscription 2FA",
            description=(
                "**1.** Scanne ce QR avec ton application d'authentification "
                "(Aegis, Google Authenticator, Bitwarden…)\n"
                f"{bloc}"
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

        async def done(i: discord.Interaction, code: str, minutes=None):
            if tf.verify(i.user.id, code, minutes):
                mins = tf.session_min if minutes is None else minutes
                await i.response.send_message(
                    f"✅ Session ouverte pour **{fmt_duration(mins)}**." if mins else
                    "✅ Session ouverte **sans limite de durée** — elle restera ouverte "
                    "jusqu'à `/2fa lock`… ou jusqu'à ce que quelqu'un mette la main sur "
                    "ton Discord. À n'utiliser que sur un appareil que tu contrôles.",
                    ephemeral=True)
                log.info("2fa: session ouverte par %s (%s)", i.user, fmt_duration(mins))
                await _audit_2fa(self.bot, i.user,
                                 f"Déverrouillage (session {fmt_duration(mins)})")
            else:
                await i.response.send_message(
                    "❌ Code invalide ou déjà utilisé.", ephemeral=True)
                log.warning("2fa: code refusé pour %s", i.user)
                await _audit_2fa(self.bot, i.user, "⚠️ Code refusé (déverrouillage)")

        await itx.response.send_modal(CodeModal(
            "Déverrouiller Edmine", done, duration=True,
            default_min=tf.session_min, maximum=self.bot.cfg.twofa_session_max_min))

    # ------------------------------------------------------------------ durée
    @grp.command(name="duree",
                 description="Changer la durée par défaut des sessions 2FA (ex: 2h, illimité).")
    @app_commands.describe(duree="minutes (45), heures (2h, 1h30) ou « illimité ». "
                                 "Vide = afficher le réglage courant.")
    async def duree(self, itx: discord.Interaction, duree: str = None):
        """Réglage GLOBAL de la durée des prochaines sessions.

        ⚠️ Porte explicite. La famille `/2fa` est exemptée de 2FA (TWOFA_EXEMPT) parce
        qu'elle est la seule porte d'entrée — mais ALLONGER la durée des sessions est un
        acte d'administration : sans le contrôle ci-dessous, n'importe quel membre du
        serveur, non inscrit et sans session, pourrait passer toutes les sessions du bot
        en « illimité ». On exige donc admin **et** une session de confiance ouverte.
        """
        tf = self.bot.twofa
        if not is_admin(self.bot.cfg, itx):
            await itx.response.send_message(
                "⛔ Réservé à l'administrateur du bot.", ephemeral=True)
            return
        if duree is None:
            await itx.response.send_message(
                f"⏱️ Durée par défaut des sessions 2FA : **{fmt_duration(tf.session_min)}** "
                f"(plafond {fmt_duration(self.bot.cfg.twofa_session_max_min)}, "
                f"valeur de config.env {fmt_duration(tf.default_session_min)}).",
                ephemeral=True)
            return
        if not session_2fa_ok(itx):
            await deny_2fa(itx)
            return
        try:
            wanted = parse_duration(duree)
        except ValueError:
            await itx.response.send_message(
                f"⏱️ Durée invalide : `{duree[:30]}`. Attendu `45`, `2h`, `1h30` "
                "ou `illimité`.", ephemeral=True)
            return
        applied = clamp_duration(wanted, tf.session_min,
                                 self.bot.cfg.twofa_session_max_min)
        tf.set_session_min(applied)
        note = ("" if applied == wanted else
                f" (demandé {fmt_duration(wanted)}, ramené au plafond "
                f"`TWOFA_SESSION_MAX_MIN`)")
        extra = ("\n⚠️ **Sans limite** : les prochaines sessions — et les rôles qu'elles "
                 "portent (« 2FA Complet », « O … ») — ne se fermeront plus d'elles-mêmes."
                 if not applied else "")
        await itx.response.send_message(
            f"⏱️ Durée par défaut des sessions 2FA : **{fmt_duration(applied)}**{note}. "
            "Les sessions déjà ouvertes gardent leur échéance." + extra, ephemeral=True)
        log.warning("2fa: duree des sessions passee a %s par %s",
                    fmt_duration(applied), itx.user)
        await _audit_2fa(self.bot, itx.user,
                         f"Durée des sessions -> {fmt_duration(applied)}")

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
            # -1 = session SANS limite (cf. TwoFA.session_left) : à ne pas confondre avec
            # 0 (« pas de session »), que le même champ affichait avant.
            if left < 0:
                sess_txt = "🔓 **illimitée** — `/2fa lock` pour la fermer"
            elif left:
                sess_txt = f"🔓 {left // 60} min {left % 60} s restantes"
            else:
                sess_txt = "🔒 fermée — `/2fa unlock`"
            emb.add_field(name="Session", value=sess_txt, inline=True)
            emb.add_field(name="Durée par défaut",
                          value=fmt_duration(tf.session_min) + " (`/2fa duree`)",
                          inline=True)
            n = tf.backup_left(itx.user.id)
            emb.add_field(name="Codes de secours",
                          value=f"{n}/8 restants" + (" ⚠️ pense à te réinscrire" if n <= 2 else ""),
                          inline=False)
        await itx.response.send_message(embed=emb, ephemeral=True)

    # ------------------------------------------------------------------ lock
    @grp.command(name="lock", description="Fermer tout de suite ta session de confiance.")
    async def lock(self, itx: discord.Interaction):
        """Contrepartie indispensable des sessions illimitées (2026-08-14) : sans elle,
        une session ouverte « sans limite » ne se refermait qu'au prochain redémarrage du
        bot. Aucune porte : fermer SA PROPRE session ne retire que des droits."""
        tf = self.bot.twofa
        if not tf.trusted(itx.user.id):
            await itx.response.send_message("🔒 Aucune session ouverte.", ephemeral=True)
            return
        tf.close_session(itx.user.id)      # retire aussi les rôles de session (on_change)
        await itx.response.send_message(
            "🔒 Session fermée. `/2fa unlock` pour en rouvrir une.", ephemeral=True)
        log.info("2fa: session fermee a la demande de %s", itx.user)
        await _audit_2fa(self.bot, itx.user, "Verrouillage (session fermée)")

    # ------------------------------------------------------------------ disable
    @grp.command(name="disable", description="Se désinscrire du 2FA (code requis).")
    async def disable(self, itx: discord.Interaction):
        tf = self.bot.twofa
        if not tf.enrolled(itx.user.id):
            await itx.response.send_message("Tu n'es pas inscrit.", ephemeral=True)
            return

        async def done(i: discord.Interaction, code: str, minutes=None):
            # `minutes` ignoré : cette modale n'a pas de champ durée (duration=False) et
            # une désinscription n'ouvre aucune session. Le paramètre n'est là que pour
            # respecter la signature de rappel commune de CodeModal.
            if not tf.verify(i.user.id, code):
                await i.response.send_message("❌ Code invalide ou déjà utilisé.",
                                              ephemeral=True)
                return
            try:
                tf.revoke(i.user.id)
            except TwoFAStoreError as e:
                # Désinscription ANNULÉE côté magasin : le dire, sinon l'utilisateur
                # croirait son 2FA retiré alors qu'il revient au prochain redémarrage.
                log.error("2fa: desinscription de %s NON enregistrée: %s", i.user, e)
                await i.response.send_message(
                    "❌ Désinscription **non enregistrée** (erreur d'écriture côté bot) — "
                    "ton 2FA reste actif. Préviens Nico.", ephemeral=True)
                return
            msg = "🔓 2FA désactivé pour ton compte."
            if self.bot.cfg.twofa_enabled:
                msg += ("\n⚠️ Le bot exige toujours le 2FA : sans inscription tu ne pourras "
                        "plus lancer **aucune** commande. Refais `/2fa setup` tout de suite.")
            # RÉPONDRE D'ABORD (budget de 3 s d'une modale), journaliser ensuite : le
            # ch.send() de _audit_2fa est un appel réseau qui, lent, ferait expirer le
            # jeton d'interaction alors que la désinscription a bien eu lieu. Même ordre
            # que `unlock` (2026-08-11).
            await i.response.send_message(msg, ephemeral=True)
            log.warning("2fa: desinscription de %s", i.user)
            await _audit_2fa(self.bot, i.user, "Désinscription")

        await itx.response.send_modal(CodeModal("Confirmer la désinscription", done))


class _ConfirmView(GatedView):
    """Bouton « Confirmer » de l'inscription.

    gate=None ASSUMÉ : c'est la porte d'entrée du 2FA elle-même (comme la famille `/2fa`,
    exemptée par TWOFA_EXEMPT). Exiger une session 2FA ici rendrait toute PREMIÈRE
    inscription impossible — la clé serait enfermée à l'intérieur. La vue reste bornée à
    son destinataire par `gate_user_id` (message éphémère, mais un id d'interaction fuité
    ne doit pas permettre à un tiers de confirmer l'inscription à sa place)."""

    gate = None
    gate_reason = ("bouton d'inscription au 2FA : exiger une session 2FA ici rendrait "
                   "toute première inscription impossible (poule et œuf)")

    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.gate_user_id = owner_id

    @discord.ui.button(label="Confirmer", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, itx: discord.Interaction, _b: discord.ui.Button):
        tf = itx.client.twofa

        async def done(i: discord.Interaction, code: str, minutes=None):
            try:                        # `minutes` : cf. CodeModal (aucun champ durée ici)
                backup = tf.confirm_enroll(i.user.id, code)
            except TwoFAStoreError as e:
                # NE PAS dire « code invalide » : l'utilisateur irait régler l'heure de son
                # téléphone pour une panne d'écriture. L'inscription a été annulée côté
                # magasin, le QR reste valable pour un nouveau clic (2026-08-11).
                log.error("2fa: inscription de %s NON enregistrée: %s", i.user, e)
                await i.response.send_message(
                    "❌ Inscription **non enregistrée** (erreur d'écriture côté bot). "
                    "Réessaie dans un instant ; si ça persiste, préviens Nico.",
                    ephemeral=True)
                return
            if backup is None:
                await i.response.send_message(
                    "❌ Code invalide ou déjà utilisé. Vérifie l'heure de ton téléphone "
                    "puis relance `/2fa setup`.", ephemeral=True)
                return
            log.info("2fa: inscription confirmee pour %s", i.user)
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
            # RÉPONDRE D'ABORD, auditer ensuite : les codes ne sont rendus qu'UNE fois et
            # ne sont pas restituables. Un ch.send() lent avant la réponse ferait expirer
            # le jeton (3 s) et les perdrait définitivement. Repli en MP si l'envoi
            # échoue quand même — c'est la seule copie (2026-08-11).
            try:
                await i.response.send_message(embed=emb, ephemeral=True)
            except discord.HTTPException as e:
                log.warning("2fa: embed des codes de secours non envoyé (%s) — repli MP", e)
                try:
                    await i.user.send(embed=emb)
                except discord.HTTPException:
                    log.error("2fa: codes de secours PERDUS pour %s (MP fermés)", i.user)
            await _audit_2fa(i.client, i.user, "Inscription confirmée")

        await itx.response.send_modal(CodeModal("Confirmer l'inscription", done))


async def setup(bot):
    await bot.add_cog(TwoFACog(bot))
