"""Modération LÉGÈRE : /purge, /lock, /unlock, /slowmode, /note.

Idée reprise des commandes purge/lock/slowmode/note de la suite JS Ultra (2026-08-30).
Serveur PRIVÉ (famille/amis) : PAS de ban, kick, timeout, massban ni warn — le
propriétaire le fait à la main si un jour c'est nécessaire. On garde uniquement ce qui
sert au quotidien : nettoyer un salon, le geler le temps d'un incident, ralentir un
flot, et se souvenir d'une décision (notes privées).

CE QUE FAIT CE COG
  - `/purge nombre [membre] [contient]` : supprime jusqu'à `nombre` (1..100) messages
    RÉCENTS du salon courant. Discord refuse la suppression en masse des messages de
    plus de 14 jours : ils sont comptés à part et IGNORÉS (jamais supprimés un par un —
    on ne fait pas ce que l'API refuse de faire en lot). Le compte annoncé est le compte
    RÉELLEMENT supprimé ;
  - `/lock [salon] [raison]` / `/unlock [salon]` : pose `send_messages=False` (+ réactions
    et fils) pour @everyone via l'overwrite du salon, en MÉMORISANT l'overwrite précédent
    dans `bot.state["locks"]` pour le restaurer À L'IDENTIQUE au déverrouillage (remettre
    « None » aveuglément casserait un salon qui était déjà en lecture seule, ou
    rouvrirait un salon fermé volontairement) ; un message est posté dans le salon ;
  - `/slowmode secondes [salon]` : 0..21600 s (plafond de l'API Discord) ;
  - `/note ajouter|liste|supprimer` : notes de modération PRIVÉES (éphémères) sur un
    membre, `bot.state["notes"]`.

PORTE : tier M/O du serveur du SALON (`admin_check(require_admin_channel=False,
scope="channel")`) : un M R820 modère les salons du R820, un M AVY-NAS ceux d'AVY-NAS,
jamais l'inverse (cloisonnement 2026-08-29) ; le salon CIBLE de /lock, /unlock et
/slowmode doit appartenir au même serveur que le salon d'où part la commande. Pas de
confinement à la catégorie Lock : on modère là où ça se passe.

CE QUE CE COG NE FAIT PAS
  - il n'agit JAMAIS sans la permission Discord requise : `channel.permissions_for(guild.me)`
    est vérifié AVANT chaque action et le refus nomme la permission manquante ;
  - sans l'intent `message_content`, le bot NE VOIT PAS le texte des messages des autres :
    le filtre `contient:` est alors refusé explicitement (« indisponible ») plutôt que
    de supprimer 0 message en silence ;
  - il ne verrouille pas un salon contre les M/O eux-mêmes (l'overwrite ne vise que
    @everyone : les rôles M/O gardent leurs permissions propres).

PIÈGES
  - `/lock` deux fois de suite ne ré-écrase PAS la mémoire (sinon `/unlock` restaurerait
    « verrouillé ») ;
  - la capacité srvperms « moderation » ne s'active que si Nico l'ajoute au catalogue
    CAPS : d'ici là `cap=None` (M passe, G non) — une capacité inconnue est FAIL-CLOSED ;
  - `NOTE_MAX_LEN` (défaut 500) lue via `getattr(cfg, "note_max_len", 500)`.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from ..core import channels as chn
from ..core import format as fmt
from ..core import srvperms
from ..core.permissions import admin_check

log = logging.getLogger("discord-bot.moderation")

LOCKS_KEY = "locks"
NOTES_KEY = "notes"
BULK_MAX_AGE = timedelta(days=14)
SLOWMODE_MAX = 21600
DEFAULT_NOTE_MAX = 500
# Champs de l'overwrite @everyone touchés par /lock (mémorisés puis restaurés à l'identique).
LOCK_FIELDS = ("send_messages", "add_reactions", "create_public_threads",
               "create_private_threads", "send_messages_in_threads")
PERM_FR = {"manage_messages": "Gérer les messages", "read_message_history": "Voir les anciens messages",
           "manage_roles": "Gérer les permissions", "manage_channels": "Gérer les salons",
           "send_messages": "Envoyer des messages", "view_channel": "Voir le salon"}
_CAP = "moderation" if "moderation" in srvperms.CAPS else None
_NONE = discord.AllowedMentions.none()
_mod = admin_check(require_admin_channel=False, scope="channel", cap=_CAP)


# ============================================================ fonctions pures (testées)
def missing_perms(perms, *needed):
    """Noms FR des permissions Discord manquantes au bot parmi `needed` (liste vide = OK)."""
    return [PERM_FR.get(n, n) for n in needed if not getattr(perms, n, False)]


def purge_split(messages, member_id=None, contains=None, now=None):
    """Applique les filtres puis sépare (supprimables en lot, trop vieux).
    Un message est « trop vieux » s'il a plus de 14 jours (limite de l'API bulk)."""
    now = now or datetime.now(timezone.utc)
    limit = now - BULK_MAX_AGE
    needle = (contains or "").lower() or None
    ok, old = [], []
    for m in messages:
        if member_id is not None and getattr(getattr(m, "author", None), "id", None) != member_id:
            continue
        if needle is not None and needle not in (getattr(m, "content", "") or "").lower():
            continue
        (ok if m.created_at > limit else old).append(m)
    return ok, old


def overwrite_snapshot(ow):
    """Mémoire de l'overwrite @everyone AVANT verrouillage : {champ: True|False|None}."""
    return {f: getattr(ow, f, None) for f in LOCK_FIELDS}


def apply_lock(ow):
    """Overwrite verrouillé (copie modifiée)."""
    ow.update(**{f: False for f in LOCK_FIELDS})
    return ow


def restore_overwrite(ow, snap):
    """Remet les champs verrouillés EXACTEMENT comme mémorisés (True / False / None)."""
    ow.update(**{f: (snap or {}).get(f) for f in LOCK_FIELDS})
    return ow


def slowmode_label(seconds):
    return "désactivé" if seconds <= 0 else fmt.humanize_duration(seconds)


def note_line(n):
    return (f"**#{n['id']}** <t:{int(n.get('ts') or 0)}:d> par {n.get('author') or n.get('author_id')} — "
            f"{n.get('text', '')}")


# ============================================================ le cog
class Moderation(commands.Cog):
    """Modération légère, tier M/O du serveur du salon, vérifications de permissions Discord."""

    note = app_commands.Group(name="note", description="Notes de modération privées sur un membre.")

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.note_max = int(getattr(self.cfg, "note_max_len", DEFAULT_NOTE_MAX) or DEFAULT_NOTE_MAX)

    # ------------------------------------------------------------------ outils
    def _audit(self, itx, action, target, result):
        audit = getattr(self.bot, "audit", None)
        if audit is not None:
            audit.record(user=f"{itx.user}({itx.user.id})", action=action, target=str(target),
                         result=str(result)[:200])

    async def _refuse(self, itx, text):
        if itx.response.is_done():
            await itx.followup.send(text, ephemeral=True, allowed_mentions=_NONE)
        else:
            await itx.response.send_message(text, ephemeral=True, allowed_mentions=_NONE)

    async def _bot_can(self, itx, channel, *needed):
        """True si le bot a les permissions ; sinon répond et renvoie False."""
        me = getattr(itx.guild, "me", None)
        perms = channel.permissions_for(me) if me is not None else discord.Permissions.none()
        miss = missing_perms(perms, *needed)
        if miss:
            await self._refuse(itx, "⛔ Le bot n'a pas la permission Discord requise sur "
                               f"{channel.mention} : **{', '.join(miss)}**. Rien n'a été fait.")
            return False
        return True

    async def _same_server(self, itx, channel):
        """Le salon cible doit appartenir au serveur du salon d'où part la commande."""
        src = chn.server_of_channel(self.bot, itx.channel)
        dst = chn.server_of_channel(self.bot, channel)
        if src != dst:
            await self._refuse(itx, f"⛔ {channel.mention} appartient au serveur **{dst}** ; cette commande "
                               f"part d'un salon de **{src}**. Les serveurs ne se mélangent pas.")
            return False
        return True

    # ------------------------------------------------------------------ /purge
    @app_commands.command(name="purge", description="Supprime des messages récents (< 14 j) du salon courant.")
    @app_commands.describe(nombre="Messages à examiner (1..100)", membre="Seulement ceux de ce membre",
                           contient="Seulement ceux contenant ce texte")
    @_mod
    async def purge(self, itx: discord.Interaction, nombre: app_commands.Range[int, 1, 100],
                    membre: discord.User = None, contient: str = None):
        ch = itx.channel
        if contient and not getattr(self.bot.intents, "message_content", False):
            await self._refuse(itx, "⛔ Filtre `contient` indisponible : le bot tourne sans l'intent "
                               "`message_content`, il ne voit pas le texte des messages des autres. "
                               "Relance sans ce filtre (ou par membre).")
            return
        if not await self._bot_can(itx, ch, "manage_messages", "read_message_history"):
            return
        await itx.response.defer(ephemeral=True)
        msgs = [m async for m in ch.history(limit=nombre)]
        ok, old = purge_split(msgs, getattr(membre, "id", None), contient)
        deleted = 0
        try:
            for i in range(0, len(ok), 100):
                chunk = ok[i:i + 100]
                if len(chunk) == 1:
                    await chunk[0].delete()
                else:
                    await ch.delete_messages(chunk, reason=f"/purge par {itx.user}")
                deleted += len(chunk)
        except discord.HTTPException as e:
            log.warning("purge partielle dans #%s : %s", getattr(ch, "name", ch), e)
        txt = f"🗑️ **{deleted}** message(s) supprimé(s) sur {len(msgs)} examiné(s)."
        if membre is not None:
            txt += f"\nFiltre membre : {membre} (id {membre.id})"
        if contient:
            txt += f"\nFiltre texte : `{contient[:50]}`"
        if old:
            txt += f"\n⚠️ {len(old)} ignoré(s) : plus de 14 jours, Discord refuse la suppression en lot."
        if deleted < len(ok):
            txt += f"\n⚠️ Arrêt sur erreur Discord après {deleted}/{len(ok)} (voir les logs)."
        self._audit(itx, "purge", f"#{getattr(ch, 'name', ch.id)}",
                    f"{deleted} supprimés, {len(old)} trop vieux"
                    + (f", membre={membre.id}" if membre is not None else ""))
        await itx.followup.send(txt, ephemeral=True, allowed_mentions=_NONE)

    # ------------------------------------------------------------------ /lock /unlock
    def _locks(self):
        d = self.bot.state.get(LOCKS_KEY, {}) or {}
        return dict(d) if isinstance(d, dict) else {}

    @app_commands.command(name="lock", description="Verrouille un salon (@everyone ne peut plus écrire).")
    @app_commands.describe(salon="Salon (défaut : celui-ci)", raison="Affichée dans le salon")
    @_mod
    async def lock(self, itx: discord.Interaction, salon: discord.TextChannel = None, raison: str = None):
        ch = salon or itx.channel
        if not await self._same_server(itx, ch):
            return
        if not await self._bot_can(itx, ch, "manage_roles"):
            return
        locks = self._locks()
        key = str(ch.id)
        everyone = itx.guild.default_role
        ow = ch.overwrites_for(everyone)
        if key not in locks:
            # première pose : on mémorise l'état d'AVANT (pas lors d'un re-lock)
            locks[key] = {"prev": overwrite_snapshot(ow), "by": itx.user.id, "ts": int(time.time()),
                          "reason": (raison or "")[:200]}
            self.bot.state.set(LOCKS_KEY, locks)
        await ch.set_permissions(everyone, overwrite=apply_lock(ow),
                                 reason=f"/lock par {itx.user}" + (f" : {raison}" if raison else ""))
        emb = discord.Embed(description=f"🔒 Salon verrouillé par {itx.user.mention}."
                            + (f"\n**Raison :** {raison[:500]}" if raison else ""), color=fmt.RED)
        try:
            await ch.send(embed=emb, allowed_mentions=_NONE)
        except discord.HTTPException:
            pass  # le verrou est posé même si l'annonce échoue (bot sans send_messages ici)
        self._audit(itx, "lock", f"#{getattr(ch, 'name', ch.id)}", raison or "ok")
        await self._refuse(itx, f"🔒 {ch.mention} verrouillé (`/unlock` restaure l'état précédent).")

    @app_commands.command(name="unlock", description="Déverrouille un salon (restaure l'overwrite mémorisé).")
    @app_commands.describe(salon="Salon (défaut : celui-ci)")
    @_mod
    async def unlock(self, itx: discord.Interaction, salon: discord.TextChannel = None):
        ch = salon or itx.channel
        if not await self._same_server(itx, ch):
            return
        if not await self._bot_can(itx, ch, "manage_roles"):
            return
        locks = self._locks()
        key = str(ch.id)
        mem = locks.pop(key, None)
        everyone = itx.guild.default_role
        ow = restore_overwrite(ch.overwrites_for(everyone), (mem or {}).get("prev"))
        # overwrite redevenu vide -> on le supprime plutôt que de laisser une entrée à 0/0
        await ch.set_permissions(everyone, overwrite=None if ow.is_empty() else ow,
                                 reason=f"/unlock par {itx.user}")
        if mem is not None:
            self.bot.state.set(LOCKS_KEY, locks)
        emb = discord.Embed(description=f"🔓 Salon déverrouillé par {itx.user.mention}.", color=fmt.GREEN)
        try:
            await ch.send(embed=emb, allowed_mentions=_NONE)
        except discord.HTTPException:
            pass
        self._audit(itx, "unlock", f"#{getattr(ch, 'name', ch.id)}",
                    "restauré" if mem else "aucun verrou mémorisé, champs remis à neutre")
        note = "" if mem else ("\n⚠️ Aucun verrou mémorisé pour ce salon : les champs d'écriture de "
                               "@everyone ont été remis à neutre (hérité de la catégorie).")
        await self._refuse(itx, f"🔓 {ch.mention} déverrouillé.{note}")

    # ------------------------------------------------------------------ /slowmode
    @app_commands.command(name="slowmode", description="Mode lent d'un salon (0 = désactivé, max 6 h).")
    @app_commands.describe(secondes="0..21600", salon="Salon (défaut : celui-ci)")
    @_mod
    async def slowmode(self, itx: discord.Interaction, secondes: app_commands.Range[int, 0, SLOWMODE_MAX],
                       salon: discord.TextChannel = None):
        ch = salon or itx.channel
        if not 0 <= secondes <= SLOWMODE_MAX:   # Range protège déjà ; garde-fou pour un appel direct
            await self._refuse(itx, f"⛔ Valeur hors bornes (0..{SLOWMODE_MAX}).")
            return
        if not await self._same_server(itx, ch):
            return
        if not await self._bot_can(itx, ch, "manage_channels"):
            return
        await ch.edit(slowmode_delay=secondes, reason=f"/slowmode par {itx.user}")
        self._audit(itx, "slowmode", f"#{getattr(ch, 'name', ch.id)}", f"{secondes}s")
        await self._refuse(itx, f"🐢 Mode lent de {ch.mention} : **{slowmode_label(secondes)}**.")

    # ------------------------------------------------------------------ /note
    def _notes(self):
        d = self.bot.state.get(NOTES_KEY, {}) or {}
        if not isinstance(d, dict):
            d = {}
        return {"seq": int(d.get("seq") or 0), "items": list(d.get("items") or [])}

    @note.command(name="ajouter", description="Ajoute une note privée sur un membre (M/O).")
    @app_commands.describe(membre="Membre concerné", texte="Contenu de la note")
    @_mod
    async def note_add(self, itx: discord.Interaction, membre: discord.User, texte: str):
        txt = (texte or "").strip()
        if not txt:
            await self._refuse(itx, "❌ Note vide.")
            return
        if len(txt) > self.note_max:
            await self._refuse(itx, f"❌ Note trop longue ({len(txt)} > {self.note_max}).")
            return
        d = self._notes()
        d["seq"] += 1
        d["items"].append({"id": d["seq"], "member_id": membre.id, "member": str(membre),
                           "author_id": itx.user.id, "author": str(itx.user),
                           "text": txt, "ts": int(time.time())})
        self.bot.state.set(NOTES_KEY, d)
        self._audit(itx, "note_add", membre.id, f"#{d['seq']}")
        await self._refuse(itx, f"📝 Note **#{d['seq']}** ajoutée sur {membre} (id {membre.id}).")

    @note.command(name="liste", description="Notes privées d'un membre (M/O).")
    @app_commands.describe(membre="Membre")
    @_mod
    async def note_list(self, itx: discord.Interaction, membre: discord.User):
        items = [n for n in self._notes()["items"] if n.get("member_id") == membre.id]
        if not items:
            await self._refuse(itx, f"ℹ️ Aucune note sur {membre} (id {membre.id}).")
            return
        lines = [note_line(n) for n in sorted(items, key=lambda n: -n["id"])[:20]]
        emb = discord.Embed(title=f"📝 Notes — {membre} ({len(items)})", description="\n".join(lines)[:4000],
                            color=fmt.ORANGE)
        await itx.response.send_message(embed=emb, ephemeral=True, allowed_mentions=_NONE)

    @note.command(name="supprimer", description="Supprime une note par son numéro (M/O).")
    @app_commands.describe(id="Numéro de la note (#)")
    @_mod
    async def note_delete(self, itx: discord.Interaction, id: app_commands.Range[int, 1]):
        d = self._notes()
        keep = [n for n in d["items"] if n.get("id") != id]
        if len(keep) == len(d["items"]):
            await self._refuse(itx, f"❌ Note #{id} introuvable.")
            return
        d["items"] = keep
        self.bot.state.set(NOTES_KEY, d)
        self._audit(itx, "note_delete", id, "ok")
        await self._refuse(itx, f"🗑️ Note #{id} supprimée.")


async def setup(bot):
    await bot.add_cog(Moderation(bot))
