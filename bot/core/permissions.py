"""Portes d'autorisation des commandes slash : guild + serveur + rôle/tier + capacité.

MODÈLE (Nico 2026-07-16, durci le 2026-08-29)
---------------------------------------------
Chaque SERVEUR relié au bot (clé GESTION_SERVERS : R820, AVY-NAS, AVY-LLM, AVY-MS01,
SYNO…) est TOTALEMENT INDÉPENDANT des autres, même si plusieurs portent le nom
« Aveyron ». Trois rôles Discord par serveur : G (Gestion = voir), M (Modérateur =
agir), O (Owner = tout + délégation). Un membre n'agit sur un serveur QUE s'il porte le
rôle M/O de CE serveur. Deux break-glass survivent à toute configuration cassée : le
propriétaire du guild et ADMIN_IDS.

⚠️ AUDIT 2026-08-29 — ce que ce module faisait avant et qui est CORRIGÉ ici :
`is_admin(cfg, itx)` sans `server=` honorait `ADMIN_ROLE_IDS`, un rôle GLOBAL qui, en
production, était l'union des M/O de R820 + AVY-NAS + AVY-LLM. Un « M R820 » pouvait
donc `/ctctl stop` une VM d'Aveyron et un « M AVY-NAS » couper Vaultwarden sur le R820.
Désormais :
  - `server=None` signifie « le serveur de CETTE instance » (SERVER_KEY = R820) et
    consulte les rôles M/O de cette clé dans GESTION_SERVERS — plus jamais un rôle global ;
  - `admin_check` / `read_check` résolvent le serveur du SALON d'où part la commande
    (core/channels.server_of_channel) et exigent le rôle de CE serveur ; les commandes
    qui décrivent structurellement le R820 (/docker, /dns, /sso…) refusent en plus
    d'être lancées depuis un salon d'un autre serveur (scope="primary") ;
  - les commandes qui nomment une cible (/ctctl, /backup…) vérifient ensuite, dans leur
    corps, que la cible appartient bien au serveur du salon (ui.guard_target) ;
  - le confinement « salon admin » devient PAR SERVEUR : la catégorie « 🔒 Lock <srv> »
    du serveur visé (visible des seuls M/O de ce serveur) ;
  - chaque refus est JOURNALISÉ (log + audit.log), sans quoi une tentative d'abus
    restait invisible.

Les BOUTONS ne passent pas par ce module directement : ils déclarent leur tier via
`core.gates.GatedView`, qui appelle `is_admin` / `can_read` / `may_lock` / `cap_ok`.

Les CAPACITÉS fines (start/stop/terminal/…) par tier et par serveur, réglables par
l'Owner de chaque serveur via `/gestion perms`, vivent dans core/srvperms.

The invoking member's roles arrive in the interaction payload, so role checks work
without the privileged GUILD_MEMBERS intent.

Checks never reveal command existence to unauthorized users (CheckFailure -> ephemeral
red embed via the global error handler).
"""
import logging

import discord
from discord import app_commands

from . import srvperms

log = logging.getLogger("discord-bot.perm")

SCOPES = ("primary", "channel")


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


def is_breakglass(cfg, interaction) -> bool:
    """Propriétaire du guild ou ADMIN_IDS : jamais restreints, sur aucun serveur."""
    if is_guild_owner(interaction):
        return True
    return bool(cfg.admin_ids and interaction.user.id in cfg.admin_ids)


def _primary_key(cfg):
    return getattr(cfg, "server_key", "R820") or "R820"


def server_roles(cfg, server=None):
    """{view, mod, owner} de la clé `server` (None = serveur de cette instance), ou None
    si la clé n'est pas déclarée dans GESTION_SERVERS.

    Rétrocompatibilité : une instance SANS GESTION_SERVERS (config d'avant le modèle
    G/M/O) garde ADMIN_ROLE_IDS comme rôles M du serveur primaire — et uniquement lui."""
    key = server or _primary_key(cfg)
    srv = (getattr(cfg, "gestion_servers", {}) or {}).get(key)
    if srv:
        return {"view": srv.get("view", 0) or 0, "mod": srv.get("mod", 0) or 0,
                "owner": srv.get("owner", 0) or 0}
    if key == _primary_key(cfg) and cfg.admin_role_ids \
            and not (getattr(cfg, "gestion_servers", {}) or {}):
        return {"view": 0, "mod": 0, "owner": 0, "_legacy": list(cfg.admin_role_ids)}
    return None


def tier_of(cfg, interaction, server=None):
    """Tier de l'invocateur SUR CE SERVEUR : « O », « M », « G » ou None.
    Break-glass (propriétaire, ADMIN_IDS) = « O » partout."""
    if is_breakglass(cfg, interaction):
        return "O"
    roles = server_roles(cfg, server)
    if roles is None:
        if server and server not in (getattr(cfg, "gestion_servers", {}) or {}):
            # clé inconnue : fabriquée par pve.server_of_name() depuis un nœud RÉEL
            # (« AVY-<NODE> ») — nœud ajouté/renommé, entrée mal formée… FAIL-CLOSED.
            log.warning("tier_of: serveur %r absent de GESTION_SERVERS -> refus "
                        "(déclarer « %s:G:M:O » pour rendre la main aux M/O)",
                        server, server)
        return None
    rids = _member_role_ids(interaction)
    if roles.get("_legacy") and rids & set(roles["_legacy"]):
        return "M"
    if roles.get("owner") and roles["owner"] in rids:
        return "O"
    if roles.get("mod") and roles["mod"] in rids:
        return "M"
    if roles.get("view") and roles["view"] in rids:
        return "G"
    return None


def is_admin(cfg, interaction, server=None) -> bool:
    """True si l'invocateur peut lancer des ACTIONS sur `server` : propriétaire du guild
    (break-glass), ADMIN_IDS, ou porteur du rôle M ou O de CE serveur.

    `server=None` = serveur de CETTE instance (SERVER_KEY, « R820 »). ⚠️ Ce n'est PLUS
    « n'importe quel rôle de ADMIN_ROLE_IDS » (audit 2026-08-29) : les M/O d'Aveyron ne
    sont admins que dans les salons d'Aveyron, ceux du R820 que sur le R820.

    ⚠️ On n'honore PAS la permission Discord « Administrateur » : le cahier des charges
    veut que SEUL le rôle M/O du serveur (accordé par son Owner via /gestion) ouvre les
    actions. Fail-CLOSED sur une clé inconnue ou une config vide : seuls les break-glass
    restent admins (jamais « tout le monde »)."""
    return tier_of(cfg, interaction, server) in ("O", "M")


def can_read(cfg, interaction, server=None) -> bool:
    """True si l'invocateur peut lancer les commandes de LECTURE sur `server` : M/O du
    serveur toujours ; G du serveur si son Owner lui a ouvert la capacité « read »
    (/gestion perms) ; sinon un rôle READ_ROLE_IDS (legacy, global). ⚠️ FAIL-CLOSED :
    READ_ROLE_IDS vide + pas de rôle sur ce serveur = refus."""
    tier = tier_of(cfg, interaction, server)
    if tier in ("O", "M"):
        return True
    if tier == "G":
        state = getattr(getattr(interaction, "client", None), "state", None)
        if srvperms.cap_allowed(state, server or _primary_key(cfg), "G", "read"):
            return True
    if not cfg.read_role_ids:
        return False
    return bool(_member_role_ids(interaction) & set(cfg.read_role_ids))


def _caps_tuple(cap):
    """Normalise `cap` : None, "stop" ou ("start", "stop") -> None ou tuple."""
    if cap is None:
        return None
    if isinstance(cap, str):
        return (cap,)
    return tuple(cap)


def cap_ok(cfg, interaction, server=None, cap=None) -> bool:
    """True si l'invocateur dispose d'AU MOINS UNE des capacités `cap` sur `server`
    (cf. srvperms). `cap=None` = pas de capacité particulière exigée. Break-glass et
    tier O = tout ; G comme M : ce que l'Owner du serveur a accordé à ce niveau."""
    caps = _caps_tuple(cap)
    if caps is None:
        return True
    tier = tier_of(cfg, interaction, server)
    if tier is None:
        return False
    state = getattr(getattr(interaction, "client", None), "state", None)
    key = server or _primary_key(cfg)
    return any(srvperms.cap_allowed(state, key, tier, c) for c in caps)


def gate_allows(cfg, interaction, server=None, gate="mod", cap=None):
    """DÉCISION UNIQUE « tier + capacité » de toutes les portes (commandes ET boutons).
    Renvoie (ok, why, tier) avec why ∈ {None, "role", "cap"}.

    Règles (Nico 2026-08-29, corrigées le soir même) :
      - propriétaire du guild / ADMIN_IDS / tier O du serveur : toujours ;
      - tier M : passe si la capacité exigée lui est laissée par l'Owner (défauts :
        tout sauf « node_terminal ») ; sans capacité déclarée, M passe (rien à régler) ;
      - tier G : passe UNIQUEMENT pour une capacité que l'Owner lui a explicitement
        accordée via /gestion perms — sans capacité déclarée, G est refusé (« role ») ;
      - porte « read » sans capacité = capacité « read » ; rôle READ_ROLE_IDS (legacy)
        accepté pour la lecture quand le membre n'a aucun tier sur ce serveur ;
      - aucun tier sur ce serveur : refus (« role »).

    ⚠️ Avant cette fonction, la porte des boutons testait « M/O ? » AVANT la capacité :
    une capacité accordée à G (ex. « rafraîchir ») n'était jamais consultée — c'est le
    défaut constaté par Nico le 29/08 au soir (REFUS [role] DlRefreshView)."""
    caps = _caps_tuple(cap)
    if gate == "read" and caps is None:
        caps = ("read",)
    tier = tier_of(cfg, interaction, server)
    if tier is None:
        if gate == "read" and cfg.read_role_ids \
                and (_member_role_ids(interaction) & set(cfg.read_role_ids)):
            return True, None, "R"
        return False, "role", None
    if tier == "O":
        return True, None, tier
    if caps is None:
        # rien de réglable : M passe, G non (une vue « mod » sans capacité déclarée)
        return (tier == "M"), (None if tier == "M" else "role"), tier
    state = getattr(getattr(interaction, "client", None), "state", None)
    key = server or _primary_key(cfg)
    ok = any(srvperms.cap_allowed(state, key, tier, c) for c in caps)
    return ok, (None if ok else "cap"), tier


def cap_label(cap):
    caps = _caps_tuple(cap) or ()
    labels = []
    for c in caps:
        spec = srvperms.CAPS.get(c)
        labels.append(spec[0] if spec else str(c))
    return " / ".join(labels) if labels else str(cap)


def channel_server(interaction):
    """Clé serveur du salon d'où part l'interaction (core/channels.server_of_channel).
    Sans I/O. Salon sans catégorie (#général, DM) = serveur de cette instance."""
    from . import channels as _ch
    bot = getattr(interaction, "client", None)
    if bot is None or getattr(bot, "cfg", None) is None:
        return None
    return _ch.server_of_channel(bot, getattr(interaction, "channel", None))


def lock_server(interaction):
    """Clé serveur si l'interaction part d'un salon d'une catégorie « 🔒 Lock <srv> »,
    sinon None. Sans I/O."""
    from . import channels as _ch
    bot = getattr(interaction, "client", None)
    if bot is None:
        return None
    return _ch.lock_server_of_channel(bot, getattr(interaction, "channel", None))


# --- journalisation des refus (audit 2026-08-29 : 0 refus tracé sur 348 lignes) ---
def log_refusal(interaction, why, detail=""):
    """Trace un refus dans le log ET dans audit.log. Ne lève jamais."""
    try:
        cmd = getattr(getattr(interaction, "command", None), "qualified_name", None)
        data = getattr(interaction, "data", None) or {}
        what = cmd or data.get("custom_id") or data.get("name") or "?"
        user = getattr(interaction, "user", None)
        uid = getattr(user, "id", "?")
        log.warning("REFUS [%s] %s (%s) sur %r guild=%s salon=%s : %s",
                    why, user, uid, what, getattr(interaction, "guild_id", None),
                    getattr(interaction, "channel_id", None), detail)
        audit = getattr(getattr(interaction, "client", None), "audit", None)
        if audit is not None:
            audit.record(user=f"{user}({uid})", action="refus", target=str(what),
                         result=f"{why}: {detail}"[:200])
    except Exception:  # noqa: BLE001 — la trace ne doit jamais casser le refus
        log.debug("journalisation d'un refus impossible", exc_info=True)


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
    """Porte commune des boutons d'ADMINISTRATION : rôle M/O du serveur + session 2FA.
    Répond elle-même en cas de refus. `server=None` = serveur de cette instance."""
    cfg = interaction.client.cfg
    if not is_admin(cfg, interaction, server=server):
        log_refusal(interaction, "role", f"bouton admin serveur={server or _primary_key(cfg)}")
        await interaction.response.send_message(
            "🔒 Action réservée aux gestionnaires (rôle M/O "
            + (server or _primary_key(cfg)) + ").", ephemeral=True)
        return False
    if not session_2fa_ok(interaction):
        await deny_2fa(interaction)
        return False
    return True


def may_lock(cfg, interaction) -> bool:
    """True si l'utilisateur peut accéder à la catégorie Lock du serveur du NŒUD
    (NODE_SERVER_KEY) : propriétaire (break-glass), OU porteur du rôle « M <srv> »
    OU « O <srv> ». Le tier « G » (visualiser) n'a PAS accès au Lock."""
    # défense en profondeur : ne jamais accorder sur une interaction d'un autre guild
    if cfg.guild_id and getattr(interaction, "guild_id", None) != cfg.guild_id:
        return False
    if is_breakglass(cfg, interaction):
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
        log_refusal(interaction, "role", "bouton Lock")
        await interaction.response.send_message(
            "🔒 Réservé aux rôles **M** ou **O** (ou au propriétaire).", ephemeral=True)
        return False
    if not session_2fa_ok(interaction):
        await deny_2fa(interaction)
        return False
    return True


def _guild_ok(cfg, interaction):
    if cfg.guild_id and interaction.guild_id != cfg.guild_id:
        raise app_commands.CheckFailure("Serveur non autorisé.")


def _resolve_scope(cfg, interaction, scope):
    """Serveur visé par la commande selon `scope` :
      - "primary" : la commande décrit le serveur de CETTE instance ; refus si elle est
        lancée depuis un salon d'un autre serveur (on ne mélange rien) ;
      - "channel" : le serveur du salon (la cible sera vérifiée dans le corps)."""
    if scope not in SCOPES:
        raise ValueError(f"scope inconnu {scope!r}")
    mine = _primary_key(cfg)
    srv = channel_server(interaction) or mine
    if scope == "primary" and srv != mine:
        raise app_commands.CheckFailure(
            f"Cette commande concerne le serveur **{mine}** ; ce salon appartient à "
            f"**{srv}**. Les serveurs ne se mélangent pas : relance-la depuis un salon "
            f"de **{mine}**.")
    return srv


def admin_check(require_admin_channel=True, scope="primary", cap=None):
    """Gate admin PAR SERVEUR.

    - `scope="primary"` (défaut) : commande du serveur de cette instance (R820), refusée
      hors de ses salons ; `scope="channel"` : serveur du salon, pour les commandes qui
      nomment une cible (le corps vérifie ensuite la cible via ui.guard_target).
    - `require_admin_channel=True` (défaut) confine la commande aux salons de la
      catégorie « 🔒 Lock <srv> » du serveur visé (actions destructives) — ou, legacy,
      à ADMIN_CHANNEL_ID pour le serveur primaire. False = n'importe quel salon du
      serveur (ex. /setratio).
    - `cap` : capacité srvperms exigée en plus du tier (ex. "services")."""
    async def predicate(interaction: discord.Interaction) -> bool:
        cfg = interaction.client.cfg
        _guild_ok(cfg, interaction)
        srv = _resolve_scope(cfg, interaction, scope)
        if require_admin_channel:
            ok_chan = lock_server(interaction) == srv
            if not ok_chan and cfg.admin_channel_id and srv == _primary_key(cfg) \
                    and interaction.channel_id == cfg.admin_channel_id:
                ok_chan = True
            if not ok_chan:
                raise app_commands.CheckFailure(
                    f"Action réservée aux salons de la catégorie **🔒 Lock {srv}**.")
        ok, why, tier = gate_allows(cfg, interaction, server=srv, gate="mod", cap=cap)
        if not ok and why == "cap":
            raise app_commands.CheckFailure(
                f"Capacité **{cap_label(cap)}** non accordée à ton niveau **{tier}** sur "
                f"**{srv}** (réglable par l'Owner du serveur : `/gestion perms`).")
        if not ok:
            # ⚠️ Ne PAS mentionner la permission Discord « Administrateur » : elle n'est
            # plus honorée depuis 2026-07-16 (seul le rôle M/O du serveur ouvre les actions).
            raise app_commands.CheckFailure(
                f"Réservé aux gestionnaires de **{srv}** (rôle M ou O, accordé via /gestion"
                + (" — ou G avec cette capacité accordée par l'Owner)." if cap else ")."))
        return True

    predicate.__qualname__ = "admin_check.<locals>.predicate"
    return app_commands.check(predicate)


def read_check(scope="primary", cap="read"):
    """Gate lecture PAR SERVEUR (mêmes `scope` que admin_check). Le tier lecture est
    M/O du serveur, G si son Owner lui a ouvert « read », ou READ_ROLE_IDS (legacy)."""
    async def predicate(interaction: discord.Interaction) -> bool:
        cfg = interaction.client.cfg
        _guild_ok(cfg, interaction)
        srv = _resolve_scope(cfg, interaction, scope)
        if cfg.read_channel_ids:
            allowed = set(cfg.read_channel_ids)
            if cfg.admin_channel_id:
                allowed.add(cfg.admin_channel_id)
            if interaction.channel_id not in allowed:
                raise app_commands.CheckFailure("Commande non autorisée dans ce salon.")
        ok, why, tier = gate_allows(cfg, interaction, server=srv, gate="read", cap=cap)
        if not ok and why == "cap":
            raise app_commands.CheckFailure(
                f"Capacité **{cap_label(cap or 'read')}** non accordée à ton niveau "
                f"**{tier}** sur **{srv}** (réglable par l'Owner du serveur : "
                "`/gestion perms`).")
        if not ok:
            raise app_commands.CheckFailure(
                f"Réservé aux membres autorisés sur **{srv}** (rôle M/O, ou G avec la "
                "capacité accordée par l'Owner).")
        return True

    predicate.__qualname__ = "read_check.<locals>.predicate"
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
            log_refusal(interaction, "check", msg)
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
