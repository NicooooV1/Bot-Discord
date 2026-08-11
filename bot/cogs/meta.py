"""Meta commands + salon #help auto-actualisé.

- /help : index des commandes (éphémère, même contenu que le salon).
- /whoami : tes rôles & permissions.
- Salon #help (catégorie « Lock ») : message épinglé listant TOUTES les commandes,
  leur usage (signature des arguments) et à quoi elles servent — auto-généré depuis
  l'arbre de commandes du bot, rafraîchi par une boucle + un bouton « Rafraîchir ».
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.gates import GatedView
from ..core.permissions import read_check, is_admin, can_read

log = logging.getLogger("discord-bot.meta")

LOCK_CATEGORY = "Lock"      # même catégorie que #ratio
HELP_CHANNEL = "help"

# command name -> category; anything unlisted lands in "Autres"
# ⚠️ Table à tenir à jour quand une commande est ajoutée : une commande absente n'est pas
# perdue (elle tombe dans « 🧩 Autres »), mais le classement perd son sens. Le cadenas 🔒,
# lui, n'est PLUS déduit d'une liste (cf. _is_admin_cmd) — il ne peut donc pas périmer.
CATEGORIES = [
    ("📊 État & métriques", ["status", "health", "ping", "node", "ct", "cts", "graph"]),
    ("💽 Stockage & matériel", ["storage", "thinpool", "raid", "smart", "temps", "ipmi"]),
    ("🛟 Sauvegardes", ["backups"]),
    ("📜 Logs & journaux", ["journal", "logs", "tail", "tasks", "logstream",
                             "logsearch", "ctlogs", "apperrors"]),
    ("🔎 In-guest", ["sys", "df"]),
    ("🚨 Alertes & dashboard", ["alerts", "dashboard"]),
    ("🧲 Seedbox & médias", ["ratio", "setratio", "langues", "film", "serie"]),
    ("🐳 Docker & torrents", ["docker", "torrents"]),
    ("📥 Téléchargements YouTube/Twitch", ["yt", "tw", "musique", "yt-config", "dl"]),
    ("🤖 Assistant IA", ["assistant"]),
    ("🔐 Sécurité & accès", ["2fa", "gestion"]),
    ("🔧 Actions (admin)", ["ctctl", "backup", "audit"]),
    ("🪪 Divers", ["whoami", "help"]),
]
# Repli pour les commandes gardées par du code INLINE et non par @admin_check :
# /gestion vérifie lui-même le tier O dans son corps, il n'a donc aucun check à inspecter.
ADMIN_CMDS = {"gestion"}


def _is_admin_cmd(cmd):
    """🔒 déduit du CODE, pas d'une liste (2026-08-11).

    `ADMIN_CMDS` listait 4 commandes pour 8 réellement gardées : /docker, /torrents,
    /setratio, /logstream… s'affichaient SANS cadenas alors que la légende promet
    l'inverse. `admin_check()` renvoie un prédicat local — repérable à son `__qualname__`
    — attaché à `Command.checks` : la liste ne peut plus dériver du code."""
    for chk in getattr(cmd, "checks", None) or ():
        if "admin_check" in getattr(chk, "__qualname__", ""):
            return True
    return cmd.qualified_name.split(" ")[0] in ADMIN_CMDS


class Meta(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.helpchan.start()

    async def cog_load(self):
        # vue persistante : le bouton Rafraîchir survit aux redémarrages du bot
        self.bot.add_view(HelpRefreshView(self))

    def cog_unload(self):
        self.helpchan.cancel()

    # ------------------------------------------------------------------ contenu
    def _grouped_commands(self):
        """Retourne [(label_catégorie, [ligne, ...]), ...] depuis l'arbre de commandes."""
        cmds = {}  # qualified_name -> (signature, description, admin?)
        for c in self.bot.tree.walk_commands():
            if not isinstance(c, app_commands.Command):
                continue  # ignore les groupes (leurs sous-commandes sont listées)
            qn = c.qualified_name
            parts = []
            for p in c.parameters:
                nm = getattr(p, "display_name", None) or p.name
                parts.append(f"<{nm}>" if p.required else f"[{nm}]")
            cmds[qn] = (" ".join(parts), (c.description or "").strip(),
                        _is_admin_cmd(c))

        def line(qn):
            sig, desc, admin = cmds[qn]
            head = f"`/{qn}" + (f" {sig}" if sig else "") + "`" + (" 🔒" if admin else "")
            return f"{head} — {desc}" if desc else head

        sections, placed = [], set()
        for label, names in CATEGORIES:
            lines = []
            for qn in sorted(cmds):
                if qn.split(" ")[0] in names:
                    placed.add(qn)
                    lines.append(line(qn))
            if lines:
                sections.append((label, lines))
        extra = [line(qn) for qn in sorted(cmds) if qn not in placed]
        if extra:
            sections.append(("🧩 Autres", extra))
        return sections

    def _help_embed(self):
        """Un seul embed (cap Discord = 6000 car. cumulés sur tout le message)."""
        sections = self._grouped_commands()
        total = sum(len(lines) for _, lines in sections)
        emb = discord.Embed(
            title="📖 Aide — toutes les commandes du bot",
            # la légende disait « salon admin + rôle autorisé » : faux pour /docker,
            # /torrents, /setratio… qui sont `admin_check(require_admin_channel=False)`
            # et se lancent depuis n'importe quel salon autorisé (2026-08-11).
            description=("Ce que fait chaque commande et comment l'utiliser.\n"
                         "`<obligatoire>` · `[optionnel]` · 🔒 = réservé aux "
                         "gestionnaires (rôle Gestion ; certaines aussi confinées au "
                         "salon admin)."),
            color=fmt.BLURPLE)
        emb.timestamp = discord.utils.utcnow()
        used = len(emb.title) + len(emb.description)
        fields, truncated = 0, False
        for label, lines in sections:
            # découpe les lignes en valeurs de champ <= 1024 caractères
            chunks, cur = [], ""
            for ln in lines:
                piece = ("\n" if cur else "") + ln
                if len(cur) + len(piece) > 1024:
                    chunks.append(cur)
                    cur = ln
                else:
                    cur += piece
            if cur:
                chunks.append(cur)
            for i, chunk in enumerate(chunks):
                name = label if i == 0 else f"{label} (suite)"
                if fields >= 24 or used + len(name) + len(chunk) > 5800:
                    truncated = True
                    break
                emb.add_field(name=name, value=chunk, inline=False)
                fields += 1
                used += len(name) + len(chunk)
            if truncated:
                break
        foot = f"{total} commandes · 🔄 Rafraîchir pour régénérer · auto-généré depuis le bot"
        emb.set_footer(text=("⚠️ liste tronquée · " + foot) if truncated else foot)
        return emb

    # ------------------------------------------------------------------ salon
    @staticmethod
    def _satellite_overwrites(guild, parent=None):
        """Permissions posées À LA CRÉATION des salons satellites de « Lock ».

        Sans `overwrites=`, Discord fait hériter les permissions du serveur : au tout
        premier démarrage sur un guild vierge, la catégorie « Lock » et #help naissaient
        donc INSCRIPTIBLES par @everyone (2026-08-11). Personne d'autre que le bot n'y
        écrit désormais.

        ⚠️ `parent` (la catégorie d'accueil) n'est pas décoratif : dès qu'on passe
        `overwrites=` à `create_text_channel`, Discord CESSE de recopier les permissions
        de la catégorie. Or `lock_category_id` peut très bien désigner la vraie « 🔒 Lock
        <clé> » de provision (requests._lock_category l'y écrit), qui masque @everyone :
        un #help créé là avec nos seuls overwrites en ressortirait VISIBLE DE TOUS. On
        repart donc des overwrites de la catégorie et on n'ajoute que le refus d'écriture
        (relecture 2026-08-11). La visibilité de #help reste ainsi exactement celle de sa
        catégorie, comme avant la correction.

        ⚠️ À passer uniquement à `create_*`, JAMAIS à `edit(overwrites=)` : sur une
        catégorie pré-existante, qui appartient à l'utilisateur et peut contenir ses
        propres salons, `edit` REMPLACE l'ensemble des overwrites (cf.
        provision._ensure_lock_category)."""
        # `.overwrites` reconstruit des PermissionOverwrite neufs à chaque appel : les
        # modifier ne touche pas le cache de la catégorie.
        ow = dict(parent.overwrites) if parent is not None else {}
        everyone = ow.get(guild.default_role) or discord.PermissionOverwrite()
        everyone.update(send_messages=False, add_reactions=False)
        ow[guild.default_role] = everyone
        if guild.me is not None:      # sans le membre bot en cache, on ne s'auto-exclut pas
            me = ow.get(guild.me) or discord.PermissionOverwrite()
            me.update(view_channel=True, send_messages=True, embed_links=True,
                      manage_messages=True)
            ow[guild.me] = me
        return ow

    async def _ensure_help_channel(self):
        gid = getattr(self.bot.cfg, "guild_id", None)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            return None
        info = self.bot.state.get("help_msg") or {}
        ch = guild.get_channel(info["channel"]) if info.get("channel") else None
        if ch is not None:
            return ch
        cat = None
        cat_id = self.bot.state.get("lock_category_id")
        if cat_id:
            c = guild.get_channel(cat_id)
            if isinstance(c, discord.CategoryChannel):
                cat = c
        if cat is None:
            cat = discord.utils.get(guild.categories, name=LOCK_CATEGORY)
        if cat is None:
            try:
                cat = await guild.create_category(
                    LOCK_CATEGORY, overwrites=self._satellite_overwrites(guild),
                    reason="salon d'aide")
            except discord.HTTPException:
                log.warning("catégorie « %s » non créée", LOCK_CATEGORY, exc_info=True)
                cat = None
        if cat is not None:
            self.bot.state.set("lock_category_id", cat.id)
        ch = discord.utils.get(guild.text_channels, name=HELP_CHANNEL)
        if ch is None:
            try:
                ch = await guild.create_text_channel(
                    HELP_CHANNEL, category=cat,
                    overwrites=self._satellite_overwrites(guild, cat),
                    topic="Aide : toutes les commandes du bot — auto + bouton Rafraîchir")
                log.info("salon #help créé")
            except discord.HTTPException:
                log.warning("salon #%s non créé", HELP_CHANNEL, exc_info=True)
                return None
        cur = self.bot.state.get("help_msg") or {}
        cur["channel"] = ch.id
        self.bot.state.set("help_msg", cur)
        return ch

    async def _pin_edit(self, ch, embed):
        info = self.bot.state.get("help_msg") or {}
        mid = info.get("message")
        msg = None
        if mid:
            try:
                msg = await ch.fetch_message(mid)
            except discord.NotFound:
                msg = None
            except discord.HTTPException:
                return
        view = HelpRefreshView(self)
        try:
            if msg is None:
                msg = await ch.send(embed=embed, view=view)
                try:
                    await msg.pin()
                except discord.HTTPException:
                    log.warning("message d'aide non épinglé", exc_info=True)
                info["message"] = msg.id
                self.bot.state.set("help_msg", info)
            else:
                await msg.edit(embed=embed, view=view)
        except discord.HTTPException:
            # avalé jusqu'ici : un embed rejeté (>6000 car.) ou un salon devenu
            # inaccessible laissait #help figé sans une ligne de log (2026-08-11).
            log.warning("publication du message d'aide impossible", exc_info=True)

    @tasks.loop(minutes=30)
    async def helpchan(self):
        try:
            ch = await self._ensure_help_channel()
            if ch is not None:
                await self._pin_edit(ch, self._help_embed())
        except Exception:
            log.exception("help channel refresh failed")

    @helpchan.before_loop
    async def _before_help(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ commandes
    @app_commands.command(description="Liste des commandes du bot, leur usage et leur rôle.")
    @read_check()
    async def help(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        await itx.followup.send(embed=self._help_embed(), ephemeral=True)

    @app_commands.command(description="Affiche tes rôles et tes permissions sur le bot.")
    async def whoami(self, itx: discord.Interaction):
        if not itx.guild_id:
            await itx.response.send_message("À utiliser dans le serveur.", ephemeral=True)
            return
        cfg = self.bot.cfg
        admin = is_admin(cfg, itx)
        read = can_read(cfg, itx)
        roles = [r for r in getattr(itx.user, "roles", []) if not r.is_default()]
        emb = discord.Embed(title="🪪 Tes permissions",
                            color=fmt.GREEN if admin else fmt.BLURPLE)
        emb.add_field(name="Utilisateur",
                      value=f"{itx.user.mention} (`{itx.user.id}`)", inline=False)
        # un champ d'embed est limité à 1024 caractères : une trentaine de mentions de
        # rôles suffit à faire rejeter tout le message par l'API (2026-08-11)
        rtxt = ", ".join(r.mention for r in roles) or "—"
        if len(rtxt) > 1024:
            rtxt = rtxt[:1000].rsplit(",", 1)[0] + f", … (+{len(roles)} au total)"
        emb.add_field(name="Rôles", value=rtxt, inline=False)
        emb.add_field(name="Actions admin", value="✅ autorisé" if admin else "❌ refusé")
        emb.add_field(name="Lecture", value="✅ autorisé" if read else "❌ refusé")
        if cfg.admin_role_ids:
            emb.set_footer(text="Rôle(s) requis: "
                                + ", ".join(str(x) for x in cfg.admin_role_ids))
        await itx.response.send_message(embed=emb, ephemeral=True)


class HelpRefreshView(GatedView):
    """Bouton « Rafraîchir » du message épinglé de #help.

    Tier « read » : le bouton ne fait que régénérer l'embed déjà affiché (aucune action
    privilégiée), mais un bouton n'est jamais couvert par `GatedTree` — il lui faut donc
    une porte déclarée, et « lecture + session 2FA » est celle qui correspond au salon
    (2026-08-11)."""

    gate = "read"

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="meta:help:refresh")
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        await itx.response.defer()
        try:
            await itx.message.edit(embed=self.cog._help_embed(), view=self)
        except discord.HTTPException:
            log.warning("rafraîchissement de #help impossible", exc_info=True)


async def setup(bot):
    await bot.add_cog(Meta(bot))
