"""/faq — réponses enregistrées (FAQ interne du homelab : VPN, adresse Jellyfin, etc.).

Idée reprise de la commande « /tag » de la suite JS Ultra (2026-08-30), avec ses défauts
corrigés : le tag JS renvoyait le texte BRUT sans `allowedMentions` (un contenu contenant
« @everyone » pinguait tout le serveur), n'imposait ni longueur ni schéma d'URL, et le
compteur d'usages vivait en base SQL. Ici : Markdown autorisé, mentions NEUTRALISÉES sur
tous les envois (`AllowedMentions.none()`), liens `http(s)` uniquement, ≤ 1800 caractères,
stockage `bot.state["faq"]`.

CE QUE FAIT CE COG
  - `/faq voir nom [public]` : affiche une réponse. Autocomplétion sur les noms. PAS de
    porte de tier : une FAQ sert justement aux invités SANS rôle G/M/O (« comment me
    connecter au VPN ? »). Elle reste derrière le 2FA global (GatedTree barre TOUTES les
    commandes slash, celle-ci comprise) et le guild configuré. Par défaut la réponse est
    éphémère ; `public=True` la poste dans le salon, réservé aux M/O (sinon, n'importe
    quel invité pourrait faire parler le bot en public) ;
  - `/faq ajouter|modifier|supprimer|liste` : gestion, tier M/O du serveur de CETTE
    instance (`admin_check(scope="primary")`, depuis n'importe quel salon du R820) ; la
    liste montre le compteur d'usages et l'auteur ;
  - audit (`bot.audit.record`) sur ajout / modification / suppression.

CE QUE CE COG NE FAIT PAS
  - il ne stocke rien de secret : une FAQ est lisible par TOUT membre du guild (2FA mis à
    part) — pas de mot de passe, de clé WireGuard, de token dedans (règle rappelée dans
    le refus des schémas d'URL hors http/https, faute de pouvoir deviner le reste) ;
  - il ne remplace pas `/help` (catalogue des commandes) : ce sont des réponses écrites
    par un humain.

PIÈGES
  - la clé de config `FAQ_MAX_LEN` n'existe pas encore dans config.py : lue via
    `getattr(cfg, "faq_max_len", 1800)` (à déclarer par Nico) ;
  - un nom de FAQ est NORMALISÉ (`normalize_name`) : « VPN Pierre » et « vpn-pierre »
    désignent la même entrée — la liste et l'autocomplétion montrent la forme normalisée ;
  - la capacité srvperms « faq » n'est activée que si Nico l'ajoute au catalogue CAPS :
    tant qu'elle n'y est pas, `cap=None` (M passe, G non) — une capacité inconnue serait
    FAIL-CLOSED et refuserait les M.
"""
import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core import srvperms
from ..core.permissions import admin_check, channel_server, is_admin, log_refusal

log = logging.getLogger("discord-bot.faq")

STATE_KEY = "faq"
NAME_MAX = 32
DEFAULT_MAX_LEN = 1800
# Capacité proposée dans le rapport ; active automatiquement le jour où elle entre dans CAPS.
_CAP = "faq" if "faq" in srvperms.CAPS else None

_NONE = discord.AllowedMentions.none()
_SCHEME_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*)://")


# ============================================================ fonctions pures (testées)
def normalize_name(raw):
    """« VPN Pierre » -> « vpn-pierre ». Minuscules, [a-z0-9-] seulement, tirets
    dédoublés, ≤ NAME_MAX. Renvoie None si rien d'utilisable ne reste."""
    s = str(raw or "").strip().lower()
    s = re.sub(r"[\s_./]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        return None
    return s[:NAME_MAX].rstrip("-") or None


def check_content(raw, max_len=DEFAULT_MAX_LEN):
    """(contenu nettoyé, None) ou (None, motif de refus). Markdown autorisé ; liens
    http(s) seulement (un `javascript:`/`file://`/`ftp://` n'a rien à faire dans une FAQ
    d'invités) ; longueur bornée pour tenir dans un message Discord avec sa marge."""
    s = str(raw or "").replace("\r\n", "\n").strip()
    if not s:
        return None, "contenu vide"
    if len(s) > max_len:
        return None, f"contenu trop long ({len(s)} > {max_len} caractères)"
    bad = sorted({m.group(1).lower() for m in _SCHEME_RE.finditer(s)} - {"http", "https"})
    if bad:
        return None, "seuls les liens http(s) sont acceptés (trouvé : " + ", ".join(bad) + ")"
    return s, None


def entry_line(name, e):
    """Ligne de la liste : nom, usages, auteur, date de dernière modification."""
    when = e.get("updated") or e.get("created") or 0
    ts = f"<t:{int(when)}:d>" if when else "date indisponible"
    author = e.get("author") or (f"id {e['author_id']}" if e.get("author_id") else "auteur indisponible")
    return f"`{name}` — {int(e.get('uses') or 0)} usage(s) · {author} · {ts}"


# ============================================================ le cog
class Faq(commands.Cog):
    """FAQ interne : réponses enregistrées, éphémères par défaut, mentions neutralisées."""

    faq = app_commands.Group(name="faq", description="Réponses enregistrées (FAQ du homelab).")

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.max_len = int(getattr(self.cfg, "faq_max_len", DEFAULT_MAX_LEN) or DEFAULT_MAX_LEN)

    # ------------------------------------------------------------------ stockage
    def _all(self):
        d = self.bot.state.get(STATE_KEY, {}) or {}
        return dict(d) if isinstance(d, dict) else {}

    def _save(self, d):
        self.bot.state.set(STATE_KEY, d)

    def _audit(self, itx, action, name, result="ok"):
        audit = getattr(self.bot, "audit", None)
        if audit is not None:
            audit.record(user=f"{itx.user}({itx.user.id})", action=action, target=name, result=result)

    # ------------------------------------------------------------------ autocomplétion
    async def _ac_names(self, itx, current):
        cur = (current or "").lower()
        names = sorted(n for n in self._all() if cur in n)
        return [app_commands.Choice(name=n, value=n) for n in names[:25]]

    # ------------------------------------------------------------------ commandes
    @faq.command(name="voir", description="Affiche une réponse enregistrée (éphémère par défaut).")
    @app_commands.describe(nom="Nom de la FAQ", public="Poster dans le salon (réservé aux M/O)")
    @app_commands.autocomplete(nom=_ac_names)
    async def voir(self, itx: discord.Interaction, nom: str, public: bool = False):
        # Pas de read_check : les invités sans rôle sont le public visé. Le 2FA global
        # (GatedTree) et le guild configuré restent la seule porte.
        if self.cfg.guild_id and getattr(itx, "guild_id", None) != self.cfg.guild_id:
            await itx.response.send_message("⛔ Serveur non autorisé.", ephemeral=True)
            return
        name = normalize_name(nom)
        d = self._all()
        e = d.get(name) if name else None
        if e is None:
            await itx.response.send_message(
                f"❌ FAQ `{name or '?'}` introuvable (voir `/faq liste` si tu es gestionnaire).",
                ephemeral=True, allowed_mentions=_NONE)
            return
        if public and not is_admin(self.cfg, itx, server=channel_server(itx)):
            log_refusal(itx, "role", f"/faq voir public {name}")
            await itx.response.send_message(
                "🔒 Poster une FAQ en public est réservé aux gestionnaires (M/O) ; "
                "réponse envoyée en privé à la place.\n\n" + e["content"],
                ephemeral=True, allowed_mentions=_NONE)
            return
        e = dict(e, uses=int(e.get("uses") or 0) + 1)
        d[name] = e
        self._save(d)
        await itx.response.send_message(e["content"], ephemeral=not public, allowed_mentions=_NONE)

    @faq.command(name="ajouter", description="Enregistre une nouvelle réponse (M/O).")
    @app_commands.describe(nom="Nom (sera normalisé : minuscules, tirets)", contenu="Texte Markdown, ≤ 1800 caractères")
    @admin_check(require_admin_channel=False, scope="primary", cap=_CAP)
    async def ajouter(self, itx: discord.Interaction, nom: str, contenu: str):
        name = normalize_name(nom)
        if not name:
            await itx.response.send_message("❌ Nom invalide (lettres, chiffres, tirets).", ephemeral=True)
            return
        content, why = check_content(contenu, self.max_len)
        if why:
            await itx.response.send_message(f"❌ Refusé : {why}.", ephemeral=True, allowed_mentions=_NONE)
            return
        d = self._all()
        if name in d:
            await itx.response.send_message(
                f"❌ `{name}` existe déjà — utilise `/faq modifier`.", ephemeral=True)
            return
        now = int(time.time())
        d[name] = {"content": content, "author_id": itx.user.id, "author": str(itx.user),
                   "created": now, "updated": now, "uses": 0}
        self._save(d)
        self._audit(itx, "faq_add", name)
        await itx.response.send_message(f"✅ FAQ `{name}` enregistrée ({len(content)} caractères).",
                                        ephemeral=True, allowed_mentions=_NONE)

    @faq.command(name="modifier", description="Remplace le contenu d'une réponse (M/O).")
    @app_commands.describe(nom="Nom de la FAQ", contenu="Nouveau texte")
    @app_commands.autocomplete(nom=_ac_names)
    @admin_check(require_admin_channel=False, scope="primary", cap=_CAP)
    async def modifier(self, itx: discord.Interaction, nom: str, contenu: str):
        name = normalize_name(nom)
        d = self._all()
        if not name or name not in d:
            await itx.response.send_message(f"❌ FAQ `{name or '?'}` introuvable.", ephemeral=True)
            return
        content, why = check_content(contenu, self.max_len)
        if why:
            await itx.response.send_message(f"❌ Refusé : {why}.", ephemeral=True, allowed_mentions=_NONE)
            return
        e = dict(d[name], content=content, updated=int(time.time()),
                 editor_id=itx.user.id, editor=str(itx.user))
        d[name] = e
        self._save(d)
        self._audit(itx, "faq_edit", name)
        await itx.response.send_message(f"✅ FAQ `{name}` mise à jour.", ephemeral=True, allowed_mentions=_NONE)

    @faq.command(name="supprimer", description="Supprime une réponse (M/O).")
    @app_commands.describe(nom="Nom de la FAQ")
    @app_commands.autocomplete(nom=_ac_names)
    @admin_check(require_admin_channel=False, scope="primary", cap=_CAP)
    async def supprimer(self, itx: discord.Interaction, nom: str):
        name = normalize_name(nom)
        d = self._all()
        if not name or name not in d:
            await itx.response.send_message(f"❌ FAQ `{name or '?'}` introuvable.", ephemeral=True)
            return
        d.pop(name)
        self._save(d)
        self._audit(itx, "faq_delete", name)
        await itx.response.send_message(f"🗑️ FAQ `{name}` supprimée.", ephemeral=True, allowed_mentions=_NONE)

    @faq.command(name="liste", description="Liste des réponses avec usages et auteur (M/O).")
    @admin_check(require_admin_channel=False, scope="primary", cap=_CAP)
    async def liste(self, itx: discord.Interaction):
        d = self._all()
        if not d:
            await itx.response.send_message("📭 Aucune FAQ enregistrée (`/faq ajouter`).", ephemeral=True)
            return
        lines = [entry_line(n, d[n]) for n in sorted(d, key=lambda k: (-int(d[k].get("uses") or 0), k))]
        emb = discord.Embed(title=f"📚 FAQ — {len(d)} entrée(s)", description="\n".join(lines)[:4000],
                            color=fmt.BLURPLE)
        await itx.response.send_message(embed=emb, ephemeral=True, allowed_mentions=_NONE)


async def setup(bot):
    await bot.add_cog(Faq(bot))
