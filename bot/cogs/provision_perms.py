"""Constructeurs d'overwrites des catégories/salons provisionnés (extraits de provision.py).

POURQUOI CE MODULE
------------------
Ce sont des fonctions quasi PURES « guild + cfg -> dict d'overwrites » : aucun appel
Discord, aucun état, rien à attendre. Les sortir de provision.py (qui pesait 2081 lignes)
rend le schéma de permissions lisible d'un seul coup d'œil — et vérifiable sans
instancier le cog. Ce n'est PAS un cog : rien à ajouter à la liste COGS de `__main__.py`.

⚠️ INVARIANT QUI TIENT TOUT LE FICHIER — `None` = « un rôle requis ne se résout pas,
   NE TOUCHE À RIEN ». Ce n'est jamais « pas d'overwrites » :

     - `edit(overwrites=None)` est un PATCH VIDE côté Discord : il ne corrige rien mais
       repart toutes les 5 minutes sur chaque salon (bug vécu, corrigé le 2026-08-11) ;
     - `create_text_channel(overwrites=None)` lève un TypeError — PAS une HTTPException —
       qui remontait jusqu'au `try` de l'étape et emportait les provisionnements voisins ;
     - poser un « @everyone deny » sans personne d'autre = perte d'accès pour tout le
       monde (anti-lockout), et la boucle de réconciliation la rejouerait en boucle.
   Les appelants DOIVENT donc tester `is None` avant de s'en servir, et se rabattre sur
   `private_seed_overwrites()` à la CRÉATION (fail-closed : personne ne voit, le bot
   écrit, le vrai schéma est posé dès que les rôles existent).

⚠️ Un membre portant la permission Discord « Administrateur » (et le propriétaire du
   serveur) IGNORE tous ces overwrites — Discord ne permet pas de les lui cacher. La
   vraie frontière des BOUTONS est runtime (`permissions.*`), pas ces permissions de
   salon, qui ne sont que le filtre de VISIBILITÉ.
"""
import logging

import discord

# même logger que provision.py : ces avertissements se lisent dans le même flux
log = logging.getLogger("discord-bot.provision")


def _base_ow(guild, **me_extra):
    """Socle commun de tous les schémas : @everyone ne voit rien, le bot garde la main.

    Sans l'overwrite du bot, une catégorie fermée peut lui devenir invisible et il ne
    sait plus ni éditer ses embeds épinglés ni corriger les permissions au cycle suivant.
    """
    ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    me = guild.me
    if me is not None:
        ow[me] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, embed_links=True,
            read_message_history=True, **me_extra)
    return ow


def private_seed_overwrites(guild):
    """Overwrites minimaux FAIL-CLOSED pour une catégorie/un salon que le bot CRÉE
    alors que les rôles voulus ne se résolvent pas encore : @everyone ne voit rien,
    seul le bot écrit (le propriétaire du guild voit tout de toute façon, Discord ne
    permet pas de le lui cacher — donc aucun risque de lockout).

    Poser ça vaut mieux que de créer public « en attendant » : `_enforce_perms`
    posera le vrai schéma dès que les rôles M/O existeront (2026-08-11)."""
    return _base_ow(guild, manage_messages=True, manage_channels=True,
                    send_messages_in_threads=True)


def ovw_drifted(ch, want):
    """True si les overwrites du salon diffèrent de l'ensemble voulu (comparaison par
    id : sans l'intent GUILD_MEMBERS les cibles sont des Object à l'identité instable)."""
    try:
        w = {t.id: p for t, p in want.items()}
        h = {t.id: p for t, p in ch.overwrites.items()}
    except Exception:  # noqa: BLE001
        return True
    return w != h


# ------------------------------------------------------- catégories « 🔒 Lock <clé> »

def _lock_ow(guild, cfg, lock_ids, what=""):
    """Schéma Lock : @everyone ne voit RIEN ; voient le propriétaire, le bot, les
    porteurs des rôles M/O de `lock_ids` et les ids de NODE_TERMINAL_OWNER_IDS.

    `what` ne sert qu'au libellé du log (« rôles M/O AVY-PVE introuvables »)."""
    ow = _base_ow(guild, manage_messages=True, manage_channels=True,
                  create_private_threads=True, send_messages_in_threads=True,
                  manage_threads=True)
    if not lock_ids:
        return None
    seen = False
    for rid in lock_ids:
        r = guild.get_role(rid)
        if r is not None:
            ow[r] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                send_messages_in_threads=True, use_application_commands=True)
            seen = True
    if not seen:
        # aucun des rôles M/O n'est résolu : ne PAS poser un Lock @everyone-deny SANS
        # personne (perte d'accès + combat de la boucle 5 min).
        log.warning("rôles M/O %sintrouvables — overwrites Lock NON modifiés", what)
        return None
    for oid in cfg.node_terminal_owner_ids:
        # get_member est vide sans l'intent GUILD_MEMBERS -> Object(id) suffit :
        # discord.py ne se sert que de .id et type l'overwrite en MEMBER.
        target = guild.get_member(oid) or discord.Object(id=oid)
        ow[target] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            create_private_threads=True, send_messages_in_threads=True)
    return ow


def lock_overwrites(guild, cfg):
    """Catégorie Lock du nœud principal : @everyone ne voit RIEN ; voient le
    propriétaire, le bot, et les porteurs du rôle « O <srv> » (qui, par construction de
    /gestion, portent AUSSI « Gestion <srv> » et ont le 2FA — cahier des charges
    2026-07-16). Catégorie Lock = tiers M (modération) ET O (owner) — pas G (visualiser).

    ⚠️ La vraie frontière des BOUTONS est runtime (permissions.lock_button_ok +
    terminal._may_open_node), pas cette permission de salon (cf. en-tête du module)."""
    return _lock_ow(guild, cfg,
                    [rid for rid in (cfg.node_mod_role_id, cfg.node_owner_role_id) if rid])


def node_lock_overwrites_fn(cfg):
    """`lock_overwrites` sous la forme `want_fn(guild)` attendue par `_enforce_perms`."""
    return lambda guild: lock_overwrites(guild, cfg)


def lock_overwrites_fn(cfg, key):
    """Comme `lock_overwrites`, mais pour la catégorie Lock d'UN serveur Aveyron
    précis (clé GESTION_SERVERS) : seuls M/O de CETTE clé voient la catégorie —
    pas le O du R820, pas le rôle G (visualiser seul) de qui que ce soit.

    Renvoie un `want_fn(guild)` : c'est la forme attendue par `_enforce_perms`."""
    def want_fn(guild):
        srv = (getattr(cfg, "gestion_servers", {}) or {}).get(key, {})
        return _lock_ow(guild, cfg,
                        [rid for rid in (srv.get("mod"), srv.get("owner")) if rid],
                        what=f"{key} ")
    return want_fn


# ------------------------------------- catégories « Supervision / Gestion <clé> » (3 tiers)

def _tiered_ow(guild, mo_ids, view_ids):
    """Schéma 3 tiers, SANS les gardes anti-lockout (c'est l'appelant qui les fait,
    parce que le message de log diffère selon la source des rôles) :
      - G (view_ids)   : VOIT, mais n'écrit pas et ne lance pas de commandes ;
      - M + O (mo_ids) : voient + écrivent + boutons (+ commandes) ;
      - @everyone      : rien.
    Les boutons restent visibles de G mais inertes (garde runtime is_admin).

    ⚠️ ORDRE : les rôles « vue seule » sont posés AVANT M/O — un rôle présent dans les
    deux listes doit garder le droit d'écrire."""
    ow = _base_ow(guild, manage_messages=True, manage_channels=True,
                  send_messages_in_threads=True)
    # VUE seule : voir, mais pas d'écriture, pas de commandes, pas de fils (sinon
    # contournement du « lecture seule »).
    view_only = discord.PermissionOverwrite(
        view_channel=True, read_message_history=True, send_messages=False,
        add_reactions=False, use_application_commands=False,
        create_public_threads=False, create_private_threads=False,
        send_messages_in_threads=False)
    for rid in view_ids:
        r = guild.get_role(rid) if rid else None
        if r is not None:
            ow[r] = view_only
    for rid in mo_ids:                            # tiers M + O : voir + interagir
        r = guild.get_role(rid)
        if r is not None:
            ow[r] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                use_application_commands=True, add_reactions=True, embed_links=True,
                send_messages_in_threads=True)
    return ow


def managed_overwrites(guild, cfg):
    """Salons de Supervision + Gestion <srv> du serveur principal (rôles ADMIN_ROLE_IDS
    pour M/O, NODE_VIEW_ROLE_ID + VIEWER_ROLE_IDS pour la vue seule).

    VIEWER_ROLE_IDS est VIDE depuis le 2026-07-17 sur demande de Nico : le rôle « A »
    ne doit voir QUE #général, plus aucun salon de gestion — seuls G/M/O par serveur
    donnent accès."""
    # garde anti-lockout : le rôle M/O (admin) DOIT être résolu, sinon on ne touche rien
    if not cfg.admin_role_ids:
        return None
    for rid in cfg.admin_role_ids:
        if guild.get_role(rid) is None:
            log.warning("rôle M/O %s introuvable — overwrites de gestion NON modifiés", rid)
            return None
    view_ids = [getattr(cfg, "node_view_role_id", 0)] + list(cfg.viewer_role_ids)
    return _tiered_ow(guild, list(cfg.admin_role_ids), view_ids)


def managed_overwrites_fn(cfg):
    """`managed_overwrites` sous la forme `want_fn(guild)` attendue par `_enforce_perms`."""
    return lambda guild: managed_overwrites(guild, cfg)


def srv_overwrites_fn(cfg, key):
    """want_fn des catégories d'un serveur secondaire (Aveyron, SYNO) : même schéma
    3 tiers que `managed_overwrites` mais avec les rôles G/M/O de la clé `key` dans
    GESTION_SERVERS (+ rôles visionneurs manuels). Garde anti-lockout : si les
    rôles M/O de la clé ne se résolvent pas, on ne touche RIEN."""
    def want_fn(guild):
        srv = (getattr(cfg, "gestion_servers", {}) or {}).get(key, {})
        mo_ids = [rid for rid in (srv.get("mod"), srv.get("owner")) if rid]
        if not mo_ids or any(guild.get_role(rid) is None for rid in mo_ids):
            log.warning("rôles M/O %s introuvables — overwrites NON modifiés", key)
            return None
        view_ids = [srv.get("view", 0)] + list(cfg.viewer_role_ids)
        return _tiered_ow(guild, mo_ids, view_ids)
    return want_fn


# ------------------------------------------------------------------ salons particuliers

def twofa_overwrites(guild):
    """#logs-2fa = JOURNAL d'audit 2FA : VISIBLE PAR PERSONNE (choix Nico). @everyone
    deny view, aucun rôle ; seul le bot y écrit (et le propriétaire le voit par bypass
    owner). Les commandes /2fa, elles, se lancent dans #général."""
    return _base_ow(guild, manage_channels=True)


def assistant_chat_overwrites(guild, cfg):
    """@everyone deny ; SEUL le rôle O AVY-LLM (+ le bot) voit/écrit ce salon —
    littéralement, pas M/G AVY-LLM ni aucun rôle R820 (demande explicite de Nico,
    plus restrictif que /assistant qui reste ouvert à tout M/O de tout serveur)."""
    srv = (getattr(cfg, "gestion_servers", {}) or {}).get("AVY-LLM", {})
    owner_id = srv.get("owner", 0)
    ow = _base_ow(guild, manage_messages=True)
    if not owner_id or guild.get_role(owner_id) is None:
        log.warning("rôle O AVY-LLM introuvable — overwrites #chat-ia NON modifiés")
        return None
    ow[guild.get_role(owner_id)] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        embed_links=True, add_reactions=True)
    return ow


def lock_perms_drifted(ch, guild, cfg):
    """True si les overwrites du salon ne sont pas EXACTEMENT ceux du schéma Lock.

    Comparaison de l'ensemble COMPLET, et pas de deux bits : un contrôle qui se
    contentait de vérifier « @everyone est refusé » et « le propriétaire est autorisé »
    était aveugle à un overwrite AJOUTÉ (quelqu'un s'ouvre l'accès à la main → jamais
    corrigé) et à un propriétaire RETIRÉ de la config (son overwrite survivait → accès
    jamais révoqué)."""
    want_ow = lock_overwrites(guild, cfg)
    if want_ow is None:
        # garde anti-lockout : aucun rôle M/O résolu -> il n'y a RIEN à comparer.
        # Avant (2026-08-11), le None.items() levait, l'except renvoyait True, et un
        # `edit(overwrites=None)` — PATCH vide côté Discord — partait toutes les
        # 5 min sur chaque salon Lock sans jamais rien corriger.
        return False
    return ovw_drifted(ch, want_ow)
