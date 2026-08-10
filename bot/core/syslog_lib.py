"""Pure syslog parsing + burst coalescing.

Lifted from /root/pve-improvements/r820-deploy/discord-syslog/syslog_to_discord.py
(COPIED, not imported — the original runs config validation + sys.exit at import time).
Transport-agnostic: the bot feeds datagrams in via Aggregator.add and drains groups
to post over the gateway (channel.send), instead of a webhook.
"""
import os
import re
import threading
import time
from collections import OrderedDict

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


# Règles ordonnées (regex, gabarit FR). Première correspondance gagne : le
# message est réécrit en français à partir des groupes nommés. Aucune
# correspondance -> texte d'origine conservé (les messages inconnus restent
# en anglais plutôt que d'être déformés).
_TR_RULES = [
    (re.compile(r"(?P<unit>[\w@.\-]+)\.service: Failed with result '(?P<res>[^']+)'"),
     "Le service {unit} s'est arrêté en échec (résultat : {res})."),
    (re.compile(r"(?P<unit>[\w@.\-]+)\.service: Main process exited, code=(?P<code>\w+), status=(?P<status>\S+)"),
     "Le service {unit} s'est terminé anormalement (code={code}, statut={status})."),
    (re.compile(r"Start request repeated too quickly"),
     "Redémarrages trop rapprochés : le service a été abandonné par systemd."),
    (re.compile(r"(?P<unit>\S+) entered failed state"),
     "L'unité {unit} est passée en état d'échec."),
    (re.compile(r"Failed to start (?P<what>.+?)\.?$"),
     "Échec du démarrage : {what}."),
    (re.compile(r"(?:Out of memory: )?Killed process (?P<pid>\d+) \((?P<name>[^)]+)\)"),
     "Mémoire saturée : processus « {name} » (PID {pid}) tué par le noyau (OOM)."),
    (re.compile(r"Out of memory"),
     "Mémoire saturée (déclenchement de l'OOM-killer)."),
    (re.compile(r"Failed password for (?:invalid user )?(?P<user>\S+) from (?P<ip>[\w.:]+)"),
     "Échec d'authentification SSH pour « {user} » depuis {ip}."),
    (re.compile(r"Invalid user (?P<user>\S+) from (?P<ip>[\w.:]+)"),
     "Tentative SSH avec un utilisateur inexistant « {user} » depuis {ip}."),
    (re.compile(r"Accepted (?:password|publickey|keyboard-interactive[\w/]*) for (?P<user>\S+) from (?P<ip>[\w.:]+)"),
     "Connexion SSH réussie : « {user} » depuis {ip}."),
    (re.compile(r"metrics send error '(?P<tgt>[^']+)': (?P<err>.+)"),
     "Échec d'envoi des métriques vers {tgt} : {err}."),
    (re.compile(r"EXT4-fs error \(device (?P<dev>[^)]+)\)"),
     "Erreur du système de fichiers EXT4 sur {dev}."),
    (re.compile(r"[Rr]e-?mount(?:ing|ed)?\b.{0,60}read-only"),
     "Système de fichiers repassé en lecture seule (erreur disque probable)."),
    (re.compile(r"\[(?P<jail>[^\]]+)\]\s+Ban\s+(?P<ip>\S+)"),
     "fail2ban : {ip} banni (jail {jail})."),
    (re.compile(r"\[(?P<jail>[^\]]+)\]\s+Unban\s+(?P<ip>\S+)"),
     "fail2ban : {ip} débanni (jail {jail})."),
    (re.compile(r"Backup of VM (?P<id>\d+) failed"),
     "Échec de la sauvegarde de la VM/du conteneur {id}."),
    (re.compile(r"VM (?P<id>\d+) qga command '(?P<cmd>[^']+)' failed - got timeout"),
     "Agent QEMU de la VM {id} : commande « {cmd} » sans réponse (délai dépassé)."),
    (re.compile(r"(?P<proc>[\w./\-]+)\[\d+\]: segfault"),
     "Plantage mémoire (segfault) du processus {proc}."),
]


def translate_fr(text):
    """Réécrit en français les messages connus ; sinon renvoie le texte tel quel."""
    for rx, tmpl in _TR_RULES:
        m = rx.search(text)
        if m:
            d = {k: (v if v is not None else "?") for k, v in m.groupdict().items()}
            try:
                return tmpl.format(**d)
            except Exception:
                return text
    return text

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
    return sev, host, app, text


def coalesce_key(sev, host, app, text):
    norm = NUM_RE.sub("#", text)
    norm = re.sub(r"\s+", " ", norm).strip()
    return (sev, host, app, norm[:200])


class Aggregator:
    def __init__(self):
        self.lock = threading.Lock()
        self.groups = OrderedDict()

    def add(self, sev, host, app, text):
        key = coalesce_key(sev, host, app, text)
        now = time.time()
        with self.lock:
            g = self.groups.get(key)
            if g is None:
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
    do_tr = TRANSLATE_FR if translate is None else translate
    emoji = SEVERITY_EMOJI.get(g["sev"], "•")
    sevname = sev_fr(g["sev"]) if do_tr else SEVERITY_NAMES.get(g["sev"], str(g["sev"]))
    prefix = f"{emoji} `{sevname}` **{g['host']}**"
    app = g.get("app") or ""
    if app:
        prefix += f" `{app}`"
    if site_label:
        prefix = f"`{site_label}` " + prefix
    body = g["sample"]
    if do_tr:
        body = translate_fr(body)
    if len(body) > 1500:
        body = body[:1500] + "…"
    suffix = f"  _(x{g['count']})_" if g["count"] > 1 else ""
    return f"{prefix}: {body}{suffix}"
