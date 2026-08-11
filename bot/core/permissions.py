"""App-command permission gates: guild + channel + role/owner checks.

Access is tied to Discord ROLES (ADMIN_ROLE_IDS / READ_ROLE_IDS): grant the bot to
someone by giving them the role in the server, not by editing a user-id list. ADMIN_IDS
stays as an owner break-glass (always allowed).

⚠️ FAIL-CLOSED partout (corrigé 2026-08-11 — ce docstring annonçait l'inverse) : si
NI ADMIN_IDS NI ADMIN_ROLE_IDS n'est configuré, seul le propriétaire du guild est admin ;
et une clé `server` absente de GESTION_SERVERS refuse au lieu de retomber sur les rôles
globaux. Le propriétaire du guild reste le break-glass permanent : aucune configuration
cassée ne peut verrouiller totalement le bot.

Les BOUTONS ne passent pas par ce module directement : ils déclarent leur tier via
`core.gates.GatedView`, qui appelle `is_admin` / `can_read` / `may_lock` ci-dessous.

The invoking member's roles arrive in the interaction payload, so role checks work
without the privileged GUILD_MEMBERS intent.

Checks never reveal command existence to unauthorized users (CheckFailure -> ephemeral
red embed via the global error handler).
"""
import logging

import discord
from discord import app_commands

log = logging.getLogger("discord-bot.perm")


def _member_role_ids(interaction):
    """Set of role ids the invoker holds (empty if not a guild member)."""
    roles = getattr(interaction.user, "roles", None)
    if not roles:
        return set()
    return {r.id for r in roles}


def is_guild_owner(interaction) -> bool:
    """True si l'utilisateur est le PROPRIÉTAIRE du serveur. Identité sûre et stable
    (owner_id du guild), utilisée comme break-glass permanent : elle survit à une
    config.env cassée sans jamais élargir l'accès à quelqu'un d'autre."""
    g = getattr(interaction, "guild", None)
    return g is not None and getattr(g, "owner_id", None) == interaction.user.id


def is_admin(cfg, interaction, server=None) -> bool:
    """True si l'invocateur peut lancer des actions privilégiées : propriétaire du serveur
    (break-glass), un ADMIN_IDS explicite, ou un rôle ADMIN_ROLE_IDS (= Gestion R820).

    `server` (clé GESTION_SERVERS, ex. « AVEYRON ») restreint le contrôle de rôle aux
    tiers M/O de CE serveur : les boutons d'un salon AVEYRON s'ouvrent aux M/O AVEYRON,
    pas aux M R820 (et réciproquement). Les break-glass (propriétaire, ADMIN_IDS)
    restent valables partout.

    ⚠️ On n'honore PLUS la permission Discord « Administrateur » : le cahier des charges
    veut que SEUL le rôle Gestion (accordé par le propriétaire via /gestion) ouvre les
    actions — sinon un membre à qui on donnerait « Administrateur » pour modérer
    contournerait tout le modèle. Fail-CLOSED si la config est vide : seul le propriétaire
    du serveur reste admin (jamais « tout le monde »)."""
    if is_guild_owner(interaction):
        return True
    if cfg.admin_ids and interaction.user.id in cfg.admin_ids:
        return True
    role_ids = set(cfg.admin_role_ids or ())
    if server:
        srv = (getattr(cfg, "gestion_servers", {}) or {}).get(server)
        if not srv:
            # FAIL-CLOSED (corrigé 2026-08-11). Avant, une clé inconnue laissait
            # `role_ids` sur cfg.admin_role_ids : la restriction par serveur était
            # ANNULÉE en silence et un porteur de « Gestion R820 » pouvait piloter une
            # machine d'Aveyron. La clé n'est pas statique — pve.server_of_name() la
            # fabrique depuis le nœud RÉEL renvoyé par l'API (« AVY-<NODE> ») — donc un
            # nœud ajouté, renommé, ou une entrée GESTION_SERVERS mal formée (rejetée
            # avec un simple warning par Config._parse_gestion_servers) suffisait.
            # Les break-glass ci-dessus restent valables : pas de verrouillage total.
            log.warning("is_admin: serveur %r absent de GESTION_SERVERS -> refus "
                        "(déclarer « %s:G:M:O » pour rendre la main aux M/O)",
                        server, server)
            return False
        role_ids = {srv.get("mod"), srv.get("owner")} - {0, None}
    if role_ids and (_member_role_ids(interaction) & role_ids):
        return True
    return False


def can_read(cfg, interaction) -> bool:
    """True si l'invocateur peut lancer les commandes de LECTURE. Les admins (M/O) toujours ;
    sinon il faut un rôle READ_ROLE_IDS. ⚠️ FAIL-CLOSED : si READ_ROLE_IDS est vide, seuls
    les admins lisent (le tier G = « voir les salons uniquement » ne lance AUCUNE commande —
    sinon il pourrait déclencher /yt, /film… à effet de bord depuis #général)."""
    if is_admin(cfg, interaction):
        return True
    if not cfg.read_role_ids:
        return False
    return bool(_member_role_ids(interaction) & set(cfg.read_role_ids))


# --- 2FA de session (pour les BOUTONS, que le GatedTree du CommandTree ne couvre pas) ---
def session_2fa_ok(interaction) -> bool:
    """True si le 2FA est désactivé OU si la session de l'utilisateur est déverrouillée.

    ⚠️ `GatedTree.interaction_check` ne s'applique qu'aux commandes slash ; les clics de
    BOUTONS (interactions de composants) l'ignorent. Cette porte est donc à poser
    explicitement dans l'`interaction_check` des vues sensibles pour honorer « le 2FA
    protège l'ensemble des usages du bot »."""
    bot = interaction.client
    cfg = getattr(bot, "cfg", None)
    tf = getattr(bot, "twofa", None)
    if tf is None or cfg is None or not getattr(cfg, "twofa_enabled", False):
        return True
    return tf.trusted(interaction.user.id)


async def deny_2fa(interaction) -> None:
    """Réponse standard quand une session 2FA est requise pour un bouton."""
    from ..views.twofa_view import UnlockView
    emb = discord.Embed(
        title="🔐 Vérification requise",
        description=("Ce bouton exige le 2FA.\nDéverrouille ci-dessous, puis "
                     "**reclique**."),
        color=0xE5A50A)
    try:
        await interaction.response.send_message(
            embed=emb, view=UnlockView(interaction.user.id), ephemeral=True)
    except discord.HTTPException:
        pass


async def admin_button_ok(interaction, server=None) -> bool:
    """Porte commune des boutons d'ADMINISTRATION : rôle admin (Gestion <srv>) + session
    2FA. Répond elle-même en cas de refus. À appeler en tête des handlers de bouton.
    `server` (clé GESTION_SERVERS) borne le rôle exigé aux tiers M/O de ce serveur —
    les salons du cluster AVEYRON passent leur clé pour être gardés par LEURS rôles."""
    cfg = interaction.client.cfg
    if not is_admin(cfg, interaction, server=server):
        await interaction.response.send_message(
            "🔒 Action réservée aux gestionnaires (rôle Gestion"
            + (f" {server}" if server else "") + ").", ephemeral=True)
        return False
    if not session_2fa_ok(interaction):
        await deny_2fa(interaction)
        return False
    return True


def may_lock(cfg, interaction) -> bool:
    """True si l'utilisateur peut accéder à la catégorie Lock d'un serveur : propriétaire
    (break-glass), OU porteur du rôle « M <srv> » (modération = tout faire sur le serveur)
    OU « O <srv> » (owner). Le tier « G » (visualiser) n'a PAS accès au Lock."""
    # défense en profondeur : ne jamais accorder sur une interaction d'un autre guild
    if cfg.guild_id and getattr(interaction, "guild_id", None) != cfg.guild_id:
        return False
    if is_guild_owner(interaction):
        return True
    if cfg.admin_ids and interaction.user.id in cfg.admin_ids:
        return True
    rids = _member_role_ids(interaction)
    m = getattr(cfg, "node_mod_role_id", 0)
    o = getattr(cfg, "node_owner_role_id", 0)
    return bool((m and m in rids) or (o and o in rids))


async def lock_button_ok(interaction) -> bool:
    """Porte des boutons de la catégorie Lock (nœud) : session 2FA + tier M ou O (ou
    propriétaire). Répond elle-même en cas de refus."""
    cfg = interaction.client.cfg
    if not may_lock(cfg, interaction):
        await interaction.response.send_message(
            "🔒 Réservé aux rôles **M** ou **O** (ou au propriétaire).", ephemeral=True)
        return False
    if not session_2fa_ok(interaction):
        await deny_2fa(interaction)
        return False
    return True


def admin_check(require_admin_channel=True):
    """Gate admin. `require_admin_channel=True` (défaut) confine la commande au salon
    admin (pour les actions destructives) ; passer False pour les commandes bénignes
    (ex. /setratio) qu'on veut pouvoir lancer depuis n'importe quel salon autorisé."""
    async def predicate(interaction: discord.Interaction) -> bool:
        cfg = interaction.client.cfg
        if cfg.guild_id and interaction.guild_id != cfg.guild_id:
            raise app_commands.CheckFailure("Serveur non autorisé.")
        if require_admin_channel and cfg.admin_channel_id and interaction.channel_id != cfg.admin_channel_id:
            raise app_commands.CheckFailure("Action réservée au salon admin.")
        if not is_admin(cfg, interaction):
            # ⚠️ Ne PAS mentionner la permission Discord « Administrateur » : elle n'est
            # plus honorée depuis 2026-07-16 (seul le rôle Gestion ouvre les actions).
            raise app_commands.CheckFailure(
                "Réservé aux gestionnaires (rôle Gestion accordé via /gestion).")
        return True

    return app_commands.check(predicate)


def read_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        cfg = interaction.client.cfg
        if cfg.guild_id and interaction.guild_id != cfg.guild_id:
            raise app_commands.CheckFailure("Serveur non autorisé.")
        if cfg.read_channel_ids:
            allowed = set(cfg.read_channel_ids)
            if cfg.admin_channel_id:
                allowed.add(cfg.admin_channel_id)
            if interaction.channel_id not in allowed:
                raise app_commands.CheckFailure("Commande non autorisée dans ce salon.")
        if not can_read(cfg, interaction):
            raise app_commands.CheckFailure("Réservé aux membres du rôle autorisé.")
        return True

    return app_commands.check(predicate)


def install_error_handler(bot):
    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error):
        from ..views.twofa_view import NeedsTwoFA, UnlockView

        view = None
        if isinstance(error, NeedsTwoFA):
            # 2FA manquant/expiré : c'est ICI qu'on répond (le tree se contente de lever),
            # sinon deux messages partiraient pour une seule interaction.
            if error.enrolled:
                emb = discord.Embed(
                    title="🔐 Vérification requise",
                    description=("Toutes les commandes du bot demandent le 2FA.\n"
                                 "Déverrouille ci-dessous, puis **relance ta commande**."),
                    color=0xE5A50A)
                view = UnlockView(interaction.user.id)
            else:
                emb = discord.Embed(
                    title="🔐 Inscription 2FA requise",
                    description=("Le bot exige le 2FA et tu n'es pas encore inscrit.\n"
                                 "Lance **`/2fa setup`** (la seule commande accessible sans code)."),
                    color=0xE5A50A)
        elif isinstance(error, app_commands.CheckFailure):
            msg = str(error) or "Action non autorisée."
            emb = discord.Embed(title="⛔ Refusé", description=msg, color=0xED4245)
        else:
            log.exception("command error", exc_info=error)
            # app command bodies raise wrapped in CommandInvokeError -> show the real cause
            real = getattr(error, "original", error)
            emb = discord.Embed(
                title="❌ Erreur",
                description=f"`{type(real).__name__}` — voir les logs du bot.",
                color=0xED4245,
            )
        kw = {"embed": emb, "ephemeral": True}
        if view is not None:
            kw["view"] = view
        try:
            if interaction.response.is_done():
                await interaction.followup.send(**kw)
            else:
                await interaction.response.send_message(**kw)
        except discord.HTTPException:
            pass
