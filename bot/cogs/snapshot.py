"""/snapshot — instantanés de la CONFIGURATION du serveur Discord et diff entre deux états.

Idée reprise de « Ultra Suite » (`/backup`, 2026-08-30), avec ses défauts corrigés : la
version JS n'enregistrait PAS les overwrites de permissions (la seule chose qui compte pour
la sécurité d'un serveur), son `load` était un stub et son insert cassé.

CE QUE FAIT CE COG
------------------
- `/snapshot creer [label]` : photographie la STRUCTURE du serveur — paramètres du guild,
  rôles (permissions en bitfield ET en liste de noms), catégories/salons (type, parent,
  sujet, NSFW, slowmode, bitrate…), OVERWRITES de permissions par rôle/membre sur chaque
  salon, emojis/stickers en métadonnées (nom/id, jamais les fichiers). Aucun message.
  Le JSON est DÉTERMINISTE (clés triées, ids en chaînes) pour être diffable à la main.
- `/snapshot liste`, `/snapshot voir <id>` (résumé + fichier JSON joint),
  `/snapshot supprimer <id>`.
- `/snapshot diff <a> [b]` : rapport lisible entre deux instantanés — ou entre `a` et
  l'état LIVE si `b` est omis — rôles créés/supprimés/renommés/permissions ±, salons
  créés/supprimés/déplacés/renommés, overwrites gagnés/perdus/modifiés. Au-delà de 3500
  caractères, le rapport complet part en fichier .txt joint.
- Instantané AUTOMATIQUE quotidien à 04:00 (Europe/Paris) SEULEMENT si quelque chose a
  changé depuis le dernier instantané (diff non vide) ; rétention `SNAPSHOT_KEEP` (défaut
  30) sur les instantanés automatiques — les manuels sont gardés jusqu'à `/snapshot
  supprimer`. Une ligne part dans #alertes UNIQUEMENT si le diff quotidien touche aux
  PERMISSIONS (rôles créés/supprimés, permissions de rôle, overwrites) : c'est un signal de
  sécurité (« qui a ouvert quoi ? »), le reste (renommages, sujets) reste silencieux.

CE QUE CE COG NE FAIT PAS
-------------------------
- AUCUNE restauration automatique. Rejouer un instantané sur un serveur vivant supprime/
  recrée des salons et des rôles (donc leurs ids, donc toutes les références du bot :
  GESTION_SERVERS, state["prov"], salons épinglés) — trop dangereux. Le produit est le
  diff lisible ; une restauration CIBLÉE (un overwrite, un rôle) pourra venir plus tard.
- Il n'enregistre pas les membres ni leurs rôles (intent `members` absent) et le dit.
- Il ne crée aucun salon : #alertes est celui de `ALERT_CHANNEL_ID` (posé par provision).

PIÈGES
------
- Les instantanés contiennent la structure de sécurité complète du serveur : commandes
  réservées au niveau OWNER (propriétaire du bot/guild, ADMIN_IDS, rôle O du serveur
  primaire), depuis la catégorie 🔒 Lock, réponses éphémères.
- `channel.overwrites` peut cibler un `discord.Object` (membre parti, rôle supprimé côté
  cache) : on l'enregistre par id avec un nom « indisponible », on n'invente pas.
- Les POSITIONS (rôles/salons) sont enregistrées mais NON diffées : une simple création
  décale toutes les autres et noierait le rapport. Le déplacement d'un salon vers une
  autre catégorie, lui, est diffé (parent).
- Le compteur de membres et l'horodatage vivent dans `meta`, exclu du diff, sinon le
  quotidien trouverait « un changement » chaque jour.
"""
import datetime as dt
import io
import json
import logging
import os
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.permissions import admin_check, is_breakglass, tier_of

log = logging.getLogger("discord-bot.snapshot")

SNAPSHOT_VERSION = 1
REPORT_LIMIT = 3500          # au-delà : fichier .txt joint
ALERT_EXCERPT = 1500         # extrait posté dans #alertes
LIST_MAX = 20
LABEL_RE = re.compile(r"[^a-z0-9-]+")

# Champs du guild suivis dans le diff (les autres — icône, bannière — sont stockés mais
# pas rapportés, ils ne disent rien de la sécurité ni de la structure).
GUILD_FIELDS = ("name", "owner_id", "verification_level", "explicit_content_filter",
                "default_notifications", "mfa_level", "afk_channel_id", "afk_timeout",
                "system_channel_id", "rules_channel_id", "public_updates_channel_id",
                "preferred_locale")
ROLE_FIELDS = ("color", "hoist", "mentionable", "managed")
CHANNEL_FIELDS = ("type", "topic", "nsfw", "slowmode_delay", "bitrate", "user_limit")


# ============================================================ sérialisation (pure)

def _enum_name(v):
    """Enum discord.py -> son nom ; None reste None ; le reste en chaîne."""
    if v is None:
        return None
    return getattr(v, "name", None) or str(v)


# discord.py itère `read_messages` pour le bit 1024 ; l'UI Discord l'appelle « view_channel »
# (et c'est ce nom que Nico lit dans les overwrites). On affiche le nom de l'UI.
PERM_ALIASES = {"read_messages": "view_channel"}


def perm_names(bits):
    """Bitfield -> liste TRIÉE des noms de permissions actives (lisible dans le JSON)."""
    try:
        p = discord.Permissions(int(bits))
    except (TypeError, ValueError):
        return []
    return sorted(PERM_ALIASES.get(name, name) for name, on in p if on)


def _perm_block(perms):
    bits = int(getattr(perms, "value", perms) or 0)
    return {"bits": bits, "names": perm_names(bits)}


def _target_kind(target):
    """« role » / « member » pour la cible d'un overwrite, sans dépendre d'un isinstance
    (fakes de tests, `discord.Object` pour une cible disparue du cache)."""
    if isinstance(target, discord.Role):
        return "role"
    if isinstance(target, (discord.Member, discord.User)):
        return "member"
    if isinstance(target, discord.Object):
        t = getattr(target, "type", None)
        return "role" if t is discord.Role else "member"
    # duck-typing : un rôle a `hoist`, un membre non
    return "role" if hasattr(target, "hoist") else "member"


def serialize_overwrites(channel):
    """Overwrites d'un salon -> dict {"<kind>:<id>": {...}}, clés stables et triables."""
    out = {}
    for target, ow in (getattr(channel, "overwrites", None) or {}).items():
        try:
            allow, deny = ow.pair()
        except AttributeError:
            continue
        kind = _target_kind(target)
        tid = str(getattr(target, "id", "?"))
        out[f"{kind}:{tid}"] = {
            "kind": kind,
            "id": tid,
            "name": getattr(target, "name", None) or "indisponible",
            "allow": _perm_block(allow),
            "deny": _perm_block(deny),
        }
    return out


def serialize_role(r):
    color = getattr(r, "color", None)
    return {
        "id": str(r.id),
        "name": r.name,
        "color": int(getattr(color, "value", color) or 0),
        "hoist": bool(getattr(r, "hoist", False)),
        "mentionable": bool(getattr(r, "mentionable", False)),
        "managed": bool(getattr(r, "managed", False)),
        "position": int(getattr(r, "position", 0) or 0),
        "permissions": _perm_block(getattr(r, "permissions", 0)),
    }


def serialize_channel(ch):
    cat = getattr(ch, "category", None)
    return {
        "id": str(ch.id),
        "name": ch.name,
        "type": _enum_name(getattr(ch, "type", None)),
        "parent_id": str(cat.id) if cat is not None else None,
        "position": int(getattr(ch, "position", 0) or 0),
        "topic": getattr(ch, "topic", None),
        "nsfw": bool(getattr(ch, "nsfw", False)),
        "slowmode_delay": int(getattr(ch, "slowmode_delay", 0) or 0),
        "bitrate": getattr(ch, "bitrate", None),
        "user_limit": getattr(ch, "user_limit", None),
        "overwrites": serialize_overwrites(ch),
    }


def serialize_guild(guild, *, now=None, members_intent=None):
    """Guild (ou fake) -> dict d'instantané, déterministe (dicts indexés par id-chaîne)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    def _id(x):
        return str(x.id) if x is not None else None
    settings = {
        "id": str(guild.id),
        "name": guild.name,
        "owner_id": str(getattr(guild, "owner_id", None) or ""),
        "verification_level": _enum_name(getattr(guild, "verification_level", None)),
        "explicit_content_filter": _enum_name(getattr(guild, "explicit_content_filter", None)),
        "default_notifications": _enum_name(getattr(guild, "default_notifications", None)),
        "mfa_level": _enum_name(getattr(guild, "mfa_level", None)),
        "afk_channel_id": _id(getattr(guild, "afk_channel", None)),
        "afk_timeout": getattr(guild, "afk_timeout", None),
        "system_channel_id": _id(getattr(guild, "system_channel", None)),
        "rules_channel_id": _id(getattr(guild, "rules_channel", None)),
        "public_updates_channel_id": _id(getattr(guild, "public_updates_channel", None)),
        "preferred_locale": _enum_name(getattr(guild, "preferred_locale", None)),
        "premium_tier": getattr(guild, "premium_tier", None),
        "features": sorted(str(f) for f in (getattr(guild, "features", None) or [])),
        "icon": getattr(getattr(guild, "icon", None), "key", None),
    }
    roles = {str(r.id): serialize_role(r) for r in (getattr(guild, "roles", None) or [])}
    chans = {str(c.id): serialize_channel(c) for c in (getattr(guild, "channels", None) or [])}
    emojis = {str(e.id): {"id": str(e.id), "name": e.name,
                          "animated": bool(getattr(e, "animated", False)),
                          "managed": bool(getattr(e, "managed", False))}
              for e in (getattr(guild, "emojis", None) or [])}
    stickers = {str(s.id): {"id": str(s.id), "name": s.name,
                            "format": _enum_name(getattr(s, "format", None))}
                for s in (getattr(guild, "stickers", None) or [])}
    n_ow = sum(len(c["overwrites"]) for c in chans.values())
    return {
        "version": SNAPSHOT_VERSION,
        "guild": settings,
        "roles": roles,
        "channels": chans,
        "emojis": emojis,
        "stickers": stickers,
        # meta = hors diff (varie sans que la structure change)
        "meta": {
            "taken_at": now.isoformat(timespec="seconds"),
            "member_count": getattr(guild, "member_count", None),
            "members_intent": members_intent,
            "counts": {"roles": len(roles), "channels": len(chans), "overwrites": n_ow,
                       "emojis": len(emojis), "stickers": len(stickers)},
        },
    }


def dump_json(snap):
    """Sérialisation canonique : clés triées, UTF-8 lisible, fin de ligne finale."""
    return json.dumps(snap, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


# ============================================================ diff (pure)

def _names_delta(old, new):
    o, n = set(old or []), set(new or [])
    return sorted(n - o), sorted(o - n)


def diff_snapshots(a, b):
    """Diff structurel a -> b. Renvoie un dict de listes ; `diff_is_empty()` dit s'il
    est vide, `touches_permissions()` s'il concerne la sécurité."""
    out = {
        "guild": [],
        "roles": {"created": [], "deleted": [], "renamed": [], "perms": [], "changed": []},
        "channels": {"created": [], "deleted": [], "renamed": [], "moved": [], "changed": []},
        "overwrites": {"gained": [], "lost": [], "changed": []},
        "emojis": {"created": [], "deleted": []},
        "stickers": {"created": [], "deleted": []},
    }
    ga, gb = a.get("guild") or {}, b.get("guild") or {}
    for f in GUILD_FIELDS:
        if ga.get(f) != gb.get(f):
            out["guild"].append({"field": f, "old": ga.get(f), "new": gb.get(f)})

    ra, rb = a.get("roles") or {}, b.get("roles") or {}
    for rid in sorted(set(rb) - set(ra)):
        r = rb[rid]
        out["roles"]["created"].append({"id": rid, "name": r["name"],
                                        "perms": r["permissions"]["names"]})
    for rid in sorted(set(ra) - set(rb)):
        r = ra[rid]
        out["roles"]["deleted"].append({"id": rid, "name": r["name"],
                                        "perms": r["permissions"]["names"]})
    for rid in sorted(set(ra) & set(rb)):
        x, y = ra[rid], rb[rid]
        if x["name"] != y["name"]:
            out["roles"]["renamed"].append({"id": rid, "old": x["name"], "new": y["name"]})
        if x["permissions"]["bits"] != y["permissions"]["bits"]:
            added, removed = _names_delta(x["permissions"]["names"], y["permissions"]["names"])
            out["roles"]["perms"].append({"id": rid, "name": y["name"],
                                          "added": added, "removed": removed})
        for f in ROLE_FIELDS:
            if x.get(f) != y.get(f):
                out["roles"]["changed"].append({"id": rid, "name": y["name"], "field": f,
                                                "old": x.get(f), "new": y.get(f)})

    ca, cb = a.get("channels") or {}, b.get("channels") or {}

    def _parent_name(snap, pid):
        if pid is None:
            return None
        p = (snap.get("channels") or {}).get(pid)
        return p["name"] if p else f"#{pid}"

    for cid in sorted(set(cb) - set(ca)):
        c = cb[cid]
        out["channels"]["created"].append({"id": cid, "name": c["name"], "type": c["type"],
                                           "parent": _parent_name(b, c["parent_id"])})
        for key, ow in sorted(c["overwrites"].items()):
            out["overwrites"]["gained"].append(_ow_entry(c, ow))
    for cid in sorted(set(ca) - set(cb)):
        c = ca[cid]
        out["channels"]["deleted"].append({"id": cid, "name": c["name"], "type": c["type"],
                                           "parent": _parent_name(a, c["parent_id"])})
        for key, ow in sorted(c["overwrites"].items()):
            out["overwrites"]["lost"].append(_ow_entry(c, ow))
    for cid in sorted(set(ca) & set(cb)):
        x, y = ca[cid], cb[cid]
        if x["name"] != y["name"]:
            out["channels"]["renamed"].append({"id": cid, "old": x["name"], "new": y["name"]})
        if x["parent_id"] != y["parent_id"]:
            out["channels"]["moved"].append({"id": cid, "name": y["name"],
                                             "old_parent": _parent_name(a, x["parent_id"]),
                                             "new_parent": _parent_name(b, y["parent_id"])})
        for f in CHANNEL_FIELDS:
            if x.get(f) != y.get(f):
                out["channels"]["changed"].append({"id": cid, "name": y["name"], "field": f,
                                                   "old": x.get(f), "new": y.get(f)})
        oa, ob = x.get("overwrites") or {}, y.get("overwrites") or {}
        for key in sorted(set(ob) - set(oa)):
            out["overwrites"]["gained"].append(_ow_entry(y, ob[key]))
        for key in sorted(set(oa) - set(ob)):
            out["overwrites"]["lost"].append(_ow_entry(y, oa[key]))
        for key in sorted(set(oa) & set(ob)):
            p, q = oa[key], ob[key]
            if p["allow"]["bits"] == q["allow"]["bits"] and p["deny"]["bits"] == q["deny"]["bits"]:
                continue
            aa, ar = _names_delta(p["allow"]["names"], q["allow"]["names"])
            da, dr = _names_delta(p["deny"]["names"], q["deny"]["names"])
            e = _ow_entry(y, q)
            e.update({"allow_added": aa, "allow_removed": ar,
                      "deny_added": da, "deny_removed": dr})
            out["overwrites"]["changed"].append(e)

    for kind in ("emojis", "stickers"):
        ea, eb = a.get(kind) or {}, b.get(kind) or {}
        for k in sorted(set(eb) - set(ea)):
            out[kind]["created"].append({"id": k, "name": eb[k]["name"]})
        for k in sorted(set(ea) - set(eb)):
            out[kind]["deleted"].append({"id": k, "name": ea[k]["name"]})
    return out


def _ow_entry(chan, ow):
    return {"channel_id": chan["id"], "channel": chan["name"], "kind": ow["kind"],
            "target_id": ow["id"], "target": ow["name"],
            "allow": list(ow["allow"]["names"]), "deny": list(ow["deny"]["names"])}


def diff_is_empty(d):
    for v in d.values():
        if isinstance(v, dict):
            if any(v.values()):
                return False
        elif v:
            return False
    return True


def touches_permissions(d):
    """True si le diff concerne la sécurité : rôle créé/supprimé, permissions de rôle,
    overwrites (gagnés/perdus/modifiés), MFA du serveur."""
    r = d.get("roles") or {}
    o = d.get("overwrites") or {}
    if r.get("created") or r.get("deleted") or r.get("perms"):
        return True
    if o.get("gained") or o.get("lost") or o.get("changed"):
        return True
    return any(g.get("field") == "mfa_level" for g in d.get("guild") or [])


def diff_counts(d):
    """Nombre total de lignes de changement (pour les résumés)."""
    n = len(d.get("guild") or [])
    for k in ("roles", "channels", "overwrites", "emojis", "stickers"):
        n += sum(len(v) for v in (d.get(k) or {}).values())
    return n


# ============================================================ rendu texte (pure)

def _s(x, n=60):
    """Texte venu de Discord : pas de backtick ni de saut de ligne, borné."""
    s = str(x if x is not None else "—").replace("`", "'").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _plus_minus(added, removed):
    parts = []
    if added:
        parts.append("+" + " +".join(added))
    if removed:
        parts.append("-" + " -".join(removed))
    return " ".join(parts) or "(aucun)"


def render_diff(d, label_a="A", label_b="B"):
    """Diff -> rapport lisible en français (une ligne par changement, sections vides omises)."""
    L = [f"Diff {label_a} → {label_b}"]
    if diff_is_empty(d):
        L.append("Aucune différence.")
        return "\n".join(L)
    if d.get("guild"):
        L.append("\n## Serveur")
        for g in d["guild"]:
            L.append(f"~ {g['field']} : {_s(g['old'])} → {_s(g['new'])}")
    r = d["roles"]
    if any(r.values()):
        L.append("\n## Rôles")
        for e in r["created"]:
            L.append(f"+ rôle créé « {_s(e['name'])} » ({e['id']}) : "
                     f"{', '.join(e['perms']) or 'aucune permission'}")
        for e in r["deleted"]:
            L.append(f"- rôle supprimé « {_s(e['name'])} » ({e['id']})")
        for e in r["renamed"]:
            L.append(f"~ rôle renommé « {_s(e['old'])} » → « {_s(e['new'])} » ({e['id']})")
        for e in r["perms"]:
            L.append(f"! permissions « {_s(e['name'])} » : {_plus_minus(e['added'], e['removed'])}")
        for e in r["changed"]:
            L.append(f"~ rôle « {_s(e['name'])} » {e['field']} : {_s(e['old'])} → {_s(e['new'])}")
    c = d["channels"]
    if any(c.values()):
        L.append("\n## Salons")
        for e in c["created"]:
            L.append(f"+ salon créé #{_s(e['name'])} ({e['type']}"
                     f"{', dans « ' + _s(e['parent']) + ' »' if e['parent'] else ''})")
        for e in c["deleted"]:
            L.append(f"- salon supprimé #{_s(e['name'])} ({e['type']})")
        for e in c["renamed"]:
            L.append(f"~ salon renommé #{_s(e['old'])} → #{_s(e['new'])}")
        for e in c["moved"]:
            L.append(f"~ salon #{_s(e['name'])} déplacé : « {_s(e['old_parent'])} » → "
                     f"« {_s(e['new_parent'])} »")
        for e in c["changed"]:
            L.append(f"~ salon #{_s(e['name'])} {e['field']} : {_s(e['old'])} → {_s(e['new'])}")
    o = d["overwrites"]
    if any(o.values()):
        L.append("\n## Overwrites de permissions")
        for e in o["gained"]:
            L.append(f"+ #{_s(e['channel'])} {e['kind']} « {_s(e['target'])} » : "
                     f"allow[{', '.join(e['allow']) or '—'}] deny[{', '.join(e['deny']) or '—'}]")
        for e in o["lost"]:
            L.append(f"- #{_s(e['channel'])} {e['kind']} « {_s(e['target'])} » : "
                     f"allow[{', '.join(e['allow']) or '—'}] deny[{', '.join(e['deny']) or '—'}]")
        for e in o["changed"]:
            L.append(f"! #{_s(e['channel'])} {e['kind']} « {_s(e['target'])} » : "
                     f"allow {_plus_minus(e['allow_added'], e['allow_removed'])} ; "
                     f"deny {_plus_minus(e['deny_added'], e['deny_removed'])}")
    for kind, lab in (("emojis", "Emojis"), ("stickers", "Stickers")):
        k = d[kind]
        if any(k.values()):
            L.append(f"\n## {lab}")
            for e in k["created"]:
                L.append(f"+ {_s(e['name'])} ({e['id']})")
            for e in k["deleted"]:
                L.append(f"- {_s(e['name'])} ({e['id']})")
    return "\n".join(L)


def truncate_report(text, limit=REPORT_LIMIT):
    """(court, complet_ou_None) : coupé à une frontière de ligne, avec une mention du reste."""
    if len(text) <= limit:
        return text, None
    cut = text[:limit]
    nl = cut.rfind("\n")
    if nl > limit // 2:
        cut = cut[:nl]
    rest = text.count("\n") - cut.count("\n")
    return cut + f"\n… ({rest} lignes de plus, rapport complet en pièce jointe)", text


# ============================================================ stockage (pure)

def snapshots_dir(cfg):
    """Dossier des instantanés : à côté de state.json (`<state_dir>/snapshots/`)."""
    sp = getattr(cfg, "state_path", None) or "/var/lib/discord-bot/state.json"
    return os.path.join(os.path.dirname(sp), "snapshots")


def clean_label(label):
    s = LABEL_RE.sub("-", str(label or "").strip().lower()).strip("-")
    return s[:32]


def make_id(now, label=None):
    sid = now.strftime("%Y-%m-%d_%H%M%S")
    lab = clean_label(label)
    return f"{sid}-{lab}" if lab else sid


VALID_ID = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}(-[a-z0-9-]{1,32})?$")


class SnapshotStore:
    """Fichiers JSON + index dans `state["snapshots"]` (liste de dicts)."""

    def __init__(self, state, base_dir):
        self.state = state
        self.base = base_dir

    def _path(self, guild_id, sid):
        if not VALID_ID.match(sid):
            raise ValueError(f"identifiant d'instantané invalide : {sid!r}")
        return os.path.join(self.base, str(guild_id), f"{sid}.json")

    def index(self, guild_id=None):
        idx = list(self.state.get("snapshots") or [])
        if guild_id is not None:
            idx = [e for e in idx if str(e.get("guild")) == str(guild_id)]
        return sorted(idx, key=lambda e: e["id"])

    def _write_index(self, entries):
        self.state.set("snapshots", sorted(entries, key=lambda e: e["id"]))

    def save(self, guild_id, snap, *, label=None, author="auto", auto=False, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        sid = make_id(now.astimezone() if now.tzinfo else now, label)
        entries = [e for e in self.index() if e["id"] != sid or str(e.get("guild")) != str(guild_id)]
        path = self._path(guild_id, sid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = dump_json(snap)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        counts = (snap.get("meta") or {}).get("counts") or {}
        entry = {"id": sid, "guild": str(guild_id), "date": now.isoformat(timespec="seconds"),
                 "label": clean_label(label) or None, "author": str(author), "auto": bool(auto),
                 "bytes": len(data.encode("utf-8")),
                 "roles": counts.get("roles", 0), "channels": counts.get("channels", 0),
                 "overwrites": counts.get("overwrites", 0)}
        entries.append(entry)
        self._write_index(entries)
        return entry

    def entry(self, guild_id, sid):
        return next((e for e in self.index(guild_id) if e["id"] == sid), None)

    def load(self, guild_id, sid):
        """Instantané ou None (fichier absent/illisible → None + journal, pas d'invention)."""
        try:
            with open(self._path(guild_id, sid), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            log.warning("snapshot %s illisible: %s", sid, e)
            return None

    def latest(self, guild_id):
        idx = self.index(guild_id)
        return idx[-1] if idx else None

    def delete(self, guild_id, sid):
        try:
            os.remove(self._path(guild_id, sid))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("snapshot %s : suppression du fichier impossible: %s", sid, e)
        before = self.index()
        after = [e for e in before if not (e["id"] == sid and str(e.get("guild")) == str(guild_id))]
        self._write_index(after)
        return len(after) < len(before)

    def prune(self, guild_id, keep):
        """Ne garde que les `keep` instantanés AUTOMATIQUES les plus récents ; les manuels
        ne sont jamais élagués. Renvoie les ids supprimés."""
        autos = [e for e in self.index(guild_id) if e.get("auto")]
        gone = []
        if keep < 0:
            keep = 0
        for e in autos[:max(0, len(autos) - keep)]:
            self.delete(guild_id, e["id"])
            gone.append(e["id"])
        return gone


# ============================================================ cog

def _paris_time(hour=4):
    try:
        from zoneinfo import ZoneInfo
        return dt.time(hour=hour, tzinfo=ZoneInfo("Europe/Paris"))
    except Exception:  # noqa: BLE001 — tzdata absent : on aligne sur l'heure locale
        return dt.time(hour=hour, tzinfo=dt.datetime.now().astimezone().tzinfo)


class Snapshot(commands.Cog):
    """/snapshot : instantanés de configuration + diff, quotidien silencieux sauf permissions."""

    snapshot = app_commands.Group(
        name="snapshot",
        description="Instantanés de la configuration du serveur Discord (rôles, salons, permissions) + diff.")

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.store = SnapshotStore(bot.state, snapshots_dir(bot.cfg))
        self.keep = int(getattr(self.cfg, "snapshot_keep", 30) or 30)
        self.auto_enabled = bool(getattr(self.cfg, "snapshot_auto", True))
        self.last_error = None

    async def cog_load(self):
        if not self.auto_enabled:
            log.warning("snapshot: SNAPSHOT_AUTO=false — pas d'instantané quotidien")
            return
        self.daily.start()

    async def cog_unload(self):
        self.daily.cancel()

    # ------------------------------------------------------------ helpers
    def _guild(self, itx=None):
        gid = getattr(self.cfg, "guild_id", 0)
        g = self.bot.get_guild(gid) if gid else None
        return g or (itx.guild if itx is not None else None)

    def _owner_ok(self, itx):
        """Niveau OWNER : break-glass (propriétaire/ADMIN_IDS) ou rôle O du serveur primaire."""
        return is_breakglass(self.cfg, itx) or tier_of(self.cfg, itx, None) == "O"

    def _live(self, guild):
        return serialize_guild(guild, members_intent=bool(getattr(self.bot.intents, "members", False)))

    async def _refuse_unless_owner(self, itx):
        if self._owner_ok(itx):
            return True
        from ..core.permissions import log_refusal
        log_refusal(itx, "snapshot: niveau Owner requis")
        await itx.response.send_message(
            "⛔ Les instantanés contiennent la structure de sécurité du serveur : réservé à "
            "l'Owner (rôle O) ou au propriétaire du bot.", ephemeral=True)
        return False

    @staticmethod
    def _entry_line(e):
        d = e.get("date", "")[:16].replace("T", " ")
        who = "auto" if e.get("auto") else f"<@{e.get('author')}>"
        lab = f" « {e['label']} »" if e.get("label") else ""
        return (f"`{e['id']}`{lab} — {d} · {who} · {e.get('roles', 0)} rôles, "
                f"{e.get('channels', 0)} salons, {e.get('overwrites', 0)} overwrites · "
                f"{fmt.humanize_bytes(e.get('bytes', 0))}")

    async def _id_ac(self, itx, current):
        g = self._guild(itx)
        if g is None:
            return []
        cur = (current or "").lower()
        out = []
        for e in reversed(self.store.index(g.id)):
            if cur and cur not in e["id"]:
                continue
            out.append(app_commands.Choice(name=e["id"][:100], value=e["id"]))
            if len(out) >= 25:
                break
        return out

    def _json_file(self, sid, snap):
        return discord.File(io.BytesIO(dump_json(snap).encode("utf-8")), filename=f"snapshot-{sid}.json")

    # ------------------------------------------------------------ commandes
    @snapshot.command(name="creer", description="Owner : photographier la configuration du serveur maintenant.")
    @app_commands.describe(label="Étiquette courte (a-z, 0-9, tirets) ajoutée à l'identifiant")
    @admin_check(scope="primary")
    async def creer(self, itx: discord.Interaction, label: str = None):
        if not await self._refuse_unless_owner(itx):
            return
        guild = self._guild(itx)
        if guild is None:
            await itx.response.send_message("Serveur indisponible.", ephemeral=True)
            return
        await itx.response.defer(ephemeral=True, thinking=True)
        snap = self._live(guild)
        try:
            e = self.store.save(guild.id, snap, label=label, author=itx.user.id, auto=False)
        except OSError as ex:
            await itx.followup.send(f"❌ Écriture impossible : {_s(ex, 200)}", ephemeral=True)
            return
        self.bot.audit.record(user=itx.user.id, action="snapshot.create", target=e["id"], result="ok")
        emb = discord.Embed(title="📸 Instantané créé", color=fmt.GREEN, description=self._entry_line(e))
        if not snap["meta"].get("members_intent"):
            emb.set_footer(text="Intent members absent : membres et attributions de rôles non enregistrés.")
        await itx.followup.send(embed=emb, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @snapshot.command(name="liste", description="Owner : lister les instantanés existants.")
    @admin_check(scope="primary")
    async def liste(self, itx: discord.Interaction):
        if not await self._refuse_unless_owner(itx):
            return
        guild = self._guild(itx)
        idx = self.store.index(guild.id) if guild else []
        if not idx:
            await itx.response.send_message("Aucun instantané. `/snapshot creer` pour en prendre un.", ephemeral=True)
            return
        lines = [self._entry_line(e) for e in reversed(idx[-LIST_MAX:])]
        more = f"\n… et {len(idx) - LIST_MAX} plus anciens" if len(idx) > LIST_MAX else ""
        emb = discord.Embed(title=f"📸 Instantanés ({len(idx)})", color=fmt.BLURPLE,
                            description=("\n".join(lines) + more)[:4000])
        emb.set_footer(text=f"Rétention auto : {self.keep} · dossier {self.store.base}")
        await itx.response.send_message(embed=emb, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @snapshot.command(name="voir", description="Owner : résumé d'un instantané + fichier JSON.")
    @app_commands.describe(id="Identifiant (voir /snapshot liste)")
    @app_commands.autocomplete(id=_id_ac)
    @admin_check(scope="primary")
    async def voir(self, itx: discord.Interaction, id: str):
        if not await self._refuse_unless_owner(itx):
            return
        guild = self._guild(itx)
        e = self.store.entry(guild.id, id) if guild else None
        snap = self.store.load(guild.id, id) if e else None
        if e is None or snap is None:
            await itx.response.send_message(f"❌ Instantané `{_s(id)}` introuvable ou illisible.", ephemeral=True)
            return
        g = snap.get("guild") or {}
        emb = discord.Embed(title=f"📸 {e['id']}", color=fmt.BLURPLE, description=self._entry_line(e))
        emb.add_field(name="Serveur", value=f"{_s(g.get('name'))} · vérif {g.get('verification_level')} · "
                                            f"MFA {g.get('mfa_level')}", inline=False)
        emb.add_field(name="Emojis / stickers",
                      value=f"{len(snap.get('emojis') or {})} / {len(snap.get('stickers') or {})}", inline=True)
        emb.add_field(name="Membres (compteur)", value=str((snap.get("meta") or {}).get("member_count") or "indisponible"),
                      inline=True)
        await itx.response.send_message(embed=emb, file=self._json_file(e["id"], snap), ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    @snapshot.command(name="diff", description="Owner : différences entre deux instantanés (ou un instantané et l'état actuel).")
    @app_commands.describe(a="Instantané de départ", b="Instantané d'arrivée (omis = état LIVE actuel)")
    @app_commands.autocomplete(a=_id_ac, b=_id_ac)
    @admin_check(scope="primary")
    async def diff(self, itx: discord.Interaction, a: str, b: str = None):
        if not await self._refuse_unless_owner(itx):
            return
        guild = self._guild(itx)
        if guild is None:
            await itx.response.send_message("Serveur indisponible.", ephemeral=True)
            return
        sa = self.store.load(guild.id, a) if self.store.entry(guild.id, a) else None
        if sa is None:
            await itx.response.send_message(f"❌ Instantané `{_s(a)}` introuvable.", ephemeral=True)
            return
        if b:
            sb = self.store.load(guild.id, b) if self.store.entry(guild.id, b) else None
            if sb is None:
                await itx.response.send_message(f"❌ Instantané `{_s(b)}` introuvable.", ephemeral=True)
                return
            lb = b
        else:
            sb, lb = self._live(guild), "LIVE"
        d = diff_snapshots(sa, sb)
        text = render_diff(d, a, lb)
        short, full = truncate_report(text)
        color = fmt.ORANGE if touches_permissions(d) else (fmt.GREEN if diff_is_empty(d) else fmt.BLURPLE)
        emb = discord.Embed(title=f"🔍 Diff {a} → {lb}", color=color,
                            description=f"```\n{short[:3900]}\n```")
        emb.set_footer(text=f"{diff_counts(d)} changement(s)"
                            + (" · touche aux PERMISSIONS" if touches_permissions(d) else ""))
        kw = {}
        if full is not None:
            kw["file"] = discord.File(io.BytesIO(full.encode("utf-8")), filename=f"diff-{a}-{lb}.txt")
        await itx.response.send_message(embed=emb, ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none(), **kw)

    @snapshot.command(name="supprimer", description="Owner : supprimer un instantané.")
    @app_commands.describe(id="Identifiant (voir /snapshot liste)")
    @app_commands.autocomplete(id=_id_ac)
    @admin_check(scope="primary")
    async def supprimer(self, itx: discord.Interaction, id: str):
        if not await self._refuse_unless_owner(itx):
            return
        guild = self._guild(itx)
        ok = self.store.delete(guild.id, id) if (guild and self.store.entry(guild.id, id)) else False
        self.bot.audit.record(user=itx.user.id, action="snapshot.delete", target=id, result="ok" if ok else "absent")
        await itx.response.send_message(
            f"🗑️ Instantané `{_s(id)}` supprimé." if ok else f"❌ Instantané `{_s(id)}` introuvable.",
            ephemeral=True)

    # ------------------------------------------------------------ quotidien
    async def run_daily(self, guild, now=None):
        """Un cycle : diff vs dernier instantané ; si non vide → fichier + élagage ; si les
        permissions bougent → #alertes. Renvoie (entry_ou_None, diff_ou_None)."""
        live = self._live(guild)
        last = self.store.latest(guild.id)
        prev = self.store.load(guild.id, last["id"]) if last else None
        if prev is not None:
            d = diff_snapshots(prev, live)
            if diff_is_empty(d):
                log.info("snapshot quotidien : rien n'a changé depuis %s", last["id"])
                return None, d
        else:
            d = None
        e = self.store.save(guild.id, live, author="auto", auto=True, now=now)
        gone = self.store.prune(guild.id, self.keep)
        log.info("snapshot quotidien %s écrit (%d élagué(s))", e["id"], len(gone))
        if d is not None and touches_permissions(d):
            await self._alert(last["id"], e["id"], d)
        return e, d

    async def _alert(self, old_id, new_id, d):
        cid = getattr(self.cfg, "alerts_channel_id", None) or getattr(self.cfg, "alert_channel_id", 0)
        if not cid:
            return
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception as ex:  # noqa: BLE001
                log.warning("snapshot: #alertes (%s) injoignable: %s", cid, ex)
                return
        text = render_diff(d, old_id, new_id)
        short, full = truncate_report(text, ALERT_EXCERPT)
        emb = discord.Embed(
            title="🔐 Permissions Discord modifiées depuis le dernier instantané",
            description=f"```\n{short}\n```",
            color=fmt.ORANGE)
        emb.set_footer(text=f"{diff_counts(d)} changement(s) · /snapshot diff {old_id} {new_id}")
        kw = {}
        if full is not None:
            kw["file"] = discord.File(io.BytesIO(full.encode("utf-8")), filename=f"diff-{old_id}-{new_id}.txt")
        await ch.send(embed=emb, allowed_mentions=discord.AllowedMentions.none(), **kw)

    @tasks.loop(time=_paris_time(4))
    async def daily(self):
        guild = self._guild()
        if guild is None:
            log.warning("snapshot quotidien : guild %s indisponible", getattr(self.cfg, "guild_id", None))
            return
        try:
            await self.run_daily(guild)
            self.last_error = None
        except Exception as ex:  # noqa: BLE001 — la boucle doit survivre
            self.last_error = str(ex)
            log.exception("snapshot quotidien en échec")

    @daily.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Snapshot(bot))
