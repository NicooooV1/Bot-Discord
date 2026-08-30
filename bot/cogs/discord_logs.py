"""#discord-logs — journal des événements du SERVEUR DISCORD lui-même (pas du homelab).

Demande Nico 2026-08-30 : porter le module « logs » + `events/*.js` d'Ultra Suite (dont la
moitié ne fonctionnait pas) vers Edmine, « en mieux ». Ici, un seul cog, un seul salon,
et surtout : le diff des PERMISSIONS (overwrites de salon, permissions de rôle) — c'est
le signal de sécurité n°1 sur un serveur privé où famille et amis sont invités.

CE QUE FAIT CE COG
  - salon `#discord-logs` dans « 🔒 Lock R820 » (contenu de messages supprimés, codes
    d'invitation, raisons de ban : sensible), créé via `channels.ensure_channel` s'il
    manque, id persisté dans `state["discord_logs"]["channel"]` ; message ÉPINGLÉ d'état
    (`ui.pin_edit`) : intents actifs, permissions du bot, compteurs, taille de la file ;
  - journalise (listeners) : messages supprimés / édités / supprimés en masse, arrivées
    (âge du compte, ⚠️ si < 7 j, rôle de bienvenue `WELCOME_ROLE_ID`), départs (expulsion
    détectée par le journal d'audit dans une fenêtre de 10 s), bans / débans, rôles
    ajoutés/retirés, pseudo, timeout, salons créés/supprimés/modifiés (nom, sujet, NSFW,
    slowmode, catégorie, **overwrites** : quel rôle/membre a gagné ou perdu quoi), rôles
    créés/supprimés/modifiés (nom, couleur, hoist, mentionable, **permissions** ajoutées
    et retirées), invitations créées/supprimées, webhooks, fils (créés/supprimés/
    archivés), emojis et stickers, serveur (nom, icône, vérification, description),
    vocal (arrivée / départ / déplacement, compact), bots ajoutés (⚠️ + qui l'a ajouté) ;
  - anti-spam : file + envoi par lots (≤ 10 embeds par message, 1 envoi toutes les 2 s) ;
    `allowed_mentions=none()` partout ;
  - signaux de sécurité (invitation, webhook, overwrites/permissions de rôle, bot ajouté,
    compte < 7 j) → une ligne courte dans #alertes (`cfg.alert_channel_id`, même pied de
    page « alerte: <clé> » que les autres cogs : le bouton Snooze fonctionne), avec un
    anti-doublon par clé (`DISCORD_LOGS_ALERT_COOLDOWN`, défaut 300 s) ;
  - compteurs par type et par JOUR dans `state["discord_logs_counts"]` (purge > 90 j),
    base d'un futur rapport ;
  - `/journal-discord statut` (lecture) et `/journal-discord test` (propriétaire /
    ADMIN_IDS uniquement : poste un embed de test dans le salon).

CE QUE CE COG NE FAIT PAS
  - il ne journalise JAMAIS les messages/fils qui se passent DANS #discord-logs (boucle) ;
    en revanche une modification des PERMISSIONS de #discord-logs est journalisée : c'est
    précisément un signal de sécurité ;
  - il n'invente pas d'exécutant : sans la permission « Voir les journaux d'audit », ou
    sans entrée d'audit dans la fenêtre de 10 s, il écrit « exécutant indisponible » ;
  - sans l'intent privilégié `members`, `on_member_join/remove/update` ne se déclenchent
    PAS : le message épinglé le dit (« intent Membres désactivé : arrivées/départs non
    journalisés ») ; sans `message_content`, le contenu est écrit « indisponible (intent
    Contenu des messages désactivé) » mais l'événement est journalisé quand même (auteur,
    salon, lien, pièces jointes) ;
  - il ne modère rien (aucune suppression, aucun ban) : lecture + un seul ajout de rôle
    (bienvenue), refus Discord journalisé tel quel.

PIÈGES CONNUS
  - `on_message_delete` / `on_message_edit` ne se déclenchent que pour les messages EN
    CACHE (1000 derniers) : on écoute les événements RAW, qui portent le message en cache
    quand il y est, et on dit « hors cache : auteur/contenu indisponibles » sinon ;
  - une édition RAW sans `edited_timestamp` est un déploiement d'embed (aperçu de lien),
    pas une édition humaine : ignorée. Idem une édition dont le contenu est identique ;
  - le journal d'audit fusionne les suppressions de messages (compteur incrémenté sur une
    entrée ancienne) : on n'affirme un exécutant QUE si l'entrée date de < 10 s ;
  - `on_member_ban` précède `on_member_remove` : on mémorise les bans récents pour ne pas
    journaliser un « départ » en plus du ban ;
  - INVITE_CREATE n'est reçu que si le bot a « Gérer le serveur » ou « Gérer les salons »
    sur le salon : sinon les invitations sont simplement invisibles (dit dans l'épinglé).
"""
import datetime as dt
import logging
import time
from collections import Counter, deque

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import channels
from ..core import format as fmt
from ..core.bg import guard_cog_loops
from ..core.permissions import is_breakglass, read_check
from ..core.ui import pin_edit
from ..views.alertaction import alert_snoozed

log = logging.getLogger("discord-bot.discord_logs")

STATE_KEY = "discord_logs"            # {"channel": id, "message": id épinglé}
COUNTS_KEY = "discord_logs_counts"    # {"AAAA-MM-JJ": {type: n}}
COUNTS_KEEP_DAYS = 90
CLIP = 900                            # avant/après d'un message : 900 caractères max
BATCH = 10                            # embeds max par message Discord
FLUSH_SECONDS = 2                     # 1 envoi toutes les 2 s au plus
AUDIT_WINDOW_S = 10                   # fenêtre de rattachement d'une entrée d'audit
YOUNG_ACCOUNT_DAYS = 7

# Libellés français des permissions Discord (les absentes sont rendues par leur nom API).
PERM_FR = {
    "administrator": "Administrateur", "view_audit_log": "Voir les journaux d'audit",
    "manage_guild": "Gérer le serveur", "manage_roles": "Gérer les rôles",
    "manage_channels": "Gérer les salons", "kick_members": "Expulser des membres",
    "ban_members": "Bannir des membres", "create_instant_invite": "Créer des invitations",
    "change_nickname": "Changer de pseudo", "manage_nicknames": "Gérer les pseudos",
    "manage_expressions": "Gérer les emojis/stickers", "manage_webhooks": "Gérer les webhooks",
    "view_channel": "Voir le salon", "send_messages": "Envoyer des messages",
    "send_tts_messages": "Envoyer des messages TTS", "manage_messages": "Gérer les messages",
    "embed_links": "Intégrer des liens", "attach_files": "Joindre des fichiers",
    "read_message_history": "Voir l'historique", "mention_everyone": "Mentionner @everyone",
    "external_emojis": "Emojis externes", "external_stickers": "Stickers externes",
    "add_reactions": "Ajouter des réactions", "connect": "Se connecter (vocal)",
    "speak": "Parler", "stream": "Vidéo/stream", "mute_members": "Rendre muet",
    "deafen_members": "Mettre en sourdine", "move_members": "Déplacer des membres",
    "use_voice_activation": "Détection de voix", "priority_speaker": "Orateur prioritaire",
    "request_to_speak": "Demander la parole", "manage_events": "Gérer les événements",
    "manage_threads": "Gérer les fils", "create_public_threads": "Créer des fils publics",
    "create_private_threads": "Créer des fils privés", "send_messages_in_threads": "Écrire dans les fils",
    "use_application_commands": "Commandes d'application", "moderate_members": "Exclure temporairement (timeout)",
    "view_guild_insights": "Voir les statistiques", "use_embedded_activities": "Activités",
    "use_soundboard": "Soundboard", "use_external_sounds": "Sons externes",
    "send_voice_messages": "Messages vocaux", "create_expressions": "Créer des emojis",
    "create_events": "Créer des événements", "send_polls": "Sondages",
    "use_external_apps": "Applications externes", "view_creator_monetization_analytics": "Analytique monétisation",
    # alias que discord.py rend lors de l'itération d'un PermissionOverwrite / Permissions
    "read_messages": "Voir le salon", "manage_permissions": "Gérer les permissions",
    "manage_emojis": "Gérer les emojis/stickers", "manage_emojis_and_stickers": "Gérer les emojis/stickers",
    "use_voice_activation": "Détection de voix",
}
# Permissions dont l'octroi est un signal de sécurité fort (→ #alertes).
DANGEROUS_PERMS = frozenset({
    "administrator", "manage_guild", "manage_roles", "manage_permissions", "manage_channels",
    "manage_webhooks", "kick_members", "ban_members", "mention_everyone", "manage_messages",
    "moderate_members", "view_audit_log", "manage_nicknames",
})

_NONE = discord.AllowedMentions.none()


# ============================================================ fonctions pures (testées)
def _safe(s, n=200):
    """Texte venu d'un utilisateur : pas de backtick, pas de saut de ligne, borné."""
    s = str(s if s is not None else "").replace("`", "'").replace("\r", "")
    s = " ".join(s.split("\n")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def clip(text, n=CLIP):
    """Contenu de message tronqué à `n` caractères (sauts de ligne conservés)."""
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def perm_fr(name):
    return PERM_FR.get(name, name)


def who(obj):
    """Libellé stable d'un utilisateur/membre/rôle/salon sans mention cliquable."""
    if obj is None:
        return "indisponible"
    if isinstance(obj, discord.Role) or type(obj).__name__.endswith("Role"):
        return f"@{_safe(getattr(obj, 'name', '?'), 60)}"
    name = getattr(obj, "name", None)
    if name is None:
        return f"id {getattr(obj, 'id', '?')}"
    if getattr(obj, "discriminator", "0") not in (None, "0", "0000"):
        name = f"{name}#{obj.discriminator}"
    oid = getattr(obj, "id", None)
    return f"{_safe(name, 60)} ({oid})" if oid else _safe(name, 60)


def chan(obj):
    if obj is None:
        return "salon indisponible"
    return f"#{_safe(getattr(obj, 'name', '?'), 60)}"


def account_age_s(created_at, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=dt.timezone.utc)
    return max(0, int((now - created_at).total_seconds()))


def is_young(created_at, now=None, days=YOUNG_ACCOUNT_DAYS):
    age = account_age_s(created_at, now)
    return age is not None and age < days * 86400


def attachments_text(atts):
    """« nom (taille) » par pièce jointe, ou None."""
    rows = []
    for a in atts or []:
        rows.append(f"{_safe(getattr(a, 'filename', '?'), 80)} ({fmt.humanize_bytes(getattr(a, 'size', 0))})")
    return "\n".join(rows)[:1024] if rows else None


def overwrite_map(overwrites):
    """{id cible: (libellé, {perm: True|False})} — seules les valeurs EXPLICITES comptent."""
    out = {}
    for target, ow in (overwrites or {}).items():
        vals = {}
        for name, val in ow:
            if val is not None:
                vals[name] = val
        out[getattr(target, "id", id(target))] = (who(target), vals)
    return out


def diff_overwrites(before, after):
    """Lignes « <cible> : ✅ perm, ⛔ perm, ↩️ perm (hérité) » entre deux dicts d'overwrites.
    Renvoie (lignes, dangereux) où `dangereux` = True si une permission de DANGEROUS_PERMS
    a été autorisée quelque part (signal #alertes)."""
    b, a = overwrite_map(before), overwrite_map(after)
    lines, danger = [], False
    for tid in sorted(set(b) | set(a), key=str):
        label = (a.get(tid) or b.get(tid))[0]
        bv, av = (b.get(tid) or (None, {}))[1], (a.get(tid) or (None, {}))[1]
        if bv == av:
            continue
        parts = []
        for perm in sorted(set(bv) | set(av)):
            old, new = bv.get(perm), av.get(perm)
            if old == new:
                continue
            if new is True:
                parts.append(f"✅ {perm_fr(perm)}")
                if perm in DANGEROUS_PERMS:
                    danger = True
            elif new is False:
                parts.append(f"⛔ {perm_fr(perm)}")
            else:
                parts.append(f"↩️ {perm_fr(perm)} (hérité)")
        if tid not in b:
            lines.append(f"➕ **{label}** (nouvel overwrite) : " + ", ".join(parts))
        elif tid not in a:
            lines.append(f"➖ **{label}** : overwrite supprimé (" + ", ".join(f"{perm_fr(p)}" for p in sorted(bv)) + " → hérité)")
        else:
            lines.append(f"**{label}** : " + ", ".join(parts))
    return lines, danger


def diff_role_perms(before, after):
    """(ajoutées, retirées) — noms API des permissions qui diffèrent entre deux Permissions."""
    b = {n for n, v in before if v}
    a = {n for n, v in after if v}
    return sorted(a - b), sorted(b - a)


def diff_channel(before, after):
    """Lignes de diff nom/sujet/NSFW/slowmode/catégorie (+ overwrites) d'un salon.
    Renvoie (lignes, dangereux)."""
    lines = []
    if getattr(before, "name", None) != getattr(after, "name", None):
        lines.append(f"nom : `{_safe(before.name, 60)}` → `{_safe(after.name, 60)}`")
    bt, at_ = getattr(before, "topic", None), getattr(after, "topic", None)
    if bt != at_:
        lines.append(f"sujet : « {_safe(bt or '—', 120)} » → « {_safe(at_ or '—', 120)} »")
    if getattr(before, "nsfw", None) != getattr(after, "nsfw", None):
        lines.append(f"NSFW : {getattr(before, 'nsfw', None)} → {getattr(after, 'nsfw', None)}")
    bs, as_ = getattr(before, "slowmode_delay", None), getattr(after, "slowmode_delay", None)
    if bs != as_:
        lines.append(f"slowmode : {bs or 0} s → {as_ or 0} s")
    bc, ac = getattr(before, "category", None), getattr(after, "category", None)
    if getattr(bc, "id", None) != getattr(ac, "id", None):
        lines.append(f"catégorie : {_safe(getattr(bc, 'name', None) or 'aucune', 60)} → "
                     f"{_safe(getattr(ac, 'name', None) or 'aucune', 60)}")
    ow, danger = diff_overwrites(getattr(before, "overwrites", {}) or {},
                                 getattr(after, "overwrites", {}) or {})
    if ow:
        lines.append("**Permissions (overwrites) :**")
        lines.extend("• " + ln for ln in ow)
    return lines, danger


def diff_role(before, after):
    """Lignes de diff nom/couleur/hoist/mentionable/permissions d'un rôle.
    Renvoie (lignes, dangereux) ; dangereux si une permission de DANGEROUS_PERMS est ajoutée."""
    lines, danger = [], False
    if before.name != after.name:
        lines.append(f"nom : `{_safe(before.name, 60)}` → `{_safe(after.name, 60)}`")
    bcol, acol = getattr(before, "color", None), getattr(after, "color", None)
    if str(bcol) != str(acol):
        lines.append(f"couleur : {bcol} → {acol}")
    if getattr(before, "hoist", None) != getattr(after, "hoist", None):
        lines.append(f"affiché séparément : {before.hoist} → {after.hoist}")
    if getattr(before, "mentionable", None) != getattr(after, "mentionable", None):
        lines.append(f"mentionnable : {before.mentionable} → {after.mentionable}")
    added, removed = diff_role_perms(before.permissions, after.permissions)
    if added:
        lines.append("permissions **ajoutées** : " + ", ".join(perm_fr(p) for p in added))
        danger = any(p in DANGEROUS_PERMS for p in added)
    if removed:
        lines.append("permissions **retirées** : " + ", ".join(perm_fr(p) for p in removed))
    return lines, danger


def pick_audit_entry(entries, target_id=None, now=None, window=AUDIT_WINDOW_S):
    """Première entrée d'audit datant de < `window` s et visant `target_id` (si donné).
    Renvoie l'entrée ou None. Sans réseau : `entries` est déjà lue."""
    now = now or dt.datetime.now(dt.timezone.utc)
    for e in entries:
        created = getattr(e, "created_at", None)
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.timezone.utc)
        if (now - created).total_seconds() > window:
            continue
        if target_id is not None and getattr(getattr(e, "target", None), "id", None) != target_id:
            continue
        return e
    return None


def take_batch(queue, n=BATCH):
    """Retire jusqu'à `n` éléments en tête de la file (FIFO)."""
    out = []
    while queue and len(out) < n:
        out.append(queue.popleft())
    return out


def bump_counts(counts, kind, day, keep_days=COUNTS_KEEP_DAYS):
    """Incrémente counts[day][kind] et purge les jours plus vieux que `keep_days`.
    `day` = « AAAA-MM-JJ ». Renvoie le dict (modifié en place)."""
    counts = counts if isinstance(counts, dict) else {}
    d = counts.setdefault(day, {})
    d[kind] = int(d.get(kind, 0)) + 1
    try:
        limit = (dt.date.fromisoformat(day) - dt.timedelta(days=keep_days)).isoformat()
    except ValueError:
        return counts
    for k in [k for k in counts if k < limit]:
        counts.pop(k, None)
    return counts


def make_embed(title, description=None, *, color=fmt.BLURPLE, fields=(), footer=None):
    """Embed borné (titre 256, description 4096, champ 1024)."""
    emb = discord.Embed(title=str(title)[:256], color=color)
    if description:
        emb.description = str(description)[:4096]
    for name, value in fields:
        if value:
            emb.add_field(name=str(name)[:256], value=str(value)[:1024], inline=False)
    if footer:
        emb.set_footer(text=str(footer)[:2048])
    emb.timestamp = dt.datetime.now(dt.timezone.utc)
    return emb


def content_or_reason(content, has_intent):
    """Contenu à afficher, ou la raison de son absence (jamais un vide muet)."""
    if content:
        return clip(content)
    if not has_intent:
        return "indisponible (intent Contenu des messages désactivé)"
    return "(vide — embed, sticker ou pièce jointe seule)"


def status_embed(*, channel, intents, perms, counts, queue_len, started, last_event,
                 welcome_role, alert_channel, notes=()):
    """Embed épinglé d'état du journal. Tout ce qui est DÉGRADÉ est dit explicitement."""
    lines = [f"Salon : {chan(channel) if channel else '❌ aucun (pas de catégorie Lock)'}",
             f"Démarré : <t:{int(started)}:R> · dernier événement : "
             + (f"<t:{int(last_event)}:R>" if last_event else "aucun depuis le démarrage"),
             f"File d'envoi : {queue_len} embed(s) en attente (lots de {BATCH}, 1 envoi / {FLUSH_SECONDS} s)"]
    intent_rows = []
    intent_rows.append("✅ intent Membres actif : arrivées, départs, rôles, pseudos, timeouts journalisés"
                       if intents.get("members") else
                       "⚠️ **intent Membres désactivé** : arrivées/départs/rôles/pseudos/timeouts NON journalisés "
                       "(bans/débans et bots ajoutés via audit restent visibles)")
    intent_rows.append("✅ intent Contenu des messages actif : avant/après des éditions visibles"
                       if intents.get("message_content") else
                       "⚠️ intent Contenu des messages désactivé : contenu écrit « indisponible », "
                       "l'événement (auteur, salon, lien, pièces jointes) est journalisé quand même")
    perm_rows = [("✅" if perms.get("view_audit_log") else "⚠️") + " Voir les journaux d'audit — "
                 + ("exécutants et raisons résolus" if perms.get("view_audit_log") else "exécutants « indisponibles »"),
                 ("✅" if perms.get("manage_guild") else "⚠️") + " Gérer le serveur — "
                 + ("invitations reçues" if perms.get("manage_guild") else "invitations créées probablement invisibles"),
                 ("✅" if perms.get("manage_roles") else "⚠️") + " Gérer les rôles — "
                 + ("rôle de bienvenue applicable" if perms.get("manage_roles") else "rôle de bienvenue refusé par Discord")]
    cfg_rows = [f"Rôle de bienvenue : {who(welcome_role) if welcome_role else 'désactivé (WELCOME_ROLE_ID=0)'}",
                f"Relais #alertes : {chan(alert_channel) if alert_channel else 'aucun (ALERT_CHANNEL_ID vide)'}"]
    total = sum(counts.values())
    top = ", ".join(f"{k} ×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:12]) or "aucun"
    emb = make_embed("📓 Journal du serveur Discord — état", "\n".join(lines), color=fmt.BLURPLE if channel else fmt.RED,
                     fields=[("Intents", "\n".join(intent_rows)), ("Permissions du bot", "\n".join(perm_rows)),
                             ("Configuration", "\n".join(cfg_rows)),
                             (f"Événements depuis le démarrage ({total})", top),
                             ("Notes", "\n".join(notes) if notes else None)],
                     footer="Compteurs par jour conservés 90 j dans state.json · #discord-logs lui-même n'est jamais journalisé")
    return emb


# ============================================================================ le cog
class DiscordLogs(commands.Cog):
    """#discord-logs : événements du serveur Discord, par lots, avec diff de permissions."""

    journal = app_commands.Group(name="journal-discord",
                                 description="Journal des événements du serveur Discord (#discord-logs).")

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self._queue = deque()
        self.counts = Counter()            # depuis le démarrage, par type
        self._started = time.time()
        self._last_event = None
        self._recent_bans = {}             # user id -> ts (évite « départ » + « ban »)
        self._alert_last = {}              # clé d'alerte -> ts (anti-doublon)
        self._warned_no_channel = False
        self._counts_dirty = False
        self.last_error = None

    async def cog_load(self):
        if not getattr(self.cfg, "discord_logs_enabled", True):
            log.warning("discord_logs: DISCORD_LOGS_ENABLED=false, cog inactif")
            return
        guard_cog_loops(self, log)
        self.flush.start()
        self.status_refresh.start()

    async def cog_unload(self):
        self.flush.cancel()
        self.status_refresh.cancel()

    # ------------------------------------------------------------ helpers d'accès
    @property
    def enabled(self):
        return bool(getattr(self.cfg, "discord_logs_enabled", True))

    def _guild(self):
        gid = getattr(self.cfg, "guild_id", None)
        return self.bot.get_guild(gid) if gid else None

    def _my_guild(self, guild):
        """True si `guild` est LE serveur configuré (ou si aucun n'est configuré)."""
        gid = getattr(self.cfg, "guild_id", None)
        return guild is not None and (not gid or guild.id == gid)

    def _info(self):
        return dict(self.bot.state.get(STATE_KEY) or {})

    def log_channel_id(self):
        return self._info().get("channel") or getattr(self.cfg, "discord_logs_channel_id", 0) or 0

    def is_log_channel(self, channel_or_id):
        cid = getattr(channel_or_id, "id", channel_or_id)
        parent = getattr(channel_or_id, "parent", None)
        lid = self.log_channel_id()
        return bool(lid) and (cid == lid or getattr(parent, "id", None) == lid)

    async def _log_channel(self):
        """#discord-logs dans « 🔒 Lock R820 » — jamais hors catégorie (règle 2026-08-11)."""
        guild = self._guild()
        if guild is None:
            return None
        info = self._info()
        ch = guild.get_channel(info["channel"]) if info.get("channel") else None
        if ch is None and getattr(self.cfg, "discord_logs_channel_id", 0):
            ch = guild.get_channel(self.cfg.discord_logs_channel_id)
        cat = channels.lock_category(self.bot, guild)
        if ch is None:
            ch = await channels.ensure_channel(
                self.bot, guild, "discord-logs", cat,
                topic="📓 Journal du serveur Discord : messages supprimés/édités, arrivées/départs, "
                      "rôles, permissions, invitations, webhooks, vocal. Lecture seule, par lots.",
                reason="journal des événements Discord (demande Nico 2026-08-30)")
            if ch is None:
                if not self._warned_no_channel:
                    log.warning("discord_logs: #discord-logs non créé (pas de catégorie Lock) — réessai plus tard")
                    self._warned_no_channel = True
                return None
        await channels.seal_if_public(self.bot, ch, cat, why="contenu de messages supprimés et codes d'invitation")
        if info.get("channel") != ch.id:
            info["channel"] = ch.id
            self.bot.state.set(STATE_KEY, info)
        return ch

    async def _alert_channel(self):
        cid = getattr(self.cfg, "alert_channel_id", 0) or getattr(self.cfg, "alerts_channel_id", 0)
        if not cid:
            return None
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("discord_logs: #alertes (%s) injoignable: %s", cid, e)
                return None
        return ch

    def _me_perms(self, guild):
        me = getattr(guild, "me", None)
        p = getattr(me, "guild_permissions", None)
        return {"view_audit_log": bool(getattr(p, "view_audit_log", False)),
                "manage_guild": bool(getattr(p, "manage_guild", False)),
                "manage_roles": bool(getattr(p, "manage_roles", False))}

    # ------------------------------------------------------------ file + compteurs
    def enqueue(self, kind, embed):
        """Ajoute un embed à la file (envoyé par lots) et compte l'événement."""
        self.counts[kind] += 1
        self._last_event = time.time()
        day = dt.date.today().isoformat()
        counts = bump_counts(dict(self.bot.state.get(COUNTS_KEY) or {}), kind, day)
        self.bot.state.set(COUNTS_KEY, counts)
        self._queue.append(embed)

    @tasks.loop(seconds=FLUSH_SECONDS)
    async def flush(self):
        if not self._queue:
            return
        ch = await self._log_channel()
        if ch is None:
            # pas de salon : on borne la file pour ne pas grossir sans fin
            while len(self._queue) > 200:
                self._queue.popleft()
            return
        batch = take_batch(self._queue)
        try:
            await ch.send(embeds=batch, allowed_mentions=_NONE)
            self.last_error = None
        except discord.Forbidden as e:
            self.last_error = f"écriture refusée dans #discord-logs ({e})"
            log.warning("discord_logs: %s", self.last_error)
        except discord.HTTPException as e:
            self.last_error = f"envoi impossible ({e})"
            log.warning("discord_logs: %s — lot remis en file", self.last_error)
            self._queue.extendleft(reversed(batch))

    @flush.before_loop
    async def _before_flush(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5)
    async def status_refresh(self):
        await self.publish_status()

    @status_refresh.before_loop
    async def _before_status(self):
        await self.bot.wait_until_ready()

    async def publish_status(self):
        guild = self._guild()
        ch = await self._log_channel()
        if guild is None or ch is None:
            return
        wr = getattr(self.cfg, "welcome_role_id", 0)
        notes = []
        if self.last_error:
            notes.append(f"⚠️ dernier échec d'envoi : {_safe(self.last_error, 150)}")
        if not getattr(self.cfg, "discord_logs_voice", True):
            notes.append("événements vocaux désactivés (DISCORD_LOGS_VOICE=0)")
        emb = status_embed(channel=ch, intents={"members": bool(self.bot.intents.members),
                                                "message_content": bool(self.bot.intents.message_content)},
                           perms=self._me_perms(guild), counts=self.counts, queue_len=len(self._queue),
                           started=self._started, last_event=self._last_event,
                           welcome_role=guild.get_role(wr) if wr else None,
                           alert_channel=self.bot.get_channel(getattr(self.cfg, "alert_channel_id", 0) or 0),
                           notes=notes)
        info = self._info()
        _msg, mid = await pin_edit(ch, emb, message_id=info.get("message"), label="#discord-logs", log=log)
        if mid and mid != info.get("message"):
            info["message"] = mid
            self.bot.state.set(STATE_KEY, info)

    # ------------------------------------------------------------ audit log
    async def executor(self, guild, action, target_id=None):
        """(user, raison, note) via le journal d'audit dans la fenêtre de 10 s.
        `note` explique une absence (« exécutant indisponible … ») ; jamais inventé."""
        if guild is None:
            return None, None, "exécutant indisponible (serveur inconnu)"
        if not self._me_perms(guild)["view_audit_log"]:
            return None, None, "exécutant indisponible (le bot n'a pas « Voir les journaux d'audit »)"
        try:
            entries = [e async for e in guild.audit_logs(limit=6, action=action)]
        except discord.HTTPException as e:
            return None, None, f"exécutant indisponible (journal d'audit illisible : {_safe(e, 80)})"
        except Exception as e:  # noqa: BLE001 — jamais casser un listener pour l'audit
            log.debug("discord_logs: audit_logs %s: %s", action, e)
            return None, None, "exécutant indisponible (journal d'audit illisible)"
        entry = pick_audit_entry(entries, target_id)
        if entry is None:
            return None, None, "exécutant indisponible (aucune entrée d'audit dans les 10 s)"
        return entry.user, entry.reason, None

    @staticmethod
    def exec_line(user, reason, note, verb="par"):
        if user is None:
            return note or "exécutant indisponible"
        s = f"{verb} {who(user)}"
        if reason:
            s += f" — raison : {_safe(reason, 200)}"
        return s

    # ------------------------------------------------------------ #alertes
    async def security_alert(self, key, text):
        """Ligne courte dans #alertes, anti-doublon par clé (cooldown) + Snooze honoré."""
        cooldown = int(getattr(self.cfg, "discord_logs_alert_cooldown", 300) or 0)
        now = time.time()
        if now - self._alert_last.get(key, 0) < cooldown:
            return False
        if alert_snoozed(self.bot.state, f"discord_{key}"):
            return False
        ch = await self._alert_channel()
        if ch is None:
            return False
        emb = discord.Embed(title="🛡️ Discord — signal de sécurité", description=str(text)[:1500],
                            color=fmt.ORANGE)
        emb.set_footer(text=f"alerte: discord_{key} [warn] · détail dans #discord-logs")
        try:
            await ch.send(embed=emb, allowed_mentions=_NONE)
        except discord.HTTPException as e:
            log.warning("discord_logs: relais #alertes impossible: %s", e)
            return False
        self._alert_last[key] = now
        return True

    # ============================================================ messages
    def _msg_ok(self, guild, channel_id, author=None):
        if not self.enabled or not self._my_guild(guild):
            return False
        if self.is_log_channel(channel_id) or self.is_log_channel(self.bot.get_channel(channel_id)):
            return False
        return not (author is not None and getattr(author, "bot", False))

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        msg = payload.cached_message
        if not self._msg_ok(guild, payload.channel_id, getattr(msg, "author", None)):
            return
        channel = self.bot.get_channel(payload.channel_id)
        fields = []
        if msg is not None:
            desc = f"Auteur : {who(msg.author)} · Salon : {chan(channel)}"
            fields.append(("Contenu", content_or_reason(msg.content, self.bot.intents.message_content)))
            fields.append(("Pièces jointes", attachments_text(msg.attachments)))
            user, reason, note = await self.executor(guild, discord.AuditLogAction.message_delete, msg.author.id)
            fields.append(("Supprimé", self.exec_line(user, reason, note or "par l'auteur ou exécutant indisponible")))
        else:
            desc = (f"Salon : {chan(channel)} · message `{payload.message_id}` **hors cache** : "
                    "auteur et contenu indisponibles")
        emb = make_embed("🗑️ Message supprimé", desc, color=fmt.RED, fields=fields,
                         footer=f"message {payload.message_id}")
        self.enqueue("message_delete", emb)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        data = payload.data or {}
        before = payload.cached_message
        after = getattr(payload, "message", None)
        author = getattr(after, "author", None) or getattr(before, "author", None)
        if author is None and (data.get("author") or {}).get("bot"):
            return
        if not self._msg_ok(guild, payload.channel_id, author):
            return
        if not data.get("edited_timestamp"):
            return          # déploiement d'embed (aperçu de lien), pas une édition humaine
        new = after.content if after is not None else data.get("content")
        old = before.content if before is not None else None
        if before is not None and old == new:
            return          # rien de changé dans le texte (épinglage, embed…)
        channel = self.bot.get_channel(payload.channel_id)
        jump = getattr(after, "jump_url", None) or (
            f"https://discord.com/channels/{payload.guild_id}/{payload.channel_id}/{payload.message_id}")
        has = self.bot.intents.message_content
        fields = [("Avant", content_or_reason(old, has) if before is not None else "indisponible (message hors cache)"),
                  ("Après", content_or_reason(new, has)),
                  ("Pièces jointes", attachments_text(getattr(after, "attachments", None)))]
        emb = make_embed("✏️ Message édité",
                         f"Auteur : {who(author) if author else 'indisponible'} · Salon : {chan(channel)} · [aller au message]({jump})",
                         color=fmt.BLURPLE, fields=fields, footer=f"message {payload.message_id}")
        self.enqueue("message_edit", emb)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if not self._msg_ok(guild, payload.channel_id):
            return
        channel = self.bot.get_channel(payload.channel_id)
        cached = list(payload.cached_messages or [])
        authors = Counter(who(m.author) for m in cached)
        user, reason, note = await self.executor(guild, discord.AuditLogAction.message_bulk_delete)
        desc = (f"**{len(payload.message_ids)}** messages supprimés d'un coup dans {chan(channel)} · "
                f"{len(cached)} en cache")
        fields = [("Auteurs (messages en cache)", "\n".join(f"{a} ×{n}" for a, n in authors.most_common(10))),
                  ("Exécutant", self.exec_line(user, reason, note))]
        self.enqueue("bulk_delete", make_embed("🧹 Suppression en masse", desc, color=fmt.RED, fields=fields))

    # ============================================================ membres
    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        if not self.enabled or not self._my_guild(guild):
            return
        age = account_age_s(member.created_at)
        young = is_young(member.created_at)
        age_txt = fmt.humanize_duration(age) if age is not None else "indisponible"
        if member.bot:
            user, reason, note = await self.executor(guild, discord.AuditLogAction.bot_add, member.id)
            desc = (f"🤖 **BOT ajouté** : {who(member)}\nAjouté {self.exec_line(user, reason, note)}\n"
                    f"Compte créé il y a {age_txt} · membres : {getattr(guild, 'member_count', '?')}")
            self.enqueue("bot_added", make_embed("⚠️ Bot / intégration ajouté au serveur", desc, color=fmt.ORANGE))
            await self.security_alert("bot_added", f"Bot **{who(member)}** ajouté au serveur, {self.exec_line(user, reason, note)}.")
            return
        lines = [f"{who(member)} · compte créé il y a **{age_txt}** (<t:{int(member.created_at.timestamp())}:D>)",
                 f"Membres : {getattr(guild, 'member_count', '?')}"]
        if young:
            lines.append(f"⚠️ **Compte récent** (< {YOUNG_ACCOUNT_DAYS} j) — vigilance (spam/alt possible, non prouvé).")
        lines.append(await self._welcome_role(member))
        self.enqueue("member_join", make_embed("📥 Arrivée", "\n".join(lines),
                                               color=fmt.ORANGE if young else fmt.GREEN))
        if young:
            await self.security_alert("young_account", f"Arrivée de **{who(member)}** avec un compte créé il y a {age_txt} (< {YOUNG_ACCOUNT_DAYS} j).")

    async def _welcome_role(self, member):
        rid = int(getattr(self.cfg, "welcome_role_id", 0) or 0)
        if not rid:
            return "Rôle de bienvenue : désactivé (WELCOME_ROLE_ID=0)."
        role = member.guild.get_role(rid)
        if role is None:
            return f"Rôle de bienvenue : rôle {rid} introuvable sur le serveur."
        try:
            await member.add_roles(role, reason="rôle de bienvenue (WELCOME_ROLE_ID)")
        except discord.Forbidden:
            return f"Rôle de bienvenue {who(role)} : **refusé par Discord** (le bot n'a pas « Gérer les rôles » ou le rôle est au-dessus du sien)."
        except discord.HTTPException as e:
            return f"Rôle de bienvenue {who(role)} : échec ({_safe(e, 80)})."
        self.bot.audit.record(user="bot", action="welcome_role", target=f"{member} ({member.id})", result=f"role={role.name}")
        return f"Rôle de bienvenue {who(role)} : ✅ ajouté."

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        guild = member.guild
        if not self.enabled or not self._my_guild(guild):
            return
        if time.time() - self._recent_bans.get(member.id, 0) < 60:
            return       # déjà journalisé comme ban
        user, reason, note = await self.executor(guild, discord.AuditLogAction.kick, member.id)
        roles = [who(r) for r in getattr(member, "roles", []) if getattr(r, "id", None) != guild.id]  # @everyone = id du guild
        joined = getattr(member, "joined_at", None)
        stay = f"présent depuis {fmt.humanize_duration(account_age_s(joined))}" if joined else "durée de présence indisponible"
        if user is not None:
            desc = f"{who(member)} **expulsé** {self.exec_line(user, reason, None)} · {stay}"
            kind, title, color = "member_kick", "👢 Expulsion", fmt.RED
        else:
            desc = f"{who(member)} a quitté le serveur · {stay}\n(départ volontaire ou {note})"
            kind, title, color = "member_leave", "📤 Départ", fmt.GREY
        self.enqueue(kind, make_embed(title, desc, color=color,
                                      fields=[("Rôles", ", ".join(roles) if roles else None)]))

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        guild = after.guild
        if not self.enabled or not self._my_guild(guild):
            return
        b_roles = {r.id: r for r in getattr(before, "roles", [])}
        a_roles = {r.id: r for r in getattr(after, "roles", [])}
        added = [a_roles[i] for i in a_roles if i not in b_roles]
        removed = [b_roles[i] for i in b_roles if i not in a_roles]
        lines = []
        danger = False
        if added or removed:
            user, reason, note = await self.executor(guild, discord.AuditLogAction.member_role_update, after.id)
            if added:
                lines.append("rôles **ajoutés** : " + ", ".join(who(r) for r in added))
                danger = any(any(v for n, v in getattr(r, "permissions", discord.Permissions.none()) if n in DANGEROUS_PERMS)
                             for r in added)
            if removed:
                lines.append("rôles **retirés** : " + ", ".join(who(r) for r in removed))
            lines.append(self.exec_line(user, reason, note))
        if getattr(before, "nick", None) != getattr(after, "nick", None):
            lines.append(f"pseudo : `{_safe(before.nick or '—', 60)}` → `{_safe(after.nick or '—', 60)}`")
        bt, at_ = getattr(before, "timed_out_until", None), getattr(after, "timed_out_until", None)
        if bt != at_:
            if at_ is not None:
                user, reason, note = await self.executor(guild, discord.AuditLogAction.member_update, after.id)
                lines.append(f"⏳ **timeout** jusqu'à <t:{int(at_.timestamp())}:f> — {self.exec_line(user, reason, note)}")
            else:
                lines.append("⏳ timeout **retiré**")
        if not lines:
            return
        self.enqueue("member_update", make_embed(f"👤 Membre modifié — {who(after)}", "\n".join(lines),
                                                 color=fmt.ORANGE if danger else fmt.BLURPLE))
        if danger:
            await self.security_alert("role_grant", f"**{who(after)}** a reçu un rôle portant des permissions sensibles : "
                                      + ", ".join(who(r) for r in added) + ".")

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        if not self.enabled or not self._my_guild(guild):
            return
        self._recent_bans[user.id] = time.time()
        ex, reason, note = await self.executor(guild, discord.AuditLogAction.ban, user.id)
        self.enqueue("member_ban", make_embed("🔨 Bannissement", f"{who(user)} banni {self.exec_line(ex, reason, note)}",
                                              color=fmt.RED))

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        if not self.enabled or not self._my_guild(guild):
            return
        ex, reason, note = await self.executor(guild, discord.AuditLogAction.unban, user.id)
        self.enqueue("member_unban", make_embed("🔓 Débannissement", f"{who(user)} débanni {self.exec_line(ex, reason, note)}",
                                                color=fmt.GREEN))

    # ============================================================ salons
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        if not self.enabled or not self._my_guild(channel.guild):
            return
        user, reason, note = await self.executor(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        kind = type(channel).__name__.replace("Channel", "").lower() or "salon"
        cat = getattr(getattr(channel, "category", None), "name", None)
        ow, danger = diff_overwrites({}, getattr(channel, "overwrites", {}) or {})
        self.enqueue("channel_create", make_embed(
            f"➕ Salon créé — {chan(channel)}",
            f"type {kind} · catégorie {_safe(cat or 'aucune', 60)} · {self.exec_line(user, reason, note)}",
            color=fmt.GREEN, fields=[("Overwrites", "\n".join("• " + ln for ln in ow))]))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if not self.enabled or not self._my_guild(channel.guild):
            return
        user, reason, note = await self.executor(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        cat = getattr(getattr(channel, "category", None), "name", None)
        self.enqueue("channel_delete", make_embed(
            f"➖ Salon supprimé — {chan(channel)}",
            f"catégorie {_safe(cat or 'aucune', 60)} · {self.exec_line(user, reason, note)}", color=fmt.RED))

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        if not self.enabled or not self._my_guild(after.guild):
            return
        lines, danger = diff_channel(before, after)
        if not lines:
            return
        action = discord.AuditLogAction.channel_update
        if any(ln.startswith("**Permissions") for ln in lines):
            action = discord.AuditLogAction.overwrite_update
        user, reason, note = await self.executor(after.guild, action, after.id)
        if user is None and action is not discord.AuditLogAction.channel_update:
            for alt in (discord.AuditLogAction.overwrite_create, discord.AuditLogAction.overwrite_delete,
                        discord.AuditLogAction.channel_update):
                user, reason, note = await self.executor(after.guild, alt, after.id)
                if user is not None:
                    break
        lines.append(self.exec_line(user, reason, note))
        self.enqueue("channel_update", make_embed(f"🔧 Salon modifié — {chan(after)}", "\n".join(lines),
                                                 color=fmt.ORANGE if danger else fmt.BLURPLE))
        if danger:
            await self.security_alert("overwrite", f"Permissions sensibles accordées sur {chan(after)} {self.exec_line(user, reason, note)}.")

    # ============================================================ rôles
    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        if not self.enabled or not self._my_guild(role.guild):
            return
        user, reason, note = await self.executor(role.guild, discord.AuditLogAction.role_create, role.id)
        perms = [perm_fr(n) for n, v in role.permissions if v]
        self.enqueue("role_create", make_embed(f"➕ Rôle créé — {who(role)}", self.exec_line(user, reason, note),
                                               color=fmt.GREEN, fields=[("Permissions", ", ".join(perms))]))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        if not self.enabled or not self._my_guild(role.guild):
            return
        user, reason, note = await self.executor(role.guild, discord.AuditLogAction.role_delete, role.id)
        self.enqueue("role_delete", make_embed(f"➖ Rôle supprimé — {who(role)}", self.exec_line(user, reason, note),
                                               color=fmt.RED))

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        if not self.enabled or not self._my_guild(after.guild):
            return
        lines, danger = diff_role(before, after)
        if not lines:
            return
        user, reason, note = await self.executor(after.guild, discord.AuditLogAction.role_update, after.id)
        lines.append(self.exec_line(user, reason, note))
        self.enqueue("role_update", make_embed(f"🔧 Rôle modifié — {who(after)}", "\n".join(lines),
                                              color=fmt.ORANGE if danger else fmt.BLURPLE))
        if danger:
            await self.security_alert("role_perms", f"Le rôle **{who(after)}** a reçu des permissions sensibles {self.exec_line(user, reason, note)}.")

    # ============================================================ invitations / webhooks
    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        guild = getattr(invite, "guild", None)
        if not self.enabled or not self._my_guild(guild):
            return
        max_age = getattr(invite, "max_age", None)
        exp = "jamais" if not max_age else f"dans {fmt.humanize_duration(max_age)}"
        uses = getattr(invite, "max_uses", None) or "illimité"
        desc = (f"code `{_safe(invite.code, 20)}` · créée par {who(getattr(invite, 'inviter', None))} · "
                f"salon {chan(getattr(invite, 'channel', None))} · utilisations max : {uses} · expire : {exp}"
                + (" · temporaire" if getattr(invite, "temporary", False) else ""))
        self.enqueue("invite_create", make_embed("🔗 Invitation créée", desc, color=fmt.ORANGE))
        await self.security_alert("invite", f"Invitation `{_safe(invite.code, 20)}` créée par {who(getattr(invite, 'inviter', None))} "
                                  f"(max {uses}, expire {exp}).")

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        guild = getattr(invite, "guild", None)
        if not self.enabled or not self._my_guild(guild):
            return
        user, reason, note = await self.executor(guild, discord.AuditLogAction.invite_delete)
        self.enqueue("invite_delete", make_embed(
            "🔗 Invitation supprimée / expirée",
            f"code `{_safe(invite.code, 20)}` · salon {chan(getattr(invite, 'channel', None))} · {self.exec_line(user, reason, note)}",
            color=fmt.GREY))

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        guild = channel.guild
        if not self.enabled or not self._my_guild(guild):
            return
        user, reason, note, what = None, None, None, "modifiés"
        for act, lbl in ((discord.AuditLogAction.webhook_create, "créé"), (discord.AuditLogAction.webhook_delete, "supprimé"),
                         (discord.AuditLogAction.webhook_update, "modifié")):
            user, reason, note = await self.executor(guild, act)
            if user is not None:
                what = lbl
                break
        count = "indisponible"
        try:
            if getattr(getattr(guild, "me", None), "guild_permissions", None) and guild.me.guild_permissions.manage_webhooks:
                count = str(len(await channel.webhooks()))
        except Exception:  # noqa: BLE001
            count = "indisponible"
        desc = f"Webhook {what} dans {chan(channel)} · {self.exec_line(user, reason, note)} · webhooks sur ce salon : {count}"
        self.enqueue("webhooks", make_embed("🪝 Webhooks modifiés", desc, color=fmt.ORANGE))
        await self.security_alert("webhook", f"Webhook {what} dans {chan(channel)} {self.exec_line(user, reason, note)}.")

    # ============================================================ fils, emojis, serveur
    @commands.Cog.listener()
    async def on_thread_create(self, thread):
        if not self.enabled or not self._my_guild(thread.guild) or self.is_log_channel(thread):
            return
        self.enqueue("thread", make_embed("🧵 Fil créé", f"{chan(thread)} dans {chan(getattr(thread, 'parent', None))} · "
                                          f"par {who(getattr(thread, 'owner', None) or discord.Object(id=getattr(thread, 'owner_id', 0)))}",
                                          color=fmt.GREEN))

    @commands.Cog.listener()
    async def on_thread_delete(self, thread):
        if not self.enabled or not self._my_guild(thread.guild) or self.is_log_channel(thread):
            return
        user, reason, note = await self.executor(thread.guild, discord.AuditLogAction.thread_delete, thread.id)
        self.enqueue("thread", make_embed("🧵 Fil supprimé", f"{chan(thread)} dans {chan(getattr(thread, 'parent', None))} · "
                                          f"{self.exec_line(user, reason, note)}", color=fmt.RED))

    @commands.Cog.listener()
    async def on_thread_update(self, before, after):
        if not self.enabled or not self._my_guild(after.guild) or self.is_log_channel(after):
            return
        lines = []
        if before.archived != after.archived:
            lines.append("archivé" if after.archived else "désarchivé")
        if getattr(before, "locked", None) != getattr(after, "locked", None):
            lines.append("verrouillé" if after.locked else "déverrouillé")
        if before.name != after.name:
            lines.append(f"renommé `{_safe(before.name, 60)}` → `{_safe(after.name, 60)}`")
        if not lines:
            return
        self.enqueue("thread", make_embed("🧵 Fil modifié", f"{chan(after)} : " + ", ".join(lines), color=fmt.BLURPLE))

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        if not self.enabled or not self._my_guild(guild):
            return
        b, a = {e.id: e for e in before}, {e.id: e for e in after}
        lines = [f"➕ :{_safe(a[i].name, 32)}:" for i in a if i not in b]
        lines += [f"➖ :{_safe(b[i].name, 32)}:" for i in b if i not in a]
        lines += [f"✏️ :{_safe(b[i].name, 32)}: → :{_safe(a[i].name, 32)}:" for i in a if i in b and b[i].name != a[i].name]
        if not lines:
            return
        self.enqueue("emojis", make_embed("😀 Emojis modifiés", "\n".join(lines)[:4000], color=fmt.BLURPLE))

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild, before, after):
        if not self.enabled or not self._my_guild(guild):
            return
        b, a = {s.id: s for s in before}, {s.id: s for s in after}
        lines = [f"➕ {_safe(a[i].name, 32)}" for i in a if i not in b]
        lines += [f"➖ {_safe(b[i].name, 32)}" for i in b if i not in a]
        if not lines:
            return
        self.enqueue("stickers", make_embed("🏷️ Stickers modifiés", "\n".join(lines)[:4000], color=fmt.BLURPLE))

    @commands.Cog.listener()
    async def on_guild_update(self, before, after):
        if not self.enabled or not self._my_guild(after):
            return
        lines = []
        if before.name != after.name:
            lines.append(f"nom : `{_safe(before.name, 60)}` → `{_safe(after.name, 60)}`")
        if getattr(before, "icon", None) != getattr(after, "icon", None):
            lines.append("icône modifiée")
        if getattr(before, "verification_level", None) != getattr(after, "verification_level", None):
            lines.append(f"niveau de vérification : {before.verification_level} → {after.verification_level}")
        if getattr(before, "description", None) != getattr(after, "description", None):
            lines.append(f"description : « {_safe(before.description or '—', 120)} » → « {_safe(after.description or '—', 120)} »")
        if getattr(before, "owner_id", None) != getattr(after, "owner_id", None):
            lines.append(f"⚠️ **propriétaire** : {before.owner_id} → {after.owner_id}")
        if not lines:
            return
        user, reason, note = await self.executor(after, discord.AuditLogAction.guild_update)
        lines.append(self.exec_line(user, reason, note))
        self.enqueue("guild_update", make_embed("🏠 Serveur modifié", "\n".join(lines), color=fmt.ORANGE))

    # ============================================================ vocal
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if not self.enabled or not self._my_guild(member.guild) or not getattr(self.cfg, "discord_logs_voice", True):
            return
        bc, ac = getattr(before, "channel", None), getattr(after, "channel", None)
        if bc is None and ac is not None:
            txt = f"🔊 {who(member)} a rejoint {chan(ac)}"
        elif bc is not None and ac is None:
            txt = f"🔇 {who(member)} a quitté {chan(bc)}"
        elif bc is not None and ac is not None and bc.id != ac.id:
            txt = f"🔀 {who(member)} : {chan(bc)} → {chan(ac)}"
        else:
            return      # mute/deaf/stream : bruit, ignoré volontairement
        emb = discord.Embed(description=txt, color=fmt.GREY)
        emb.timestamp = dt.datetime.now(dt.timezone.utc)
        self.enqueue("voice", emb)

    # ============================================================ commandes
    @journal.command(name="statut", description="État du journal Discord : salon, intents, compteurs, file.")
    @read_check()
    async def statut(self, itx: discord.Interaction):
        guild = itx.guild
        ch = self.bot.get_channel(self.log_channel_id()) if self.log_channel_id() else None
        wr = getattr(self.cfg, "welcome_role_id", 0)
        emb = status_embed(channel=ch, intents={"members": bool(self.bot.intents.members),
                                                "message_content": bool(self.bot.intents.message_content)},
                           perms=self._me_perms(guild), counts=self.counts, queue_len=len(self._queue),
                           started=self._started, last_event=self._last_event,
                           welcome_role=guild.get_role(wr) if (guild and wr) else None,
                           alert_channel=self.bot.get_channel(getattr(self.cfg, "alert_channel_id", 0) or 0),
                           notes=([f"⚠️ dernier échec d'envoi : {_safe(self.last_error, 150)}"] if self.last_error else [])
                           + ([] if self.enabled else ["cog désactivé (DISCORD_LOGS_ENABLED=0)"]))
        counts = self.bot.state.get(COUNTS_KEY) or {}
        if counts:
            days = sorted(counts)[-7:]
            emb.add_field(name="7 derniers jours (state.json)",
                          value="\n".join(f"`{d}` {sum(counts[d].values())} événement(s)" for d in days)[:1024],
                          inline=False)
        await itx.response.send_message(embed=emb, ephemeral=True)

    @journal.command(name="test", description="Poste un embed de test dans #discord-logs (propriétaire uniquement).")
    @read_check()
    async def test(self, itx: discord.Interaction):
        if not is_breakglass(self.cfg, itx):
            await itx.response.send_message("🔒 Réservé au propriétaire du serveur (ou ADMIN_IDS).", ephemeral=True)
            return
        await itx.response.defer(ephemeral=True)
        ch = await self._log_channel()
        if ch is None:
            await itx.followup.send("❌ Aucun salon #discord-logs (catégorie 🔒 Lock introuvable) — rien posté.", ephemeral=True)
            return
        emb = make_embed("🧪 Test du journal Discord", f"Demandé par {who(itx.user)} · la file compte {len(self._queue)} embed(s).",
                         color=fmt.GREEN, footer="ce message n'est pas un événement réel")
        self.enqueue("test", emb)
        self.bot.audit.record(user=f"{itx.user}({itx.user.id})", action="discord_logs_test", target=ch.name, result="ok")
        await itx.followup.send(f"✅ Embed de test mis en file pour {chan(ch)} (envoi sous {FLUSH_SECONDS} s).", ephemeral=True)


async def setup(bot):
    await bot.add_cog(DiscordLogs(bot))
