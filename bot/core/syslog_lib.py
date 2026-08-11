"""Pure syslog parsing + burst coalescing.

Lifted from /root/pve-improvements/r820-deploy/discord-syslog/syslog_to_discord.py
(COPIED, not imported — the original runs config validation + sys.exit at import time).
Transport-agnostic: the bot feeds datagrams in via Aggregator.add and drains groups
to post over the gateway (channel.send), instead of a webhook.

Aucune dépendance à discord.py ici (module de parsing pur) : l'échappement markdown
nécessaire à l'affichage est donc réimplémenté localement (`escape_md`) plutôt
qu'importé de `discord.utils`.
"""
import logging
import os
import re
import threading
import time
from collections import OrderedDict

log = logging.getLogger("discord-bot.syslog")

# Bornes appliquées À L'INGESTION (2026-08-11). Un datagramme UDP fait jusqu'à
# 65 507 octets et rien ne coupait avant le stockage : format_group ne tronquait
# qu'à l'affichage, donc une rafale de gros paquets tenait entièrement en RAM.
MAX_TEXT = 1024      # format_group coupe à 1500 : on reste au-dessus du log légitime
MAX_HOST = 64
MAX_APP = 32
# Plafond du nombre de groupes distincts d'une fenêtre de flush. coalesce_key ne
# normalise que les chiffres : faire varier des LETTRES crée une clé neuve à chaque
# paquet, donc un dict non borné + un tri O(n) sur la boucle d'événements.
MAX_GROUPS = 2000

SEVERITY_NAMES = {
    0: "emerg", 1: "alert", 2: "crit", 3: "err",
    4: "warning", 5: "notice", 6: "info", 7: "debug",
}
SEVERITY_NUM = {v: k for k, v in SEVERITY_NAMES.items()}
SEVERITY_NUM.update({"error": 3, "warn": 4, "information": 6, "informational": 6})

SEVERITY_EMOJI = {
    0: "🚨", 1: "🚨", 2: "🔥", 3: "❌",
    4: "⚠️", 5: "ℹ️", 6: "ℹ️", 7: "🐛",
}

# ---- Localisation française -------------------------------------------------
SEVERITY_FR = {
    0: "urgence", 1: "alerte", 2: "critique", 3: "erreur",
    4: "avertissement", 5: "remarque", 6: "info", 7: "débogage",
}

# Interrupteur : LOG_TRANSLATE_FR=false garde les messages bruts en anglais.
TRANSLATE_FR = os.environ.get("LOG_TRANSLATE_FR", "true").strip().lower() \
    not in ("0", "false", "no", "off")


def sev_fr(sev):
    """Libellé français d'une sévérité syslog (numérique)."""
    return SEVERITY_FR.get(sev, str(sev))


# Règles ordonnées (regex, gabarit FR, litéral requis). Première correspondance
# gagne : le message est réécrit en français à partir des groupes nommés. Aucune
# correspondance -> texte d'origine conservé (les messages inconnus restent
# en anglais plutôt que d'être déformés).
#
# Le 3e champ est un fragment EN MINUSCULES que le regex exige de toute façon : s'il
# est absent du message, on saute le regex. Ce n'est pas de l'optimisation gratuite —
# quatre motifs commencent par un quantificateur non ancré ([\w@.\-]+, \S+, [\w./\-]+)
# et coûtent O(n²) sur un message d'un seul tenant : 460 ms mesurées pour un log de
# 3 Ko, passées à bloquer la boucle d'événements (format_group est appelé depuis
# _dispatch et depuis aclose). Le filtre ne peut pas changer la règle gagnante : un
# message que le regex accepterait contient forcément son litéral. (2026-08-11)
_TR_RULES = [
    (re.compile(r"(?P<unit>[\w@.\-]+)\.service: Failed with result '(?P<res>[^']+)'"),
     "Le service {unit} s'est arrêté en échec (résultat : {res}).",
     "failed with result"),
    (re.compile(r"(?P<unit>[\w@.\-]+)\.service: Main process exited, code=(?P<code>\w+), status=(?P<status>\S+)"),
     "Le service {unit} s'est terminé anormalement (code={code}, statut={status}).",
     "main process exited"),
    (re.compile(r"Start request repeated too quickly"),
     "Redémarrages trop rapprochés : le service a été abandonné par systemd.",
     "start request repeated too quickly"),
    (re.compile(r"(?P<unit>\S+) entered failed state"),
     "L'unité {unit} est passée en état d'échec.",
     "entered failed state"),
    (re.compile(r"Failed to start (?P<what>.+?)\.?$"),
     "Échec du démarrage : {what}.",
     "failed to start "),
    (re.compile(r"(?:Out of memory: )?Killed process (?P<pid>\d+) \((?P<name>[^)]+)\)"),
     "Mémoire saturée : processus « {name} » (PID {pid}) tué par le noyau (OOM).",
     "killed process "),
    (re.compile(r"Out of memory"),
     "Mémoire saturée (déclenchement de l'OOM-killer).",
     "out of memory"),
    (re.compile(r"Failed password for (?:invalid user )?(?P<user>\S+) from (?P<ip>[\w.:]+)"),
     "Échec d'authentification SSH pour « {user} » depuis {ip}.",
     "failed password for "),
    (re.compile(r"Invalid user (?P<user>\S+) from (?P<ip>[\w.:]+)"),
     "Tentative SSH avec un utilisateur inexistant « {user} » depuis {ip}.",
     "invalid user "),
    (re.compile(r"Accepted (?:password|publickey|keyboard-interactive[\w/]*) for (?P<user>\S+) from (?P<ip>[\w.:]+)"),
     "Connexion SSH réussie : « {user} » depuis {ip}.",
     "accepted "),
    (re.compile(r"metrics send error '(?P<tgt>[^']+)': (?P<err>.+)"),
     "Échec d'envoi des métriques vers {tgt} : {err}.",
     "metrics send error "),
    (re.compile(r"EXT4-fs error \(device (?P<dev>[^)]+)\)"),
     "Erreur du système de fichiers EXT4 sur {dev}.",
     "ext4-fs error (device "),
    (re.compile(r"[Rr]e-?mount(?:ing|ed)?\b.{0,60}read-only"),
     "Système de fichiers repassé en lecture seule (erreur disque probable).",
     "read-only"),
    (re.compile(r"\[(?P<jail>[^\]]+)\]\s+Ban\s+(?P<ip>\S+)"),
     "fail2ban : {ip} banni (jail {jail}).",
     "ban"),
    (re.compile(r"\[(?P<jail>[^\]]+)\]\s+Unban\s+(?P<ip>\S+)"),
     "fail2ban : {ip} débanni (jail {jail}).",
     "unban"),
    (re.compile(r"Backup of VM (?P<id>\d+) failed"),
     "Échec de la sauvegarde de la VM/du conteneur {id}.",
     "backup of vm "),
    (re.compile(r"VM (?P<id>\d+) qga command '(?P<cmd>[^']+)' failed - got timeout"),
     "Agent QEMU de la VM {id} : commande « {cmd} » sans réponse (délai dépassé).",
     "qga command "),
    (re.compile(r"(?P<proc>[\w./\-]+)\[\d+\]: segfault"),
     "Plantage mémoire (segfault) du processus {proc}.",
     "segfault"),
]


_TR_WARNED = set()


def translate_fr(text):
    """Réécrit en français les messages connus ; sinon renvoie le texte tel quel."""
    low = text.lower()          # un seul abaissement de casse pour tout le filtrage
    for rx, tmpl, needle in _TR_RULES:
        if needle and needle not in low:
            continue            # garde-fou anti-O(n²) : cf. commentaire de _TR_RULES
        m = rx.search(text)
        if m:
            d = {k: (v if v is not None else "?") for k, v in m.groupdict().items()}
            try:
                return tmpl.format(**d)
            except Exception as e:
                # Gabarit cassé = bug de code, pas de donnée : on repli sur le texte
                # brut mais on le DIT (une seule fois par gabarit pour ne pas noyer
                # le journal si la règle correspond à chaque message). 2026-08-11
                if tmpl not in _TR_WARNED:
                    _TR_WARNED.add(tmpl)
                    log.warning("gabarit de traduction invalide %r (%s)", tmpl, e)
                return text
    return text


# Caractères qui font de la mise en forme dans Discord. Le corps d'un log est du
# texte contrôlé par l'émetteur : sans échappement, un `**` ou un backtick casse le
# rendu (voire imite un préfixe de gravité). Les mentions, elles, sont déjà
# neutralisées à l'envoi (allowed_mentions.none()).
_MD_RE = re.compile(r"([\\`*_~|>])")


def escape_md(s):
    """Neutralise le markdown Discord d'un texte non fiable (échappement local)."""
    return _MD_RE.sub(r"\\\1", s)


def sanitize_field(s, limit, fallback="?"):
    """Assainit un champ d'en-tête (hôte) rendu HORS bloc `code`.

    ⚠️ PIÈGE : en RFC5424 l'hôte et le programme sortent d'un simple `split(" ")`
    (cf. parse_packet), donc ils peuvent contenir un SAUT DE LIGNE — échapper le
    markdown ne suffit pas, `**ho\\nst**` fabrique quand même une ligne visuelle
    supplémentaire dans #logs, ce qui est exactement la forge que l'on veut fermer.
    On écrase donc d'abord toute espace (str.split() couvre \\n, \\r, \\x85, \\u2028…),
    on tronque, PUIS on échappe : couper après l'échappement laisserait une
    contre-oblique orpheline qui mangerait le caractère suivant du gabarit. (2026-08-11)
    """
    clean = " ".join(str(s or "").split())[:limit]
    return escape_md(clean) if clean else fallback


def sanitize_code(s, limit):
    """Assainit un champ rendu DANS un bloc `code` (le programme).

    Discord affiche le contenu d'un bloc `code` VERBATIM : y échapper le markdown
    n'y protège de rien et fait apparaître les contre-obliques à l'écran
    (`node_exporter` deviendrait `node\\_exporter` sur chaque ligne légitime).
    Seuls comptent ici ce qui referme le bloc — le backtick, remplacé par un
    homoglyphe — et les sauts de ligne. (2026-08-11)
    """
    return " ".join(str(s or "").split())[:limit].replace("`", "ˋ")


PRI_RE = re.compile(rb"^<(\d{1,3})>")
NUM_RE = re.compile(r"\d+")
# RFC3164 TAG: "appname[pid]: message" (pid optional). Kernel & co have no tag -> no match.
TAG_RE = re.compile(r"^([A-Za-z0-9_.\/-]{1,48}?)(?:\[\d+\])?:\s+(.*)$")


def parse_packet(data, src_ip):
    """Return (severity_num, hostname, appname, text) for a syslog datagram (RFC3164/5424).

    appname is "" when absent (RFC5424 NILVALUE, or RFC3164 without a TAG — kernel etc.).
    """
    sev = 5
    raw = data
    m = PRI_RE.match(data)
    if m:
        sev = int(m.group(1)) & 0x07
        raw = data[m.end():]
    try:
        text = raw.decode("utf-8", "replace").strip()
    except Exception:
        text = repr(raw)

    host = src_ip
    app = ""
    parts = text.split(" ", 6)
    if len(parts) >= 7 and parts[0] == "1":  # RFC5424: VER TS HOST APP PROCID MSGID REST
        host = parts[2] if parts[2] not in ("-", "") else src_ip
        app = parts[3] if parts[3] not in ("-", "") else ""
        rest = parts[6]  # STRUCTURED-DATA (- or [..]) + SP + MSG
        if rest.startswith("-"):  # NILVALUE structured data
            text = rest[1:].lstrip(" ")
        elif rest.startswith("["):  # consume one or more [SD-ELEMENT]
            i, n = 0, len(rest)
            while i < n and rest[i] == "[":
                depth = 0
                while i < n:
                    c = rest[i]
                    if c == "\\" and i + 1 < n:  # RFC5424 escapes ] " \ as \X
                        i += 2
                        continue
                    if c == "[":
                        depth += 1
                    elif c == "]":
                        depth -= 1
                        if depth == 0:
                            i += 1
                            break
                    i += 1
            text = rest[i:].lstrip(" ")
        else:
            text = rest
    else:  # RFC3164
        m3 = re.match(r"^[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+(\S+)\s+(.*)$", text)
        if m3:
            host = m3.group(1)
            text = m3.group(2)
        mt = TAG_RE.match(text)
        if mt:
            app = mt.group(1)
            text = mt.group(2)
    # Normalisation À LA SOURCE (2026-08-11). L'échappement et la troncature existent bien
    # en aval (sanitize_field / sanitize_code au rendu, Aggregator.add pour les clés), mais
    # ils reposaient sur un contrat IMPLICITE : « parse_packet rend du non-fiable, à
    # assainir plus tard ». C'est exactement la forme de contrat non écrit qui a produit
    # les défauts de cet audit — un seul appelant qui l'ignore rouvre la forge de lignes.
    # L'hôte et le programme sortent d'un `split(" ")` sur un datagramme UDP NON
    # AUTHENTIFIÉ : ils peuvent contenir « \n ». On écrase toute espace (str.split() couvre
    # \n, \r, \x85,  …) et on borne ici. Le corps, lui, garde ses sauts de ligne
    # légitimes (une trace d'exception EST multi-ligne) : c'est le rendu qui s'en charge.
    host = " ".join(str(host or "").split())[:MAX_HOST] or src_ip
    app = " ".join(str(app or "").split())[:MAX_APP]
    return sev, host, app, text


def coalesce_key(sev, host, app, text):
    norm = NUM_RE.sub("#", text)
    norm = re.sub(r"\s+", " ", norm).strip()
    return (sev, host, app, norm[:200])


class Aggregator:
    """Coalescence des rafales, bornée en mémoire.

    `max_groups` plafonne le nombre de clés DISTINCTES d'une fenêtre de flush : au-delà,
    les nouvelles clés sont comptées dans `dropped` et jetées (les groupes déjà connus
    continuent de compter normalement). Sans ce plafond, un émetteur qui fait varier des
    lettres à chaque paquet crée une entrée neuve par datagramme — dict non borné puis
    tri O(n) sur la boucle d'événements au flush. (2026-08-11)
    """

    def __init__(self, max_groups=MAX_GROUPS):
        self.lock = threading.Lock()
        self.groups = OrderedDict()
        self.max_groups = max_groups
        self.dropped = 0          # cumulatif, exposé par /logstream stats

    def add(self, sev, host, app, text):
        # Troncature À L'INGESTION : ce qu'on stocke est ce qu'on affichera, et un
        # datagramme de 64 Ko ne doit pas rester en mémoire jusqu'au flush.
        text = str(text)[:MAX_TEXT]
        host = str(host)[:MAX_HOST]
        app = str(app)[:MAX_APP]
        key = coalesce_key(sev, host, app, text)
        now = time.time()
        with self.lock:
            g = self.groups.get(key)
            if g is None:
                if len(self.groups) >= self.max_groups:
                    self.dropped += 1
                    return
                self.groups[key] = {"count": 1, "sev": sev, "host": host, "app": app,
                                    "sample": text, "first": now, "last": now}
            else:
                g["count"] += 1
                g["last"] = now

    def drain(self):
        with self.lock:
            groups = self.groups
            self.groups = OrderedDict()
        return groups


def format_group(g, site_label="", translate=None):
    """Rend une ligne Discord à partir d'un groupe coalescé.

    Tout ce qui vient du datagramme (hôte, programme, corps) est tronqué puis échappé :
    un log multi-ligne (trace noyau, exception Java relayée par rsyslog) cassait la mise
    en page en produisant plusieurs lignes visuelles indiscernables de vrais messages, et
    un backtick ou un `**` dans un log légitime brouillait le rendu. (2026-08-11)
    """
    do_tr = TRANSLATE_FR if translate is None else translate
    emoji = SEVERITY_EMOJI.get(g["sev"], "•")
    sevname = sev_fr(g["sev"]) if do_tr else SEVERITY_NAMES.get(g["sev"], str(g["sev"]))
    # Délinéarisation + troncature + échappement : cf. sanitize_field / sanitize_code.
    host = sanitize_field(g.get("host"), MAX_HOST)
    prefix = f"{emoji} `{sevname}` **{host}**"
    app = sanitize_code(g.get("app"), MAX_APP)
    if app:
        prefix += f" `{app}`"
    if site_label:
        prefix = f"`{site_label}` " + prefix
    body = g["sample"]
    if do_tr:
        # Assainir APRÈS traduction : translate_fr remplace le message par un gabarit
        # maîtrisé quand une règle correspond, et c'est ce texte-là qui part.
        body = translate_fr(body)
    if len(body) > 1500:
        body = body[:1500] + "…"
    body = escape_md(" ⏎ ".join(body.splitlines()))
    if len(body) > 1500:                 # l'échappement peut doubler la longueur
        body = body[:1500]
        # On ne retire QUE la contre-oblique orpheline (parité), pas tout le run final :
        # un rstrip("\\") vidait entièrement un corps fait de contre-obliques —
        # 1 500 « \ » d'affilée ne laissaient plus que « … ». (2026-08-11)
        if (len(body) - len(body.rstrip("\\"))) % 2:
            body = body[:-1]
        body += "…"
    suffix = f"  _(x{g['count']})_" if g["count"] > 1 else ""
    return f"{prefix}: {body}{suffix}"
