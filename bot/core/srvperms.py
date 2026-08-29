"""Permissions FINES par serveur, réglées par l'Owner de chaque serveur (Nico 2026-08-29).

MODÈLE
------
Chaque serveur (clé GESTION_SERVERS : R820, AVY-NAS, AVY-LLM, AVY-MS01, SYNO…) est
totalement indépendant. Ses trois tiers G (Gestion = voir) / M (Modérateur = agir) /
O (Owner) ont des DÉFAUTS codés ici ; l'Owner du serveur peut, via `/gestion perms`,
resserrer ou élargir ce que G et M peuvent faire SUR SON SERVEUR :

  - CAPACITÉS : lecture (commandes), rafraîchir, graphes, start/stop/reboot, sauvegarde,
    suppression de sauvegarde, console LXC, sauvegarde du nœud, shell root du nœud,
    validation #demandes, panneaux de services (docker/dns/dist/sso/torrents…),
    acquittement des alertes ;
  - VISIBILITÉ : masquer tel ou tel salon du serveur à un tier (posé en overwrite
    Discord par provision, donc réel côté client, pas seulement côté bot).

Le tier O n'est jamais restreint ; le propriétaire du guild et ADMIN_IDS non plus
(break-glass, cf. core/permissions). Une capacité absente du catalogue est REFUSÉE
(fail-closed) : ajouter un bouton = déclarer sa capacité ici.

ÉTAT PERSISTANT (bot.state, clé « srv_perms ») :
    {"R820": {"M": {"caps": {"terminal": false}, "hidden": ["1519075565704052856"]},
              "G": {"caps": {"read": true}}}}
Seuls les ÉCARTS aux défauts sont stockés : remettre un tier à zéro = supprimer sa clé.
"""
import logging

log = logging.getLogger("discord-bot.srvperms")

STATE_KEY = "srv_perms"
TIERS = ("G", "M")          # tiers réglables (O = tout, toujours)

# Catalogue : clé -> (libellé, description courte, défaut G, défaut M)
CAPS = {
    "read":          ("📖 Lecture",            "commandes de lecture (/ct, /status, /graph…)",     False, True),
    "refresh":       ("🔄 Rafraîchir",         "boutons Rafraîchir des embeds",                     False, True),
    "graph":         ("📈 Graphes",            "bouton Graph / commande /graph",                    False, True),
    "start":         ("▶️ Démarrer",           "démarrer un invité",                                False, True),
    "stop":          ("⏹️ Arrêter",            "arrêter un invité",                                 False, True),
    "reboot":        ("🔁 Redémarrer",         "redémarrer un invité",                              False, True),
    "backup":        ("💾 Sauvegarder",        "lancer une sauvegarde d'un invité",                 False, True),
    "backup_delete": ("🗑️ Supprimer sauvegarde", "supprimer une sauvegarde (irréversible)",        False, True),
    "terminal":      ("🖥️ Console LXC",        "console root d'un conteneur (bouton Terminal)",     False, True),
    "node_backup":   ("🗄️ Sauvegarde du nœud", "vzdump de TOUS les invités (salon hyperviseur)",    False, True),
    "node_terminal": ("☠️ Shell root du nœud", "shell root de l'hyperviseur (⚠️ accès à tout)",    False, False),
    "requests":      ("🎬 Demandes médias",    "approuver/refuser dans #demandes",                  False, True),
    "services":      ("🧩 Panneaux services",  "/docker, /dns, /dist, /sso, /torrents, /yt-config…", False, True),
    "alerts":        ("🔕 Alertes",            "mettre une alerte en sommeil (#alertes)",           False, True),
}

# actions /ctctl -> capacité
ACTION_CAP = {"start": "start", "stop": "stop", "restart": "reboot", "reboot": "reboot",
              "backup": "backup"}


def default_cap(tier, cap):
    spec = CAPS.get(cap)
    if spec is None:
        return False
    return spec[2] if tier == "G" else spec[3]


def _table(state):
    d = state.get(STATE_KEY, {}) if state is not None else {}
    return d if isinstance(d, dict) else {}


def tier_conf(state, server, tier):
    """Écarts enregistrés pour (serveur, tier) : {"caps": {...}, "hidden": [...]}."""
    srv = _table(state).get(server, {})
    conf = srv.get(tier, {}) if isinstance(srv, dict) else {}
    return conf if isinstance(conf, dict) else {}


def cap_allowed(state, server, tier, cap):
    """True si le tier `tier` du serveur `server` dispose de la capacité `cap`.
    O = toujours ; capacité inconnue = jamais (fail-closed) ; sinon écart enregistré,
    sinon défaut du catalogue."""
    if tier == "O":
        return True
    if tier not in TIERS or cap not in CAPS:
        return False
    caps = tier_conf(state, server, tier).get("caps", {})
    v = caps.get(cap) if isinstance(caps, dict) else None
    if isinstance(v, bool):
        return v
    return default_cap(tier, cap)


def effective_caps(state, server, tier):
    """{cap: bool} effectif pour l'affichage du panneau."""
    return {c: cap_allowed(state, server, tier, c) for c in CAPS}


def hidden_channels(state, server, tier):
    """Ids (int) des salons du serveur MASQUÉS au tier."""
    if tier not in TIERS:
        return set()
    h = tier_conf(state, server, tier).get("hidden", [])
    out = set()
    for x in (h or []):
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out


def _write(state, server, tier, conf):
    table = dict(_table(state))
    srv = dict(table.get(server, {}) or {})
    if conf:
        srv[tier] = conf
    else:
        srv.pop(tier, None)
    if srv:
        table[server] = srv
    else:
        table.pop(server, None)
    state.set(STATE_KEY, table)


def set_caps(state, server, tier, allowed):
    """Enregistre l'ensemble des capacités AUTORISÉES pour le tier (les autres sont
    refusées). Seuls les écarts aux défauts sont conservés."""
    if tier not in TIERS:
        raise ValueError(f"tier réglable attendu (G/M), reçu {tier!r}")
    allowed = {c for c in allowed if c in CAPS}
    conf = dict(tier_conf(state, server, tier))
    caps = {}
    for c in CAPS:
        want = c in allowed
        if want != default_cap(tier, c):
            caps[c] = want
    if caps:
        conf["caps"] = caps
    else:
        conf.pop("caps", None)
    _write(state, server, tier, conf)
    return caps


def set_hidden(state, server, tier, channel_ids):
    if tier not in TIERS:
        raise ValueError(f"tier réglable attendu (G/M), reçu {tier!r}")
    conf = dict(tier_conf(state, server, tier))
    ids = sorted({str(int(c)) for c in channel_ids})
    if ids:
        conf["hidden"] = ids
    else:
        conf.pop("hidden", None)
    _write(state, server, tier, conf)
    return ids


def reset(state, server, tier=None):
    """Remet un tier (ou tout le serveur) aux défauts."""
    if tier is None:
        table = dict(_table(state))
        table.pop(server, None)
        state.set(STATE_KEY, table)
    else:
        _write(state, server, tier, {})


def tier_has_slash_caps(state, server, tier):
    """True si le tier peut lancer au moins une COMMANDE slash (sert à provision pour
    lui rendre `use_application_commands` dans les salons du serveur)."""
    return any(cap_allowed(state, server, tier, c) for c in ("read", "graph", "services"))


def summary_lines(state, server, tier):
    """Lignes lisibles pour l'embed du panneau."""
    eff = effective_caps(state, server, tier)
    lines = []
    for c, (label, desc, dg, dm) in CAPS.items():
        dflt = dg if tier == "G" else dm
        mark = "✅" if eff[c] else "❌"
        star = "" if eff[c] == dflt else " *(modifié)*"
        lines.append(f"{mark} {label} — {desc}{star}")
    return lines
