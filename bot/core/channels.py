"""Résolution des catégories provisionnées — SOURCE UNIQUE DE VÉRITÉ.

POURQUOI CE MODULE EXISTE
-------------------------
`provision.py` possède les salons et les catégories : il les crée, les nomme d'après
`SERVER_KEY` (« 🔒 Lock R820 », « 📊 Supervision R820 ») et publie leurs ids dans
`state["prov"]["categories"]`. Six cogs satellites cherchaient pourtant la même catégorie
CHACUN À SA FAÇON, avec quatre défauts récurrents (audit du 2026-08-11) :

  - des noms de catégorie EN DUR et PÉRIMÉS depuis le passage multi-serveurs
    (« Lock », « 📊 Supervision ») — introuvables, donc salon créé À LA RACINE, donc
    visible de tout le serveur ;
  - des CLÉS d'état inventées (`"super"` au lieu de `"supervision"`) — jamais trouvées ;
  - une catégorie de repli créée SANS overwrites, donc publique ;
  - quatre copies de la même fonction `_norm`.

Ici, une seule fonction fait autorité : `resolve(bot, guild, KEY)`. Et surtout,
`ensure_channel()` applique la règle qui ferme la classe entière :

    ⚠️ AUCUN cog ne crée de salon HORS d'une catégorie provisionnée.
       Pas de catégorie -> pas de salon, et on le dit dans les logs.

Un salon manquant est un désagrément réparé au prochain cycle de provisioning ; un salon
public exposant le ratio d'un tracker privé ou les demandes de médias, non.

CLÉS DISPONIBLES (telles que provision les publie — vérifiées sur l'état réel) :
    lock · supervision · containers · archive · syno_lock · syno_sup
    avy_sup_<noeud> · avy_gest_<noeud> · avy_lock_<noeud> · containers_avy
"""
import logging

import discord

log = logging.getLogger("discord-bot.channels")

# Clés publiées par provision._ensure_category. Une clé absente d'ici est une faute de
# frappe : `resolve()` la refuse bruyamment au lieu de renvoyer None en silence.
KNOWN_KEYS = {
    "lock", "supervision", "containers", "archive",
    "syno_lock", "syno_sup", "containers_avy",
}


def norm(s):
    """Nom de catégorie/salon normalisé, pour comparer « Lock » et « 🔒 Lock R820 ».

    Même règle que `provision._ensure_category` : on ne garde que l'alphanumérique en
    minuscules. Sans elle, un emoji ou une espace suffit à faire créer un DOUBLON.
    """
    return "".join(c for c in str(s or "").lower() if c.isalnum())


def _by_id(guild, cid):
    c = guild.get_channel(cid) if cid else None
    return c if isinstance(c, discord.CategoryChannel) else None


def resolve(bot, guild, key, name_hint=None):
    """Catégorie provisionnée pour `key`, ou None.

    Trois sources, dans l'ordre de fiabilité :
      1. l'id publié par provision (`state["prov"]["categories"][key]`) — fait autorité ;
      2. le NOM attendu, comparé en normalisé (`name_hint`, ex. « Lock R820 ») — rattrape
         le cas où provision n'a pas encore tourné mais où la catégorie existe déjà ;
      3. rien : None, et l'appelant DOIT renoncer plutôt que créer hors catégorie.

    On ne crée jamais de catégorie ici : c'est le travail de provision, qui seul sait
    quels overwrites poser.
    """
    if key not in KNOWN_KEYS and not key.startswith(("avy_sup_", "avy_gest_", "avy_lock_")):
        log.error("catégorie demandée avec une clé inconnue %r — provision ne publie que "
                  "%s (faute de frappe ?)", key, sorted(KNOWN_KEYS))
        return None
    if guild is None:
        return None
    cats = ((bot.state.get("prov") or {}).get("categories") or {})
    cat = _by_id(guild, cats.get(key))
    if cat is not None:
        return cat
    if name_hint:
        want = norm(name_hint)
        cat = next((c for c in guild.categories if norm(c.name) == want), None)
        if cat is not None:
            return cat
    return None


def lock_category(bot, guild):
    """Catégorie « 🔒 Lock <SERVER_KEY> » — celle qui masque @everyone."""
    srv = getattr(bot.cfg, "server_key", "R820")
    return resolve(bot, guild, "lock", f"Lock {srv}")


def supervision_category(bot, guild):
    """Catégorie « 📊 Supervision <SERVER_KEY> »."""
    srv = getattr(bot.cfg, "server_key", "R820")
    return resolve(bot, guild, "supervision", f"Supervision {srv}")


def server_of_channel(bot, channel):
    """Clé serveur du SALON d'où part une commande : « R820 » (SERVER_KEY) par défaut,
    « AVY-<NŒUD> » dans une catégorie provisionnée d'un nœud Aveyron, « AVY » dans la
    catégorie générique `containers_avy`, « SYNO » dans celles du NAS.

    Règle Nico 2026-08-26 : « chaque serveur est séparé, on ne mélange rien ». Cette
    fonction est la SEULE définition de « quel serveur est ce salon » : l'autocomplétion
    et les commandes qui prennent une cible (/ct, /graph) s'en servent pour ne proposer
    et n'afficher que les machines du serveur courant.

    Sources, par ordre de fiabilité : l'id de catégorie publié par provision
    (`state["prov"]["categories"]`), puis le NOM de la catégorie (normalisé) pour un
    provisioning pas encore passé. Salon sans catégorie (#général, DM) = serveur de
    CETTE instance.
    """
    srv = getattr(bot.cfg, "server_key", "R820")
    if channel is None:
        return srv
    cat = channel if isinstance(channel, discord.CategoryChannel) else None
    if cat is None:
        # fil -> salon parent -> catégorie
        parent = getattr(channel, "parent", None)
        cat = getattr(channel, "category", None) or getattr(parent, "category", None)
    if cat is None:
        return srv
    cats = ((bot.state.get("prov") or {}).get("categories") or {})
    for key, cid in cats.items():
        if cid != cat.id:
            continue
        for pfx in ("avy_sup_", "avy_gest_", "avy_lock_"):
            if key.startswith(pfx):
                return bot.pve.avy_server_key(key[len(pfx):])
        if key == "containers_avy":
            return "AVY"
        if key.startswith("syno_"):
            return "SYNO"
        return srv
    n = norm(cat.name)
    for node in (getattr(bot.cfg, "avy_nodes", None) or []):
        k = bot.pve.avy_server_key(node)
        if n.endswith(norm(k)):
            return k
    if n.endswith(norm("SYNO")):
        return "SYNO"
    return srv


def _category_of(channel):
    if channel is None:
        return None
    if isinstance(channel, discord.CategoryChannel):
        return channel
    parent = getattr(channel, "parent", None)
    return getattr(channel, "category", None) or getattr(parent, "category", None)


def lock_server_of_channel(bot, channel):
    """Clé serveur si `channel` est dans une catégorie « 🔒 Lock <srv> » (celle du
    R820, d'un nœud Aveyron ou du NAS), sinon None. Sans I/O.

    Sert de « salon admin » PAR SERVEUR (audit 2026-08-29) : les commandes destructives
    (/ctctl, /backup…) se lancent depuis la catégorie Lock du serveur visé, visible des
    seuls M/O de ce serveur — plus depuis un unique ADMIN_CHANNEL_ID global."""
    cat = _category_of(channel)
    if cat is None:
        return None
    srv = getattr(bot.cfg, "server_key", "R820")
    cats = ((bot.state.get("prov") or {}).get("categories") or {})
    for key, cid in cats.items():
        if cid != cat.id:
            continue
        if key == "lock":
            return srv
        if key.startswith("avy_lock_"):
            return bot.pve.avy_server_key(key[len("avy_lock_"):])
        if key == "syno_lock":
            return "SYNO"
        return None
    n = norm(cat.name)
    if not n.startswith("lock"):
        return None
    # repli sur le NOM : « 🔒 Lock <clé> » avec une clé déclarée (GESTION_SERVERS ou
    # nœud Aveyron connu) — la plus longue d'abord, pour que « AVY-MS01 » ne soit pas
    # attrapée par une clé qui en serait un suffixe.
    keys = set(getattr(bot.cfg, "gestion_servers", {}) or {}) | {srv, "SYNO"}
    for node in (getattr(bot.cfg, "avy_nodes", None) or []):
        keys.add(bot.pve.avy_server_key(node))
    for k in sorted(keys, key=lambda x: -len(norm(x))):
        if norm(k) and n.endswith(norm(k)):
            return k
    return None


def server_categories(bot, guild, server):
    """Catégories provisionnées appartenant au serveur `server`, sous la forme
    [(clé prov, catégorie, genre)] avec genre ∈ {"tiered", "lock"} — c'est le schéma
    d'overwrites à leur appliquer. Sert à /gestion perms (liste des salons du serveur)
    et à provision.enforce_server_perms (réapplication immédiate)."""
    if guild is None:
        return []
    srv = getattr(bot.cfg, "server_key", "R820")
    cats = ((bot.state.get("prov") or {}).get("categories") or {})
    out = []
    for key, cid in cats.items():
        cat = _by_id(guild, cid)
        if cat is None:
            continue
        if server == srv and key in ("supervision", "containers"):
            out.append((key, cat, "tiered"))
        elif server == srv and key == "lock":
            out.append((key, cat, "lock"))
        elif server == "SYNO" and key == "syno_sup":
            out.append((key, cat, "tiered"))
        elif server == "SYNO" and key == "syno_lock":
            out.append((key, cat, "lock"))
        elif key.startswith(("avy_sup_", "avy_gest_")):
            node = key.split("_", 2)[2]
            if bot.pve.avy_server_key(node) == server:
                out.append((key, cat, "tiered"))
        elif key.startswith("avy_lock_"):
            node = key[len("avy_lock_"):]
            if bot.pve.avy_server_key(node) == server:
                out.append((key, cat, "lock"))
    return out


def server_channel_ids(bot, guild, server):
    """Ids de tous les salons texte des catégories du serveur (pour valider une
    sélection de salons à masquer : jamais un salon d'un autre serveur)."""
    ids = set()
    for _key, cat, _genre in server_categories(bot, guild, server):
        ids.update(ch.id for ch in cat.text_channels)
    return ids


def same_server(channel_srv, target_srv, default="R820"):
    """True si une cible de serveur `target_srv` (None = cluster primaire) peut être
    montrée dans un salon de serveur `channel_srv`. « AVY » (catégorie générique
    Aveyron) accepte tout nœud AVY-*."""
    t = target_srv or default
    if channel_srv == "AVY":
        return str(t).startswith("AVY-")
    return channel_srv == t


def is_public(channel):
    """True si @everyone peut VOIR ce salon/cette catégorie (None si indéterminable)."""
    guild = getattr(channel, "guild", None)
    if guild is None:
        return None
    try:
        return bool(channel.permissions_for(guild.default_role).view_channel)
    except Exception:  # noqa: BLE001 — un diagnostic ne doit jamais casser l'appelant
        return None


async def ensure_channel(bot, guild, name, category, *, topic=None, reason=None):
    """Renvoie le salon `name`, en le créant DANS `category` s'il n'existe pas.

    RÈGLE (2026-08-11) : si `category` est None, on NE CRÉE RIEN et on journalise.
    `create_text_channel(category=None)` pose le salon à la racine du serveur, avec les
    permissions par défaut — c'est exactement comme cela que #ratio, #demandes et #help
    se sont retrouvés lisibles de tout le monde.

    Un salon EXISTANT est rendu tel quel, sans être déplacé : il appartient à
    l'utilisateur, qui a pu le ranger ailleurs volontairement. Pour le remettre sous clé,
    voir `seal_if_public()`.
    """
    ch = discord.utils.get(guild.text_channels, name=name)
    if ch is not None:
        return ch
    if category is None:
        log.warning("salon #%s NON créé : aucune catégorie provisionnée disponible "
                    "(il serait créé à la racine, donc PUBLIC). Il sera créé au prochain "
                    "cycle de provisioning.", name)
        return None
    try:
        # pas d'`overwrites=` : le salon SYNCHRONISE ceux de sa catégorie, qui portent la
        # confidentialité. En passer ici ferait CESSER l'héritage (piège documenté dans
        # meta._satellite_overwrites).
        ch = await guild.create_text_channel(
            name, category=category, topic=topic,
            reason=reason or "salon géré par le bot")
        log.info("salon #%s créé dans « %s »", name, category.name)
        return ch
    except discord.HTTPException as e:
        log.warning("salon #%s non créé: %s", name, e)
        return None


async def seal_if_public(bot, channel, category, *, why=""):
    """Range un salon devenu public dans `category` (héritage des overwrites).

    Utilise `edit(category=…, sync_permissions=True)` et JAMAIS `edit(overwrites=…)` :
    ce dernier REMPLACE l'ensemble des overwrites et retirerait un accès légitime posé à
    la main. Ne fait rien si la catégorie cible est elle-même publique — déplacer vers
    une catégorie ouverte ne réglerait rien.

    Renvoie True si un déplacement a eu lieu.
    """
    if channel is None or is_public(channel) is not True:
        return False
    if category is None or is_public(category) is not False:
        log.warning("#%s est visible de @everyone%s et aucune catégorie verrouillée n'est "
                    "disponible : à ranger à la main",
                    getattr(channel, "name", "?"), f" ({why})" if why else "")
        return False
    try:
        await channel.edit(category=category, sync_permissions=True,
                           reason="salon lisible de @everyone — remise sous clé")
        log.warning("#%s était visible de @everyone%s : déplacé dans « %s » et "
                    "resynchronisé sur ses permissions",
                    getattr(channel, "name", "?"), f" ({why})" if why else "",
                    category.name)
        return True
    except discord.HTTPException as e:
        log.error("#%s : remise sous clé impossible (%s) — le contenu reste lisible de "
                  "tout le serveur", getattr(channel, "name", "?"), e)
        return False
