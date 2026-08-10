"""Auto-provisioning des salons Discord (demande utilisateur 2026-07-09).

Le bot possède la permission « Gérer les salons » : il CRÉE et maintient lui-même la
structure du serveur au lieu de dépendre d'IDs collés à la main dans config.env.

Deux volets :
  1) Catégorie « 📊 Supervision » + salons dédiés (alertes, rapports, journaux-live,
     materiel, sauvegardes, stockage). Les rendus périodiques (matériel/sauvegardes/
     stockage) sont épinglés et édités sur place (pas de spam).
  2) Un salon par guest (LXC/VM), réconcilié dynamiquement contre l'API Proxmox :
       - nouveau guest        -> nouveau salon créé automatiquement,
       - guest supprimé       -> salon DÉPLACÉ dans « 🗄️ Archive » (jamais supprimé),
       - ordre des salons      = ordre des VMID (l'ID donne la position).

Idempotent : les IDs résolus sont persistés dans bot.state["prov"]. Au redémarrage le
bot ré-adopte les salons existants (par ID persisté, puis par nom) au lieu d'en recréer.
Après réconciliation, le routage temps réel est recâblé vers les salons résolus :
  - cfg.alert_channel_id / report_channel_id  (lus en direct par Alerts/Reports),
  - cfg.ct_channels                           (lu en direct par le routeur de logs),
  - CtChannels.map + Logs.stream.channel_id   (capturés à l'init -> mis à jour ici).
"""
import asyncio
import logging

import discord
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.permissions import admin_button_ok

log = logging.getLogger("discord-bot.provision")

# Salons de supervision : clé interne -> (nom Discord, description/topic).
SUPER_CHANNELS = [
    ("alertes",       "alertes",       "Alertes proactives (RAID/SMART/IPMI/sauvegardes/thinpool)."),
    ("rapports",      "rapports",      "Rapport quotidien de santé de l'infrastructure."),
    ("journaux_live", "journaux-live", "Flux syslog temps réel (sévérité <= warning)."),
    ("materiel",      "materiel",      "Matériel : IPMI (conso/ventilos/températures), SMART, RAID."),
    ("sauvegardes",   "sauvegardes",   "État des sauvegardes PBS par guest."),
    ("stockage",      "stockage",      "Stockage : thinpool, storages Proxmox, remplissage."),
    ("seedbox",       "seedbox",       "Seedbox : ratio, upload, top torrents, VPN, port-forwarding."),
    ("nas",           "nas",           "NAS Synology : capacité, santé disques/SMART, RAID, température."),
    ("reseau",        "reseau",        "Réseau : routeur CRS305, WAN (latence/débit), services publics."),
    ("services",      "services",      "Santé des services (Postgres/Vaultwarden/mail/*arr…)."),
]

CAT_SUPERVISION = "📊 Supervision R820"
CAT_CONTAINERS = "📦 Conteneurs"      # utilisé seulement si aucune catégorie CT existante
CAT_ARCHIVE = "🗄️ Archive"
CAT_LOCK = "🔒 Lock R820"             # hyperviseur — visible Gestion+O R820 (+ propriétaire)

# Salons du NAS Synology, promu SERVEUR à part entière (clé SYNO) le 2026-08-07 :
# ses rôles G/M/O sont dans GESTION_SERVERS comme R820/AVY-*, et c'est leur seule
# présence là-bas qui active le bloc (pas de variable d'environnement de plus).
# Lecture SEULE : le bot supervise le NAS en SNMP mais n'agit jamais dessus.
SYNO_SUP_CHANNELS = [
    ("sante",   "sante",   "NAS : état général, température, ventilateur, alimentation, DSM, charge."),
    ("disques",  "disques",  "NAS : disques physiques — modèle, température, santé, secteurs défectueux."),
    ("volumes",  "volumes",  "NAS : volumes et groupes de stockage — capacité, remplissage, état."),
]


def _slug(name: str) -> str:
    """Nom de guest -> nom de salon Discord valide (minuscules, sans espace)."""
    s = "".join(c if (c.isalnum() or c in "-_") else "-" for c in name.strip().lower())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")[:90] or "guest"


def _pdelta(cur, prev, unit="", dec=0):
    """Delta d'évolution depuis le dernier refresh (flèche + valeur)."""
    if prev is None:
        return ""
    d = cur - prev
    if round(d, dec) == 0:
        return " (=)"
    return f" (▲ +{d:.{dec}f}{unit})" if d > 0 else f" (▼ {d:.{dec}f}{unit})"


class Provision(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.enabled = getattr(bot.cfg, "auto_provision", True)
        # --- multi-serveurs : les noms de catégories dérivent de SERVER_KEY (défaut R820).
        # Une instance secondaire (SERVER_KEY=AVEYRON, IS_PRIMARY=false) gère alors
        # « 📊 Supervision AVEYRON » / « Gestion AVEYRON » / « 🔒 Lock AVEYRON » et ne touche
        # PAS aux ressources partagées (#logs-2fa, #général) réservées au primaire.
        sk = getattr(bot.cfg, "server_key", "R820")
        self.CAT_SUPERVISION = f"📊 Supervision {sk}"
        self.CAT_LOCK = f"🔒 Lock {sk}"
        self.CAT_CONTAINERS = f"Gestion {sk}"
        # cluster Aveyron supervisé par CE bot : TROIS serveurs distincts (un par nœud
        # pve/nas/llm), chacun avec « 📊 Supervision AVY-X » + « Gestion AVY-X » et les
        # rôles G/M/O de SA clé GESTION_SERVERS (choix Nico 2026-07-17).
        # Salons de supervision par nœud (embeds tenus par le cog Avy) :
        # « hyperviseur » n'est PLUS dans cette liste : ce salon vit désormais dans sa
        # propre catégorie « 🔒 Lock <clé> » (propriétaire uniquement, choix Nico
        # 2026-07-18 — pendant du salon du nœud R820), cf. _ensure_avy_lock_hyperviseur.
        self.AVY_SUP_CHANNELS = [
            ("alertes",     "alertes",     "Alertes du serveur : nœud injoignable, stockage plein, VM/conteneur tombé, sauvegarde en échec."),
            ("stockage",    "stockage",    "Stockages du nœud : remplissage, état."),
            ("sauvegardes", "sauvegardes", "Sauvegardes vzdump du nœud vers nas-backup."),
            ("materiel",    "materiel",    "Disques physiques du nœud : modèle, santé SMART, usure, température."),
        ]
        # NAS Synology = serveur à part (demande Nico) : mêmes catégories et mêmes
        # 3 tiers que les autres, mais SANS catégorie « Gestion » — il n'a aucun
        # invité à piloter, la valeur est dans la supervision. La fiche sensible
        # (n° de série, SMART détaillé) va dans « 🔒 Lock SYNO », comme l'hyperviseur.
        self.syno_key = getattr(bot.cfg, "syno_key", "SYNO")
        self.CAT_SYNO_SUP = f"📊 Supervision {self.syno_key}"
        self.CAT_SYNO_LOCK = f"🔒 Lock {self.syno_key}"
        self.is_primary = getattr(bot.cfg, "is_primary", True)
        self._done_once = False
        self._lock = asyncio.Lock()
        # état persistant : { categories:{}, super:{key:id}, ct:{name:id},
        #                     archived:{name:id}, order:[names] }
        self.prov = dict(bot.state.get("prov", {}) or {})
        self.prov.setdefault("categories", {})
        self.prov.setdefault("super", {})
        self.prov.setdefault("ct", {})
        self.prov.setdefault("archived", {})
        self.prov.setdefault("order", [])

    def _save(self):
        self.bot.state.set("prov", self.prov)

    async def cog_load(self):
        # bouton Rafraîchir persistant pour les embeds épinglés thématiques
        self.bot.add_view(TopicalRefreshView(self))

    def cog_unload(self):
        self.reconcile.cancel()
        self.refresh_topical.cancel()

    # ------------------------------------------------------------------ helpers

    def _guild(self):
        return self.bot.get_guild(self.cfg.guild_id)

    async def _ensure_category(self, guild, name, key):
        """Adopte (par id persisté puis par nom) ou crée une catégorie. Renvoie l'objet."""
        cid = self.prov["categories"].get(key)
        cat = guild.get_channel(cid) if cid else None
        if isinstance(cat, discord.CategoryChannel):
            return cat
        cat = discord.utils.get(guild.categories, name=name)
        if cat is None:
            # fallback tolérant aux emojis/espaces : « Archive » adopte « 🗄️ Archive »
            # (et inversement) au lieu de créer un DOUBLON. Comparaison alphanumérique.
            norm = lambda s: "".join(c for c in s.lower() if c.isalnum())
            target = norm(name)
            cat = next((c for c in guild.categories if norm(c.name) == target), None)
        if cat is None:
            cat = await guild.create_category(name=name, reason="auto-provision bot")
            log.info("catégorie créée: %s", name)
        self.prov["categories"][key] = cat.id
        return cat

    # ------------------------------------------------- catégorie « 🔒 Lock » (nœud)

    def _lock_overwrites(self, guild):
        """Catégorie Lock : @everyone ne voit RIEN ; voient le propriétaire, le bot, et les
        porteurs du rôle « O <srv> » (qui, par construction de /gestion, portent AUSSI
        « Gestion <srv> » et ont le 2FA — cahier des charges 2026-07-16).

        ⚠️ Un membre portant la permission Discord « Administrateur » (et le propriétaire
        du serveur) IGNORE ces overwrites — Discord ne permet pas de les lui cacher. La
        vraie frontière des BOUTONS est donc runtime (permissions.lock_button_ok +
        terminal._may_open_node), pas cette permission de salon, qui reste le filtre de
        visibilité."""
        ow = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
        }
        me = guild.me
        if me is not None:
            ow[me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True,
                manage_messages=True, manage_channels=True, read_message_history=True,
                create_private_threads=True, send_messages_in_threads=True,
                manage_threads=True)
        # Catégorie Lock = tiers M (modération) ET O (owner) — pas G (visualiser).
        lock_ids = [rid for rid in (self.cfg.node_mod_role_id, self.cfg.node_owner_role_id) if rid]
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
            log.warning("rôles M/O introuvables — overwrites Lock NON modifiés")
            return None
        for oid in self.cfg.node_terminal_owner_ids:
            # get_member est vide sans l'intent GUILD_MEMBERS -> Object(id) suffit :
            # discord.py ne se sert que de .id et type l'overwrite en MEMBER.
            target = guild.get_member(oid) or discord.Object(id=oid)
            ow[target] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                create_private_threads=True, send_messages_in_threads=True)
        return ow

    def _lock_overwrites_fn(self, key):
        """Comme _lock_overwrites, mais pour la catégorie Lock d'UN serveur Aveyron
        précis (clé GESTION_SERVERS) : seuls M/O de CETTE clé voient la catégorie —
        pas le O du R820, pas le rôle G (visualiser seul) de qui que ce soit."""
        def want_fn(guild):
            srv = (getattr(self.cfg, "gestion_servers", {}) or {}).get(key, {})
            lock_ids = [rid for rid in (srv.get("mod"), srv.get("owner")) if rid]
            ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
            me = guild.me
            if me is not None:
                ow[me] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, embed_links=True,
                    manage_messages=True, manage_channels=True, read_message_history=True,
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
                log.warning("rôles M/O %s introuvables — overwrites Lock NON modifiés", key)
                return None
            for oid in self.cfg.node_terminal_owner_ids:
                target = guild.get_member(oid) or discord.Object(id=oid)
                ow[target] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    create_private_threads=True, send_messages_in_threads=True)
            return ow
        return want_fn

    def _managed_overwrites(self, guild):
        """Salons de Supervision + Gestion <srv>, 3 tiers :
          - G (view, READ_ROLE_IDS)  : VOIT, mais n'écrit pas et ne lance pas de commandes ;
          - M + O (ADMIN_ROLE_IDS)   : voient + écrivent + boutons (+ commandes) ;
          - @everyone                : rien.
        Les boutons restent visibles de G mais inertes (garde runtime is_admin)."""
        ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        me = guild.me
        if me is not None:
            ow[me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True,
                manage_messages=True, manage_channels=True, read_message_history=True,
                send_messages_in_threads=True)
        # garde anti-lockout : le rôle M/O (admin) DOIT être résolu, sinon on ne touche rien
        if not self.cfg.admin_role_ids:
            return None
        for rid in self.cfg.admin_role_ids:
            if guild.get_role(rid) is None:
                log.warning("rôle M/O %s introuvable — overwrites de gestion NON modifiés", rid)
                return None
        # VUE seule (tier G + éventuels rôles visionneurs manuels dans VIEWER_ROLE_IDS,
        # VIDE depuis le 2026-07-17 sur demande de Nico : le rôle « A » ne doit voir QUE
        # #général, plus aucun salon de gestion — seuls G/M/O par serveur donnent accès) :
        # voir, mais pas d'écriture, pas de commandes, pas de fils (sinon contournement
        # du « lecture seule »).
        view_only = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=False,
            add_reactions=False, use_application_commands=False,
            create_public_threads=False, create_private_threads=False,
            send_messages_in_threads=False)
        view_ids = [getattr(self.cfg, "node_view_role_id", 0)] + list(self.cfg.viewer_role_ids)
        for rid in view_ids:
            r = guild.get_role(rid) if rid else None
            if r is not None:
                ow[r] = view_only
        for rid in self.cfg.admin_role_ids:           # tiers M + O : voir + interagir
            r = guild.get_role(rid)
            if r is not None:
                ow[r] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    use_application_commands=True, add_reactions=True, embed_links=True,
                    send_messages_in_threads=True)
        return ow

    async def _ensure_general_perms(self, guild):
        """#général : ENTRÉE ouverte à TOUS les membres (il n'y a plus de rôle d'entrée
        dédié). @everyone voit, écrit et peut lancer /2fa ici (les commandes 2FA se font
        dans #général ; #logs-2fa n'est qu'un JOURNAL privé). set_permissions ne touche
        QUE l'overwrite @everyone."""
        cid = getattr(self.cfg, "general_channel_id", 0)
        if not cid:
            return
        ch = guild.get_channel(cid)
        if not isinstance(ch, discord.TextChannel):
            return
        r = guild.default_role
        want = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=True,
            use_application_commands=True)
        if ch.overwrites.get(r) != want:
            try:
                await ch.set_permissions(r, overwrite=want,
                                         reason="#général : entrée ouverte (voir + /2fa)")
                log.info("#général : ouvert à @everyone (entrée + /2fa)")
            except discord.HTTPException:
                log.warning("#général : overwrite @everyone non appliqué")

    def _twofa_overwrites(self, guild):
        """#logs-2fa = JOURNAL d'audit 2FA : VISIBLE PAR PERSONNE (choix Nico). @everyone
        deny view, aucun rôle ; seul le bot y écrit (et le propriétaire le voit par bypass
        owner). Les commandes /2fa, elles, se lancent dans #général."""
        ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        me = guild.me
        if me is not None:
            ow[me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True,
                read_message_history=True, manage_channels=True)
        return ow

    async def _ensure_twofa_channel(self, guild):
        """Crée/adopte le JOURNAL d'audit 2FA (#logs-2fa) : rangé dans « 🗄️ Archive »,
        VISIBLE PAR PERSONNE (@everyone deny, seul le bot écrit + owner par bypass), et
        publie son id dans le state pour que le cog 2fa y journalise les événements."""
        name = getattr(self.cfg, "twofa_channel_name", "") or ""
        if not name:
            return
        want = self._twofa_overwrites(guild)
        arch = await self._ensure_category(guild, CAT_ARCHIVE, "archive")
        cid = self.prov.get("twofa_channel")
        ch = guild.get_channel(cid) if cid else None
        if not isinstance(ch, discord.TextChannel):
            ch = discord.utils.get(guild.text_channels, name=name)
        if not isinstance(ch, discord.TextChannel):
            try:
                ch = await guild.create_text_channel(
                    name=name, overwrites=want, category=arch,
                    topic="Journal d'audit 2FA (inscriptions, déverrouillages, "
                          "désinscriptions). Privé — le bot seul y écrit.",
                    reason="auto-provision : journal 2FA")
                log.info("journal 2FA créé: #%s", ch.name)
            except discord.HTTPException:
                log.exception("création du journal 2FA impossible")
                return
        else:
            if arch is not None and ch.category_id != arch.id:
                try:
                    await ch.edit(category=arch, reason="journal 2FA rangé dans Archive")
                    await ch.edit(overwrites=want, reason="journal 2FA : permissions")
                    log.info("journal 2FA rangé dans Archive + permissions réimposées")
                except discord.HTTPException:
                    log.warning("journal 2FA : rangement dans Archive impossible")
            elif self._ovw_drifted(ch, want):
                try:
                    await ch.edit(overwrites=want, reason="journal 2FA : permissions")
                    log.info("journal 2FA : permissions (ré)appliquées")
                except discord.HTTPException:
                    log.warning("journal 2FA : permissions non appliquées")

        self.prov["twofa_channel"] = ch.id
        # publié pour le cog 2fa : c'est le salon où JOURNALISER (pas où confiner /2fa)
        if self.bot.state.get("twofa_log_channel_id") != ch.id:
            self.bot.state.set("twofa_log_channel_id", ch.id)

    @staticmethod
    def _ovw_drifted(ch, want):
        """True si les overwrites du salon diffèrent de l'ensemble voulu (comparaison par
        id : sans l'intent GUILD_MEMBERS les cibles sont des Object à l'identité instable)."""
        try:
            w = {t.id: p for t, p in want.items()}
            h = {t.id: p for t, p in ch.overwrites.items()}
        except Exception:  # noqa: BLE001
            return True
        return w != h

    async def _enforce_perms(self, guild, cat, want_fn):
        """Applique want_fn(guild) à la catégorie ET à chacun de ses salons texte, mais
        SEULEMENT en cas de dérive : à l'état stable, zéro appel API (pas de rate-limit).
        `edit(overwrites=)` REMPLACE l'ensemble -> on POSE exactement le schéma voulu."""
        if cat is None:
            return
        want = want_fn(guild)
        if want is None:      # un rôle requis manque -> ne rien toucher (cf. want_fn)
            return
        for ch in [cat] + list(cat.text_channels):
            try:
                if self._ovw_drifted(ch, want):
                    await ch.edit(overwrites=want, reason="permissions R820 (cahier des charges)")
                    log.info("permissions (ré)appliquées: %s", getattr(ch, "name", "?"))
            except discord.HTTPException:
                log.warning("permissions non appliquées: %s", getattr(ch, "name", "?"))

    def _lock_perms_drifted(self, ch, guild):
        """True si les overwrites du salon ne sont pas EXACTEMENT ceux attendus.

        Comparaison de l'ensemble COMPLET, et pas de deux bits : un contrôle qui se
        contentait de vérifier « @everyone est refusé » et « le propriétaire est autorisé »
        était aveugle à un overwrite AJOUTÉ (quelqu'un s'ouvre l'accès à la main → jamais
        corrigé) et à un propriétaire RETIRÉ de la config (son overwrite survivait → accès
        jamais révoqué). Comparaison par id : sans l'intent GUILD_MEMBERS les cibles sont
        des `discord.Object`, dont l'identité d'objet n'est pas stable."""
        try:
            want = {t.id: p for t, p in self._lock_overwrites(guild).items()}
            have = {t.id: p for t, p in ch.overwrites.items()}
        except Exception:
            return True
        return want != have

    async def _ensure_lock_category(self, guild):
        """Adopte la catégorie « Lock » si elle existe, sinon la crée.

        ⚠️ Les overwrites ne sont posés QUE sur une catégorie que le bot vient de créer.
        Une catégorie pré-existante appartient à l'utilisateur — elle peut contenir ses
        propres salons (ici : back/ratio/help/demandes) avec ses propres règles — et
        `edit(overwrites=...)` REMPLACE l'ensemble : le bot effacerait silencieusement des
        accès qu'il n'a pas posés. La confidentialité qui compte est de toute façon portée
        par le salon du nœud lui-même, dont les overwrites, eux, sont maintenus."""
        key_before = self.prov["categories"].get("lock")
        existed = key_before is not None or discord.utils.get(
            guild.categories, name=self.CAT_LOCK) is not None
        cat = await self._ensure_category(guild, self.CAT_LOCK, "lock")
        if not existed:
            try:
                await cat.edit(overwrites=self._lock_overwrites(guild),
                               reason="catégorie Lock créée par le bot : propriétaire seul")
                log.info("catégorie Lock : permissions initiales appliquées")
            except discord.HTTPException:
                log.exception("application des permissions de la catégorie Lock")
        return cat

    async def _provision_node_channel(self, guild):
        """Salon du nœud PVE dans « 🔒 Lock » (dashboard live + console root)."""
        if not getattr(self.cfg, "node_channel_enabled", True):
            return
        cat = await self._ensure_lock_category(guild)
        cid = self.prov.get("node")
        ch = guild.get_channel(cid) if cid else None
        if not isinstance(ch, discord.TextChannel):
            # adoption d'un salon déjà présent dans la catégorie (nom avec ou sans emoji)
            base = fmt.slug(self.cfg.pve_node)
            ch = next((c for c in cat.text_channels
                       if fmt.strip_status_emoji(c.name) == base), None)
        if not isinstance(ch, discord.TextChannel):
            ch = await guild.create_text_channel(
                name=f"🟢-{fmt.slug(self.cfg.pve_node)}", category=cat,
                overwrites=self._lock_overwrites(guild),
                topic=f"Hyperviseur Proxmox « {self.cfg.pve_node} » — "
                      f"état live + console root. Propriétaire uniquement.",
                reason="auto-provision : salon de l'hyperviseur")
            log.info("salon du nœud créé: #%s", ch.name)
        else:
            if ch.category_id != cat.id:
                try:
                    await ch.edit(category=cat, reason="auto-provision: rangement dans Lock")
                except discord.HTTPException:
                    pass
            if self._lock_perms_drifted(ch, guild):
                try:
                    await ch.edit(overwrites=self._lock_overwrites(guild),
                                  reason="salon du nœud : propriétaire uniquement")
                    log.info("salon du nœud : permissions (ré)appliquées")
                except discord.HTTPException:
                    log.exception("application des permissions du salon du nœud")
        self.prov["node"] = ch.id
        return ch

    async def _provision_jellyfin_log_channel(self, guild):
        """Salon #jellyfin-logs dans « 🔒 Lock » : journal d'activité Jellyfin (lecture
        démarrée, compte créé, connexion…), comme dans l'interface admin Jellyfin —
        propriétaire uniquement (demande Nico 2026-07-18)."""
        if not (getattr(self.cfg, "jellyfin_logs_enabled", True) and self.cfg.jellyfin_url
                and self.cfg.jellyfin_api_key):
            return
        cat = await self._ensure_lock_category(guild)
        name = "jellyfin-logs"
        cid = self.prov.get("jellyfin_logs")
        ch = guild.get_channel(cid) if cid else None
        if not isinstance(ch, discord.TextChannel):
            ch = discord.utils.get(cat.text_channels, name=name)
        if not isinstance(ch, discord.TextChannel):
            ch = await guild.create_text_channel(
                name=name, category=cat, overwrites=self._lock_overwrites(guild),
                topic="Journal d'activité Jellyfin (lectures, comptes, connexions) — "
                      "propriétaire uniquement.",
                reason="auto-provision : journal Jellyfin")
            log.info("salon jellyfin-logs créé: #%s", ch.name)
        else:
            if ch.category_id != cat.id:
                try:
                    await ch.edit(category=cat, reason="auto-provision: rangement dans Lock")
                except discord.HTTPException:
                    pass
            if self._lock_perms_drifted(ch, guild):
                try:
                    await ch.edit(overwrites=self._lock_overwrites(guild),
                                  reason="salon jellyfin-logs : propriétaire uniquement")
                except discord.HTTPException:
                    log.exception("application des permissions de jellyfin-logs")
        self.prov["jellyfin_logs"] = ch.id
        return ch

    async def _ensure_text(self, guild, name, category, key_store, key, topic=None,
                           global_fallback=True):
        """Adopte (id persisté -> nom dans la catégorie) ou crée un salon texte.

        global_fallback=False : n'adopte PAS un salon homonyme d'une autre catégorie
        (utilisé pour les salons de guests, dont le nom peut être générique)."""
        cid = key_store.get(key)
        ch = guild.get_channel(cid) if cid else None
        if isinstance(ch, discord.TextChannel):
            if category and ch.category_id != category.id:
                try:
                    await ch.edit(category=category, reason="auto-provision: rangement")
                except discord.HTTPException:
                    pass
            return ch
        # match par nom dans la catégorie cible (adoption d'un salon créé à la main)
        ch = None
        if category:
            ch = discord.utils.get(category.text_channels, name=name)
        if ch is None and global_fallback:
            ch = discord.utils.get(guild.text_channels, name=name)
        if ch is None:
            ch = await guild.create_text_channel(
                name=name, category=category, topic=topic,
                reason="auto-provision bot")
            log.info("salon créé: #%s", name)
        key_store[key] = ch.id
        return ch

    # ------------------------------------------------------------- provisioning

    async def provision_all(self):
        guild = self._guild()
        if guild is None:
            log.warning("guild %s introuvable — provisioning différé", self.cfg.guild_id)
            return
        me = guild.me
        if me is None or not (me.guild_permissions.administrator
                              or me.guild_permissions.manage_channels):
            log.error("permission « Gérer les salons » absente — provisioning impossible")
            return

        async with self._lock:
            # 1) Catégorie + salons de supervision
            sup_cat = await self._ensure_category(guild, self.CAT_SUPERVISION, "supervision")
            for key, cname, topic in SUPER_CHANNELS:
                await self._ensure_text(guild, cname, sup_cat, self.prov["super"], key, topic)
            # accès : @everyone rien, rôle A vision, rôle Gestion boutons (tous les salons)
            await self._enforce_perms(guild, sup_cat, self._managed_overwrites)

            # 1b) ressources PARTAGÉES (#général, #logs-2fa) : primaire uniquement — une
            #     instance secondaire n'y touche pas (elles sont communes à tout le guild).
            if self.is_primary:
                await self._ensure_general_perms(guild)
                await self._ensure_twofa_channel(guild)

            # 2) Catégorie conteneurs : réutiliser celle des salons CT existants si possible
            cont_cat = await self._resolve_containers_category(guild)
            # 2b) serveurs Aveyron : Supervision + Gestion PAR NŒUD (AVY-PVE/NAS/LLM)
            avy_cats = {}          # clé serveur -> catégorie Gestion
            if getattr(self.bot.pve, "avy_enabled", False):
                for node in self.bot.pve.avy_nodes():
                    key = self.bot.pve.avy_server_key(node)
                    try:
                        sup = await self._ensure_category(
                            guild, f"📊 Supervision {key}", f"avy_sup_{node}")
                        gest = await self._ensure_category(
                            guild, f"Gestion {key}", f"avy_gest_{node}")
                        lock = await self._ensure_category(
                            guild, f"🔒 Lock {key}", f"avy_lock_{node}")
                        await self._ensure_avy_sup_channels(guild, node, sup)
                        await self._ensure_avy_lock_hyperviseur(guild, node, lock)
                        # salon dédié au stack IA locale (GPU/services/santé) — UN SEUL
                        # salon, sur le nœud qui héberge la VM (AVY_LLM_NODE), pas un
                        # par nœud comme AVY_SUP_CHANNELS (les autres n'ont pas de GPU)
                        if (getattr(self.bot.pve, "llm_enabled", False)
                                and node == self.cfg.avy_llm_node):
                            await self._ensure_text(
                                guild, "ia-locale", sup, self.prov, "avy_llm_channel",
                                topic="Assistant IA local : GPU, modèle, services, santé.",
                                global_fallback=False)
                        avy_cats[key] = gest
                        ow_fn = self._srv_overwrites_fn(key)
                        await self._enforce_perms(guild, sup, ow_fn)
                        await self._enforce_perms(guild, gest, ow_fn)
                        await self._enforce_perms(guild, lock, self._lock_overwrites_fn(key))
                    except Exception:
                        log.exception("provisioning serveur %s", key)

            # 2c) NAS Synology : serveur à part, supervision seule (voir _provision_syno).
            #     Isolé dans son try : le NAS n'est pas vital pour les salons d'invités.
            if self._syno_enabled():
                try:
                    await self._provision_syno(guild)
                except Exception:
                    log.exception("provisioning serveur %s", self.syno_key)

            # 3) Réconciliation des salons par guest
            await self._reconcile_containers(guild, cont_cat, avy_cats)
            await self._enforce_perms(guild, cont_cat, self._managed_overwrites)

            # 4) Catégorie « 🔒 Lock » + salon de l'hyperviseur (propriétaire uniquement).
            #    Isolé du reste : un échec ici (permissions Discord) ne doit pas empêcher
            #    le provisioning des salons d'invités, qui est la fonction principale.
            try:
                lock_cat = await self._ensure_lock_category(guild)
                await self._provision_node_channel(guild)
                await self._provision_jellyfin_log_channel(guild)
                # toute la catégorie Lock (salon nœud + salons persos) = Gestion+O R820
                await self._enforce_perms(guild, lock_cat, self._lock_overwrites)
            except Exception:
                log.exception("provisioning du salon du nœud")

            # 4b) Salon de chat direct avec l'assistant IA locale (2026-07-18, Nico :
            #     « nouvelle catégorie, uniquement O-LLM qui y a accès ») — indépendant
            #     du reste, jamais bloquant pour le reste du provisioning.
            if getattr(self.bot.pve, "llm_enabled", False):
                try:
                    await self._ensure_assistant_chat(guild)
                except Exception:
                    log.exception("provisioning du salon de chat IA")

            # 5) Recâblage du routage temps réel vers les salons résolus
            self._rewire()
            self._save()

    def _assistant_chat_overwrites(self, guild):
        """@everyone deny ; SEUL le rôle O AVY-LLM (+ le bot) voit/écrit ce salon —
        littéralement, pas M/G AVY-LLM ni aucun rôle R820 (demande explicite de Nico,
        plus restrictif que /assistant qui reste ouvert à tout M/O de tout serveur)."""
        srv = (getattr(self.cfg, "gestion_servers", {}) or {}).get("AVY-LLM", {})
        owner_id = srv.get("owner", 0)
        ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        me = guild.me
        if me is not None:
            ow[me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True,
                read_message_history=True, manage_messages=True)
        if not owner_id or guild.get_role(owner_id) is None:
            log.warning("rôle O AVY-LLM introuvable — overwrites #chat-ia NON modifiés")
            return None
        ow[guild.get_role(owner_id)] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            embed_links=True, add_reactions=True)
        return ow

    async def _ensure_assistant_chat(self, guild):
        cat = await self._ensure_category(guild, "🤖 Assistant IA", "assistant_ia")
        ch = await self._ensure_text(
            guild, "chat-ia", cat, self.prov, "assistant_chat",
            topic="Discute directement avec l'assistant IA locale — pas besoin de "
                  "/assistant, écris simplement ton message.",
            global_fallback=False)
        want = self._assistant_chat_overwrites(guild)
        if want is not None:
            for target in (cat, ch):
                if self._ovw_drifted(target, want):
                    try:
                        await target.edit(overwrites=want, reason="salon assistant IA : O AVY-LLM uniquement")
                    except discord.HTTPException:
                        log.warning("permissions #chat-ia non appliquées: %s",
                                   getattr(target, "name", "?"))

    async def _resolve_containers_category(self, guild):
        # a) catégorie persistée
        cid = self.prov["categories"].get("containers")
        cat = guild.get_channel(cid) if cid else None
        if isinstance(cat, discord.CategoryChannel):
            return cat
        # b) parent commun des salons CT déjà connus (config seed = "Dell R820")
        seed = self.prov["ct"] or dict(self.cfg.ct_channels)
        for chid in seed.values():
            ch = guild.get_channel(chid)
            if isinstance(ch, discord.TextChannel) and ch.category is not None:
                self.prov["categories"]["containers"] = ch.category.id
                return ch.category
        # c) sinon créer une catégorie dédiée
        return await self._ensure_category(guild, self.CAT_CONTAINERS, "containers")

    async def _ensure_avy_sup_channels(self, guild, node, sup_cat):
        """Salons de supervision d'un nœud Aveyron (alertes/hyperviseur/stockage/
        sauvegardes/materiel) ; ids persistés dans prov['avy_sup'][node] pour le cog Avy.
        Les noms sont SUFFIXÉS par le nœud (#alertes-pve…) : quatre #alertes homonymes
        dans l'interface étaient illisibles (retour Nico 2026-07-17)."""
        store = self.prov.setdefault("avy_sup", {}).setdefault(node, {})
        for key, cname, topic in self.AVY_SUP_CHANNELS:
            wanted = f"{cname}-{node}"
            ch = await self._ensure_text(guild, wanted, sup_cat, store, key, topic,
                                         global_fallback=False)
            if ch is not None and ch.name != wanted:
                try:
                    await ch.edit(name=wanted, reason="supervision Aveyron: nom suffixé par nœud")
                except discord.HTTPException:
                    pass

    async def _ensure_avy_lock_hyperviseur(self, guild, node, lock_cat):
        """Salon #hyperviseur-<nœud> Aveyron, rangé dans SA catégorie « 🔒 Lock <clé> »
        (propriétaire uniquement) au lieu de « 📊 Supervision <clé> » (choix Nico
        2026-07-18 : pendant du salon du nœud R820, une catégorie Lock par serveur
        ajouté). MÊME clé de stockage que _ensure_avy_sup_channels
        (prov['avy_sup'][node]['hyperviseur']) : le cog Avy le lit sans changement,
        seuls la catégorie et les droits diffèrent — _ensure_text déplace tout salon
        déjà connu par id vers la nouvelle catégorie automatiquement (migration)."""
        store = self.prov.setdefault("avy_sup", {}).setdefault(node, {})
        wanted = f"hyperviseur-{node}"
        ch = await self._ensure_text(guild, wanted, lock_cat, store, "hyperviseur",
                                     topic=f"Hyperviseur Proxmox « {node} » (Aveyron) — "
                                           f"état live. Propriétaire uniquement.",
                                     global_fallback=False)
        if ch is not None and ch.name != wanted:
            try:
                await ch.edit(name=wanted, reason="hyperviseur Aveyron: nom suffixé par nœud")
            except discord.HTTPException:
                pass
        return ch

    def _syno_enabled(self):
        """Le NAS n'est un « serveur » que si ses rôles G/M/O existent dans
        GESTION_SERVERS. Sans eux, _srv_overwrites_fn refuserait de poser les
        overwrites (garde anti-lockout) et on créerait des salons que personne ne
        verrait — mieux vaut ne rien créer du tout."""
        return bool((getattr(self.cfg, "gestion_servers", {}) or {}).get(self.syno_key))

    async def _provision_syno(self, guild):
        """Catégories + salons du NAS Synology (clé SYNO).

        « 📊 Supervision SYNO » : santé / disques / volumes, lisibles par G+M+O SYNO.
        « 🔒 Lock SYNO » : #synology, la fiche complète (n° de série, DSM, SMART
        attribut par attribut) — M+O seulement, comme le salon de l'hyperviseur.
        Les salons sont suffixés « -nas » : sans ça, #sante/#disques/#volumes se
        mélangeraient visuellement avec les salons homonymes des autres serveurs
        (même reproche que Nico avait fait pour les quatre #alertes d'Aveyron)."""
        store = self.prov.setdefault("syno", {})
        sup = await self._ensure_category(guild, self.CAT_SYNO_SUP, "syno_sup")
        lock = await self._ensure_category(guild, self.CAT_SYNO_LOCK, "syno_lock")
        for key, cname, topic in SYNO_SUP_CHANNELS:
            wanted = f"{cname}-nas"
            ch = await self._ensure_text(guild, wanted, sup, store, key, topic,
                                         global_fallback=False)
            if ch is not None and ch.name != wanted:
                try:
                    await ch.edit(name=wanted, reason="supervision NAS : nom suffixé")
                except discord.HTTPException:
                    pass
        await self._ensure_text(
            guild, "synology", lock, store, "fiche",
            topic="NAS Synology — fiche détaillée (série, DSM, SMART). M/O uniquement.",
            global_fallback=False)
        await self._enforce_perms(guild, sup, self._srv_overwrites_fn(self.syno_key))
        await self._enforce_perms(guild, lock, self._lock_overwrites_fn(self.syno_key))

    def _srv_overwrites_fn(self, key):
        """want_fn des catégories d'un serveur Aveyron : même schéma 3 tiers que
        _managed_overwrites mais avec les rôles G/M/O de la clé `key` dans
        GESTION_SERVERS (+ rôles visionneurs manuels). Garde anti-lockout : si les
        rôles M/O de la clé ne se résolvent pas, on ne touche RIEN."""
        def want_fn(guild):
            srv = (getattr(self.cfg, "gestion_servers", {}) or {}).get(key, {})
            mo_ids = [rid for rid in (srv.get("mod"), srv.get("owner")) if rid]
            ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
            me = guild.me
            if me is not None:
                ow[me] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, embed_links=True,
                    manage_messages=True, manage_channels=True,
                    read_message_history=True, send_messages_in_threads=True)
            if not mo_ids or any(guild.get_role(rid) is None for rid in mo_ids):
                log.warning("rôles M/O %s introuvables — overwrites NON modifiés", key)
                return None
            view_only = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True, send_messages=False,
                add_reactions=False, use_application_commands=False,
                create_public_threads=False, create_private_threads=False,
                send_messages_in_threads=False)
            for rid in [srv.get("view", 0)] + list(self.cfg.viewer_role_ids):
                r = guild.get_role(rid) if rid else None
                if r is not None:
                    ow[r] = view_only
            for rid in mo_ids:
                r = guild.get_role(rid)
                if r is not None:
                    ow[r] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True,
                        read_message_history=True, use_application_commands=True,
                        add_reactions=True, embed_links=True,
                        send_messages_in_threads=True)
            return ow
        return want_fn

    async def _reconcile_containers(self, guild, cont_cat, avy_cats=None):
        """Crée les salons des VM/conteneurs présents, ordonne par VMID — chacun dans
        la catégorie Gestion de SON serveur (R820, ou AVY-<nœud> pour les -avy ; une
        VM/un conteneur migré change de catégorie).

        Un guest disparu de PVE garde son salon là où il est (aucun archivage
        automatique, retiré à la demande de Nico 2026-07-18 : un salon ne doit pas
        bouger juste parce que la VM/le conteneur est éteint ou temporairement
        injoignable)."""
        # seed initial du mapping name->channel depuis la config (adoption)
        if not self.prov["ct"] and self.cfg.ct_channels:
            self.prov["ct"] = dict(self.cfg.ct_channels)

        guests = await self._guests()   # {name: (vmid, clé serveur|None)} (None si PVE indispo)
        if guests is None:
            log.info("PVE indisponible — salons CT conservés tels quels, pas de réconciliation")
            return

        def _gcat(name):
            key = guests[name][1]
            return (avy_cats or {}).get(key) or cont_cat

        # a) guests présents -> création (ou recalage de catégorie) ; les guests qui ne
        #    sont plus dans `guests` sont simplement ignorés ici, leur salon ne bouge pas
        for name in guests:
            cat = _gcat(name)
            if name in self.prov["ct"]:
                await self._ensure_text(guild, _slug(name), cat,
                                        self.prov["ct"], name,
                                        topic=f"VM/conteneur Proxmox « {name} ».",
                                        global_fallback=False)
                continue
            if name in self.prov["archived"]:            # ancien salon archivé -> réactiver
                cid = self.prov["archived"].pop(name)
                ch = guild.get_channel(cid)
                if isinstance(ch, discord.TextChannel):
                    try:
                        await ch.edit(category=cat, reason=f"guest {name} recréé")
                    except discord.HTTPException:
                        pass
                    self.prov["ct"][name] = cid
                    continue
            await self._ensure_text(guild, _slug(name), cat,
                                    self.prov["ct"], name,
                                    topic=f"VM/conteneur Proxmox « {name} ».",
                                    global_fallback=False)

        # c) tri par VMID (l'ID donne la position) — seulement si l'ordre a changé,
        #    PAR catégorie (le tri est intra-catégorie : chaque serveur trie les siens)
        groups = {}
        for n in self.prov["ct"]:
            if n in guests:
                groups.setdefault(guests[n][1], []).append(n)
        prim = sorted(groups.get(None, []), key=lambda n: guests[n][0])
        if prim != self.prov.get("order"):
            await self._apply_order(guild, prim, cont_cat)
            self.prov["order"] = prim
        for key, cat in (avy_cats or {}).items():
            lst = sorted(groups.get(key, []), key=lambda n: guests[n][0])
            if lst != self.prov.get(f"order_{key}"):
                await self._apply_order(guild, lst, cat)
                self.prov[f"order_{key}"] = lst

    async def _apply_order(self, guild, order, cont_cat):
        # move() gère les positions INTRA-catégorie (edit(position=) est global au guild)
        for i, name in enumerate(order):
            ch = guild.get_channel(self.prov["ct"][name])
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.move(category=cont_cat, beginning=True, offset=i,
                                  reason="tri par VMID")
                except discord.HTTPException:
                    pass

    async def _guests(self):
        """{name: (vmid, clé serveur)} pour tous les LXC/VM, ou None si PVE indisponible.
        Clé serveur = None pour le R820, « AVY-<NŒUD> » pour un invité Aveyron."""
        if not self.bot.pve.enabled:
            return None
        try:
            res = await asyncio.to_thread(self.bot.pve.resources)
        except Exception:
            log.exception("lecture des ressources PVE échouée")
            return None
        pve = self.bot.pve
        d = {}
        for r in res:
            if not r.get("name") or r.get("vmid") is None:
                continue
            key = None
            if pve.is_avy_name(r["name"]):
                # -avy sans nœud résolu (réponse Aveyron partielle) : NE PAS le classer.
                # Sinon key=None -> _gcat se rabat sur la catégorie R820 = bug des salons
                # -avy atterrissant dans « Gestion R820 ». On l'ignore ce cycle ; son salon
                # reste où il est et sera replacé dans sa vraie catégorie AVY-<nœud> dès
                # qu'Aveyron renverra son nœud.
                if not r.get("node"):
                    continue
                key = pve.avy_server_key(r["node"])
            d[r["name"]] = (r["vmid"], key)
        # liste vide = anomalie (token filtré / glitch PVE) -> traiter comme indisponible
        # pour NE PAS archiver tous les salons par erreur.
        return d or None

    # ------------------------------------------------------------------ rewiring

    def _rewire(self):
        if self.prov.get("assistant_chat"):
            self.cfg.assistant_chat_channel_id = self.prov["assistant_chat"]
        s = self.prov["super"]
        if s.get("alertes"):
            self.cfg.alert_channel_id = s["alertes"]
            # 1er câblage vers un nouveau salon d'alertes -> repost unique des alertes
            # actives (l'edge-trigger d'Alerts ne reposte pas un état déjà "firing").
            if self.prov.get("alerts_wired_to") != s["alertes"]:
                self.prov["alerts_wired_to"] = s["alertes"]
                self.bot.state.set("alerts", {})
        if s.get("rapports"):
            self.cfg.report_channel_id = s["rapports"]
        if s.get("journaux_live"):
            self.cfg.live_log_channel_id = s["journaux_live"]
            logs_cog = self.bot.get_cog("Logs")
            if logs_cog is not None and getattr(logs_cog, "stream", None) is not None:
                logs_cog.stream.channel_id = s["journaux_live"]
        # mapping guest->salon lu en direct par le routeur de logs
        self.cfg.ct_channels = dict(self.prov["ct"])
        ctcog = self.bot.get_cog("CtChannels")
        if ctcog is not None:
            ctcog.map = dict(self.prov["ct"])
            if not ctcog.refresh.is_running():
                ctcog.refresh.change_interval(minutes=self.cfg.dashboard_interval_min)
                ctcog.refresh.start()
        # salon de l'hyperviseur : le cog NodeChannel le lit en direct (comme CtChannels.map)
        nodecog = self.bot.get_cog("NodeChannel")
        if nodecog is not None and self.prov.get("node") and nodecog.enabled:
            nodecog.channel_id = self.prov["node"]
            if not nodecog.refresh.is_running():
                nodecog.refresh.change_interval(minutes=self.cfg.dashboard_interval_min)
                nodecog.refresh.start()
        # journal d'activité Jellyfin : le cog JellyfinActivity le lit en direct
        jfcog = self.bot.get_cog("JellyfinActivity")
        if jfcog is not None and self.prov.get("jellyfin_logs"):
            jfcog.channel_id = self.prov["jellyfin_logs"]
            if not jfcog.poll.is_running():
                jfcog.poll.start()

    # ----------------------------------------------------- rendus thématiques

    async def _pinned_edit(self, channel_id, embed, pending=None):
        """Édite le message épinglé du salon (créé+épinglé au 1er passage).

        `pending` = baselines de delta à avancer UNIQUEMENT après une écriture
        confirmée : un refresh raté ne doit pas consommer la variation du cycle (#21)."""
        if not channel_id:
            return
        ch = self.bot.get_channel(channel_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(channel_id)
            except Exception:
                return
        pins = self.prov.setdefault("pins", {})
        mid = pins.get(str(channel_id))
        msg = None
        if mid:
            try:
                msg = await ch.fetch_message(mid)
            except discord.NotFound:
                msg = None
            except discord.HTTPException:
                return
        view = TopicalRefreshView(self)
        ok = False
        try:
            if msg is None:
                msg = await ch.send(embed=embed, view=view)
                try:
                    await msg.pin()
                except discord.HTTPException:
                    pass
                pins[str(channel_id)] = msg.id
                self._save()
                ok = True
            else:
                await msg.edit(embed=embed, view=view)
                ok = True
        except discord.HTTPException:
            pass
        # baselines de delta avancées seulement après une écriture confirmée (#21)
        if ok and pending:
            for k, v in pending.items():
                self.bot.state.set(k, v)

    async def _emb_materiel(self):
        inf = self.bot.influx
        pending = {}          # baselines de delta à persister APRÈS écriture réussie (#21)
        emb = discord.Embed(title="🔧 Matériel", color=fmt.BLURPLE)
        emb.timestamp = discord.utils.utcnow()
        power = await inf.ipmi_power()
        if power:
            emb.add_field(name="Alimentation (W)",
                          value="\n".join(f"{n}: {(v or 0):.0f}" for n, v in power)[:1024] or "—")
        pc = await inf.power_cost()
        if pc:
            try:
                emb.add_field(
                    name="💶 Coût électrique",
                    value=(f"{float(pc.get('watts', 0)):.0f} W · "
                           f"**{float(pc.get('eur_day', 0)):.2f} €/jour** · "
                           f"~{float(pc.get('eur_month', 0)):.0f} €/mois "
                           f"({float(pc.get('kwh_day', 0)):.1f} kWh/j @ "
                           f"{float(pc.get('tariff_eur_kwh', 0)):.3f} €/kWh)"),
                    inline=False)
            except (TypeError, ValueError):
                pass
        fans = await inf.ipmi_fans()
        if fans:
            emb.add_field(name="Ventilateurs (RPM)",
                          value="\n".join(f"{n}: {int(v or 0)}" for n, v in fans[:12])[:1024] or "—")
        temps = [(n, v) for n, v in (await inf.ipmi_temps() or []) if v is not None]
        if temps:
            mx = max(v for _, v in temps)
            emb.add_field(name="Températures (°C)",
                          value="\n".join(f"{n}: {v:.0f}" for n, v in temps[:12])[:1024])
            if mx >= 40:
                emb.color = fmt.RED if mx >= 60 else fmt.YELLOW
            _p = self.bot.state.get("prov_prev_mat_temp")
            emb.add_field(name="Δ temp max (depuis refresh)",
                          value=f"{mx:.0f}°C{_pdelta(mx, _p, '°C')} · seuils ⚠️≥40 / 🔥≥60",
                          inline=False)
            pending["prov_prev_mat_temp"] = mx
        ctrl, summ = await inf.raid()
        if summ:
            disks = await inf.raid_disks()
            gd = max((int(d.get("grown_defects", 0) or 0) for d in disks), default=0)
            emb.add_field(
                name="RAID",
                value=(f"{int(summ.get('disks_online', 0))}/{int(summ.get('disks_total', 0))} online"
                       + (" · optimal ✅" if (ctrl or {}).get("vd_optimal") else " · ⚠️ dégradé")
                       + f" · grown max {gd}"))
            if ctrl and not ctrl.get("vd_optimal"):
                emb.color = fmt.RED
        health = await inf.smart_health()
        if health:
            bad = [d for d, h in health if not bool(h)]
            emb.add_field(name="SMART",
                          value=f"{len(health) - len(bad)}/{len(health)} sains"
                                + (f" · ❌ {bad}" if bad else " ✅"))
            if bad:
                emb.color = fmt.RED
        emb.set_footer(text="rafraîchi")
        return emb, pending

    async def _emb_sauvegardes(self):
        inf = self.bot.influx
        pending = {}          # baselines de delta à persister APRÈS écriture réussie (#21)
        emb = discord.Embed(title="🛟 Sauvegardes (PBS)", color=fmt.GREEN)
        emb.timestamp = discord.utils.utcnow()
        summ = await inf.backup_summary()
        ages = await inf.backup_ages() or []
        if summ:
            nob = int(summ.get("guests_without_backup", 0))
            emb.add_field(name="Plus ancienne",
                          value=fmt.humanize_duration(summ.get("oldest_age_seconds")))
            emb.add_field(name="Sans sauvegarde",
                          value=(f"{nob} / {len(ages)} guests" if ages else str(nob)))
            if nob:
                emb.color = fmt.YELLOW
            _p = self.bot.state.get("prov_prev_nob")
            emb.add_field(name="Δ sans-backup (depuis refresh)",
                          value=f"{nob}{_pdelta(nob, _p)}", inline=False)
            pending["prov_prev_nob"] = nob
            # volume réel des sauvegardes sur le NAS (logique vs dédupliqué)
            logical = float(summ.get("total_logical_bytes", 0) or 0)
            real = float(summ.get("real_used_bytes", 0) or 0)
            dedup = float(summ.get("dedup_factor", 0) or 0)
            snaps = int(summ.get("snapshots_total", 0) or 0)
            if logical:
                emb.add_field(
                    name="📦 Volume sauvegardé (sur le NAS)",
                    value=(f"{fmt.humanize_bytes(logical)} logique → **{fmt.humanize_bytes(real)}** "
                           f"réel sur disque · dédup **×{dedup:.1f}** · {snaps} snapshots"),
                    inline=False)
        lines = []
        for r in ages:
            age = r.get("age_seconds")
            name = r.get("name") or r.get("vmid")
            sz = r.get("size_bytes")
            szs = f" · {fmt.humanize_bytes(sz)}" if sz else ""
            if age is None or age < 0 or not r.get("has_backup"):
                lines.append(f"❌ **{name}** — aucune")
                emb.color = fmt.RED
            else:
                flag = "⚠️" if age > 108000 else "✅"
                lines.append(f"{flag} **{name}** — {fmt.humanize_duration(age)}{szs}")
        if lines:
            emb.add_field(name="Par guest", value="\n".join(lines)[:1024], inline=False)
        emb.set_footer(text="rafraîchi")
        return emb, pending

    async def _emb_stockage(self):
        inf = self.bot.influx
        pending = {}          # baselines de delta à persister APRÈS écriture réussie (#21)
        emb = discord.Embed(title="🗄️ Stockage", color=fmt.BLURPLE)
        emb.timestamp = discord.utils.utcnow()
        tp = await inf.thinpool()
        if tp and tp.get("pool_bytes"):
            poolb = tp.get("pool_bytes")
            du = tp.get("data_used_bytes")
            oc = tp.get("overcommit_percent")
            # USAGE RÉEL (données écrites / pool) = le VRAI risque : un thinpool plein
            # corrompt les volumes. C'est LUI qui doit rester < 100 % et pilote la couleur.
            dp = tp.get("data_percent")
            if dp is None:
                dp = (du / poolb * 100) if (du and poolb) else 0.0
            dp = float(dp)
            emb.add_field(name=f"Thinpool local-lvm — {dp:.0f}% utilisé",
                          value=fmt.pct_of(du, poolb, decimals=1))
            if dp >= 90:
                emb.color = fmt.RED
            elif dp >= 85:
                emb.color = fmt.YELLOW
            # surallocation = INFO (dépasse 100 % en thin provisioning, c'est normal)
            if oc is not None:
                alloc = tp.get("allocated_bytes")
                oc_ctx = (f" ({fmt.humanize_bytes(alloc)} alloués / {fmt.humanize_bytes(poolb)} pool)"
                          if alloc is not None else "")
                emb.add_field(name="Surprovisionnement (info)", value=f"{oc:.0f} %{oc_ctx}")
        st = await inf.storages()
        if st:
            lines = [f"{'⚠️' if s.get('pct', 0) >= 85 else '•'} {s['name']} — "
                     f"{fmt.pct_of(s.get('used', 0), s.get('total', 0))}"
                     for s in st[:12]]
            emb.add_field(name="Storages Proxmox", value="\n".join(lines)[:1024], inline=False)
            if any(s.get("pct", 0) >= 90 for s in st):
                emb.color = fmt.RED
            mxp = max((s.get("pct", 0) for s in st), default=0)
            _p = self.bot.state.get("prov_prev_sto_pct")
            emb.add_field(name="Δ remplissage max (depuis refresh)",
                          value=f"{mxp:.0f}%{_pdelta(mxp, _p, '%')}", inline=False)
            pending["prov_prev_sto_pct"] = mxp
        # prévision de saturation (projection linéaire ~7 j) pour les disques qui grossissent
        fc_lines = []
        for path in ("/mnt/media", "/"):
            try:
                fc = await inf.disk_forecast(path)
            except Exception:
                fc = None
            if fc and fc.get("days") is not None:
                d = fc["days"]
                icon = "🔴" if d < 30 else ("🟠" if d < 90 else "🟢")
                when = "moins d'1 jour" if d < 1 else f"~{d:.0f} jours"
                # tailles réelles en plus du % (demande Nico 2026-07-20), ex. « 3,3 To / 3,8 To »
                ub, tb = fc.get("used_bytes"), fc.get("total_bytes")
                sizes = f" ({fmt.humanize_bytes(ub)} / {fmt.humanize_bytes(tb)})" if (ub and tb) else ""
                fc_lines.append(f"{icon} `{path}` {fc['current']:.0f}%{sizes} → plein dans **{when}**")
                if d < 30:
                    emb.color = fmt.RED
        if fc_lines:
            emb.add_field(name="📈 Prévision de saturation",
                          value="\n".join(fc_lines), inline=False)
        emb.set_footer(text="rafraîchi")
        return emb, pending

    async def _emb_seedbox(self):
        """Seedbox : ratio, upload, VPN/PF, sync, et top torrents uploaders."""
        inf = self.bot.influx
        b = self.cfg.influx_bucket
        pending = {}          # baselines de delta à persister APRÈS écriture réussie (#21)

        def piv(meas, keys):
            rk = ", ".join(f'"{k}"' for k in keys)
            return (f'from(bucket:"{b}") |> range(start:-6m) '
                    f'|> filter(fn:(r)=> r._measurement=="{meas}") |> last() '
                    f'|> pivot(rowKey:[{rk}], columnKey:["_field"], valueColumn:"_value")')

        qbr = await inf.aq(piv("qbittorrent", ["host"]))
        vpnr = await inf.aq(piv("servarr_vpn", ["host", "country"]))
        syncr = await inf.aq(piv("servarr_sync", ["host"]))
        qb = qbr[0] if qbr else {}
        vpn = vpnr[0] if vpnr else {}
        sync = syncr[0] if syncr else {}

        # Stats C411 = EXACTEMENT celles de #ratio : même source de vérité, partagée via
        # Servarr.c411_snapshot() (relève officielle tracker > estimation /setratio+qBit >
        # saisie manuelle) — jamais les chiffres locaux qBittorrent (ratio de session,
        # upload/download cumulés du client) qui en divergent.
        srv = self.bot.get_cog("Servarr")
        snap = await srv.c411_snapshot() if srv else None
        if snap:
            ratio, up_to, dl_go = snap["ratio"], snap["up_to"], snap["dl_go"]
            bonus, src = snap["bonus_go"], snap["source"]
        else:   # repli si le cog Servarr n'est pas chargé
            c411 = self.bot.state.get("c411") or {}
            ratio = float(c411.get("ratio", 0) or 0) or float(qb.get("ratio", 0) or 0)
            up_to = float(c411.get("up_to", 0) or 0)
            dl_go = float(c411.get("dl_go", 0) or 0)
            bonus = float(c411.get("bonus_go", 0) or 0)
            src = "manuel"
        color = fmt.GREEN if ratio >= 1 else (fmt.YELLOW if ratio >= 0.8 else fmt.RED)
        emb = discord.Embed(title="🧲 Seedbox — ratio & torrents", color=color)
        emb.timestamp = discord.utils.utcnow()
        if not qb:
            emb.description = "Aucune donnée (collecteur `servarr-metrics` muet ?)."
            emb.color = fmt.RED
            emb.set_footer(text="rafraîchi")
            return emb, pending

        emb.add_field(name=f"Ratio C411 ({src})", value=f"**{ratio:.2f}**")
        b_label = "crédit" if src == "officiel" else "bonus"
        up_bonus = f"\n(dont {bonus:.1f} Go {b_label})" if bonus >= 0.1 else ""
        emb.add_field(name="⬆️ Upload C411", value=f"{up_to:.3f} To{up_bonus}")
        emb.add_field(name="⬇️ Download C411", value=f"{dl_go:.1f} Go")
        emb.add_field(name="Débit ↑ / ↓",
                      value=f"{fmt.humanize_rate(qb.get('up_speed',0) or 0)} / "
                            f"{fmt.humanize_rate(qb.get('dl_speed',0) or 0)}")
        emb.add_field(name="Torrents",
                      value=f"{int(qb.get('seeding',0) or 0)} seed / {int(qb.get('torrents_total',0) or 0)}")
        emb.add_field(name="Pairs", value=str(int(qb.get("peers", 0) or 0)))
        ul = int(qb.get("alltime_ul", 0) or 0)
        _p = self.bot.state.get("prov_prev_seed_ul")
        d = ul - int(_p) if _p is not None else 0
        emb.add_field(name="📈 Uploadé depuis le refresh",
                      value=(f"▲ {fmt.humanize_bytes(d)}" if d > 0 else "—"), inline=False)
        pending["prov_prev_seed_ul"] = ul

        if vpn:
            up = int(vpn.get("up", 0) or 0)
            pf = int(vpn.get("pf_ok", 0) or 0)
            extra = (f" · 🔁 sync {fmt.status_emoji(int(sync.get('usersync_up',0) or 0))}"
                     if sync else "")
            emb.add_field(
                name="VPN & sync", inline=False,
                value=f"{fmt.status_emoji(up)} {vpn.get('country','?')} "
                      f"(`{vpn.get('egress','?')}`) · Port-forward "
                      f"{'✅' if pf else '❌'} :{int(vpn.get('listen_port',0) or 0)}{extra}")

        top_flux = (
            f'from(bucket:"{b}") |> range(start:-8m) '
            f'|> filter(fn:(r)=> r._measurement=="qbit_torrent" '
            f'and (r._field=="uploaded" or r._field=="upspeed")) |> last() '
            f'|> pivot(rowKey:["name"], columnKey:["_field"], valueColumn:"_value") '
            f'|> group() |> sort(columns:["uploaded"], desc:true) |> limit(n:10)')
        tops = await inf.aq(top_flux)
        if tops:
            lines = []
            for i, t in enumerate(tops, 1):
                up = t.get("uploaded", 0) or 0
                sp = t.get("upspeed", 0) or 0
                nm = str(t.get("name", "?"))[:46]
                arrow = f" ↑{fmt.humanize_rate(sp)}" if sp > 0 else ""
                lines.append(f"`{i:>2}.` **{fmt.humanize_bytes(up)}**{arrow} · {nm}")
            emb.add_field(name="🏆 Top uploaders", value="\n".join(lines)[:1024], inline=False)
        emb.set_footer(text="rafraîchi")
        return emb, pending

    async def _emb_nas(self):
        """NAS Synology DS216se : capacité (via l'API PVE) + santé SNMP (si activé)."""
        pending = {}
        inf = self.bot.influx
        cap = await inf.nas_capacity()
        h = await inf.nas_health()
        sysd = (h or {}).get("system") or {}
        color = fmt.GREEN
        emb = discord.Embed(title="🗄️ NAS Synology — DS216se", color=color)
        emb.timestamp = discord.utils.utcnow()
        # --- capacité (toujours dispo, lue côté hyperviseur) ---
        if cap:
            total = float(cap.get("total", 0) or 0)
            used = float(cap.get("used", 0) or 0)
            pct = (used / total * 100) if total else 0
            emb.add_field(
                name="Capacité (volume de sauvegarde)",
                value=f"{fmt.pct_bar(pct)}\n{fmt.pct_of(used, total, decimals=1)}",
                inline=False)
            if pct >= 90:
                color = fmt.RED
            elif pct >= 75:
                color = fmt.YELLOW
        else:
            emb.add_field(name="Capacité", value="— (collecteur muet ?)", inline=False)
        # --- santé (SNMP) ---
        if sysd:
            t = sysd.get("system_temp_c")
            st = int(float(sysd.get("system_status", 0) or 0))
            fan = int(float(sysd.get("system_fan_status", 0) or 0))
            emb.add_field(name="Système",
                          value=f"{'🟢' if st == 1 else '🔴'} état · {t}°C · "
                                f"ventilo {'🟢' if fan == 1 else '🔴'}")
            dlines = []
            for d in (h.get("disks") or []):
                ds = int(float(d.get("status", 0) or 0))
                dlines.append(f"{'🟢' if ds == 1 else '🔴'} {d.get('disk_id', '?')} — {d.get('temp_c', '?')}°C")
                if ds != 1:
                    color = fmt.RED
            if dlines:
                emb.add_field(name="Disques", value="\n".join(dlines), inline=False)
            bad = 0
            for r in (h.get("smart") or []):
                try:
                    bad += int(float(r.get("raw", 0) or 0))
                except (TypeError, ValueError):
                    pass
            if bad:
                emb.add_field(name="⚠️ Secteurs défectueux (SMART)",
                              value=f"**{bad}** — `sda` à surveiller / remplacer", inline=False)
                color = fmt.RED
            for rd in (h.get("raid") or []):
                rs = int(float(rd.get("status", 0) or 0))
                emb.add_field(name="RAID",
                              value=f"{'🟢 Normal' if rs == 1 else ('🟠 Dégradé' if rs == 11 else '🔴 Critique')}"
                                    f" ({rd.get('raid_name', '?')})")
                if rs != 1:
                    color = fmt.RED
        else:
            emb.add_field(
                name="Santé disques / SMART / RAID / température",
                value="⚠️ Indisponible — **active SNMP** sur le NAS (DSM → Panneau de config → "
                      "Terminal & SNMP → cocher SNMP, communauté `homelab`). Ça se remplira tout seul.",
                inline=False)
        emb.color = color
        emb.set_footer(text="capacité via PVE · santé via SNMP · rafraîchi")
        return emb, pending

    # ------------------------------------------------ NAS Synology (serveur SYNO)
    # Toutes les valeurs viennent des mesures synology* d'InfluxDB, alimentées par
    # l'input SNMP de Telegraf sur l'hyperviseur. Le bot ne parle jamais au NAS
    # directement, et n'a aucun moyen d'agir dessus : supervision en lecture seule.
    @staticmethod
    def _syno_i(d, key, default=0):
        """Champ SNMP -> entier (Influx renvoie des flottants, parfois des chaînes)."""
        try:
            return int(float((d or {}).get(key, default) or default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _syno_muet():
        """Message unique quand SNMP ne répond plus, pour ne pas laisser un salon vide
        sans explication (cause la plus fréquente : SNMP décoché dans DSM)."""
        emb = discord.Embed(
            title="🗄️ NAS Synology — pas de données",
            description="Aucune métrique SNMP depuis plus de 15 minutes.\n"
                        "À vérifier : DSM → Panneau de configuration → **Terminal & SNMP** "
                        "(SNMP v2c coché, communauté `homelab`), et que le NAS est allumé.",
            color=fmt.GREY)
        emb.timestamp = discord.utils.utcnow()
        return emb, {}

    async def _emb_syno_sante(self):
        h = await self.bot.influx.nas_health()
        sysd = (h or {}).get("system") or {}
        if not sysd:
            return self._syno_muet()
        st = self._syno_i(sysd, "system_status")
        power = self._syno_i(sysd, "power_status")
        sfan = self._syno_i(sysd, "system_fan_status")
        cfan = self._syno_i(sysd, "cpu_fan_status")
        temp = self._syno_i(sysd, "system_temp_c")
        ok = lambda v: "🟢" if v == 1 else "🔴"
        color = fmt.GREEN if all(v == 1 for v in (st, power, sfan, cfan)) else fmt.RED
        if color == fmt.GREEN and temp >= 50:
            color = fmt.YELLOW

        emb = discord.Embed(
            title=f"🗄️ {sysd.get('model') or 'Synology'} — santé",
            color=color)
        emb.timestamp = discord.utils.utcnow()
        emb.add_field(name="État", value=f"{ok(st)} système\n{ok(power)} alimentation")
        emb.add_field(name="Refroidissement",
                      value=f"{ok(sfan)} ventilateur\n{ok(cfan)} ventilateur CPU")
        emb.add_field(name="Température", value=f"**{temp} °C**")

        up = self._syno_i(sysd, "uptime")          # Timeticks = centièmes de seconde
        load = self._syno_i(sysd, "load1_x100") / 100.0
        idle = self._syno_i(sysd, "cpu_idle_pct", 100)
        emb.add_field(name="Charge",
                      value=f"CPU {max(0, 100 - idle)} % · charge {load:.2f}")
        total_kb = self._syno_i(sysd, "mem_total_kb")
        if total_kb:
            libre_kb = (self._syno_i(sysd, "mem_avail_kb")
                        + self._syno_i(sysd, "mem_buffer_kb")
                        + self._syno_i(sysd, "mem_cached_kb"))
            used = max(0, total_kb - libre_kb) * 1024
            emb.add_field(name="Mémoire",
                          value=fmt.pct_of(used, total_kb * 1024, decimals=1))
        if up:
            emb.add_field(name="Allumé depuis", value=fmt.humanize_duration(up / 100))

        maj = self._syno_i(sysd, "upgrade_available")
        if maj == 1:
            emb.add_field(name="Mise à jour DSM",
                          value="🟠 Une mise à jour est disponible", inline=False)
        # dsm_version contient déjà « DSM 7.1-… » : ne pas préfixer une seconde fois.
        emb.set_footer(text=f"{sysd.get('dsm_version') or 'DSM ?'} · SNMP · lecture seule")
        return emb, {}

    async def _emb_syno_disques(self):
        h = await self.bot.influx.nas_health()
        disks = (h or {}).get("disks") or []
        if not disks:
            return self._syno_muet()
        # SMART brut par disque : c'est LA valeur qui dit si un disque se dégrade.
        smart = {}
        for r in (h.get("smart") or []):
            dev = (r.get("dev") or "").rsplit("/", 1)[-1]
            smart.setdefault(dev, {})[r.get("attr_name")] = self._syno_i(r, "raw")

        emb = discord.Embed(title="🗄️ NAS Synology — disques", color=fmt.GREEN)
        emb.timestamp = discord.utils.utcnow()
        pire = 0                                   # 0 = sain, 1 = à surveiller, 2 = grave
        for d in sorted(disks, key=lambda x: str(x.get("disk_id"))):
            status = self._syno_i(d, "status")
            health = self._syno_i(d, "health_status")
            bad = self._syno_i(d, "bad_sector")
            temp = self._syno_i(d, "temp_c")
            # status 1 = Normal (2+ = souci) ; health 1 = Normal, 2 = Warning, 3+ = Critical.
            niveau = 2 if (status != 1 or health >= 3) else (1 if (health == 2 or bad) else 0)
            pire = max(pire, niveau)
            mark = ("🟢", "🟠", "🔴")[niveau]
            lignes = [f"{d.get('disk_model') or '?'} · **{temp} °C**",
                      f"⚠️ **{bad}** secteur(s) défectueux" if bad
                      else "aucun secteur défectueux"]
            # « Disk 1 » -> sda, « Disk 2 » -> sdb : DSM numérote les baies à partir de 1
            # et nomme les périphériques dans le même ordre.
            num = "".join(c for c in str(d.get("disk_id") or "") if c.isdigit())
            devsm = smart.get(f"sd{chr(ord('a') + int(num) - 1)}", {}) if num else {}
            if devsm:
                lignes.append(f"SMART : {devsm.get('Reallocated_Sector_Ct', 0)} réalloué(s), "
                              f"{devsm.get('Current_Pending_Sector', 0)} en attente")
            emb.add_field(name=f"{mark} {d.get('disk_id') or '?'}",
                          value="\n".join(lignes), inline=False)
        emb.color = (fmt.GREEN, fmt.YELLOW, fmt.RED)[pire]
        emb.set_footer(text="SNMP · un secteur réalloué isolé et stable n'est pas alarmant ; "
                            "c'est sa progression qui compte")
        return emb, {}

    async def _emb_syno_volumes(self):
        h = await self.bot.influx.nas_health()
        raids = (h or {}).get("raid") or []
        if not raids:
            return self._syno_muet()
        emb = discord.Embed(title="🗄️ NAS Synology — volumes", color=fmt.GREEN)
        emb.timestamp = discord.utils.utcnow()
        pire = 0                                   # 0 = sain, 1 = à surveiller, 2 = grave
        for rd in sorted(raids, key=lambda x: str(x.get("raid_name"))):
            nom = str(rd.get("raid_name") or "?")
            status = self._syno_i(rd, "status")
            total = self._syno_i(rd, "total_bytes")
            free = self._syno_i(rd, "free_bytes")
            # status : 1 = Normal, 11 = Dégradé, 12 = Crashé (2-10 = états transitoires).
            etat = ("🟢 Normal" if status == 1
                    else ("🟠 Dégradé" if status == 11 else "🔴 Critique"))
            if status != 1:
                pire = 2
            # Un « Storage Pool » affiche comme espace libre ce qui n'est pas encore
            # ALLOUÉ à un volume : une fois le volume créé il tombe à zéro, ce qui est
            # normal. En faire une barre de remplissage afficherait 100 % en permanence
            # et rendrait le salon rouge à tort — seuls les volumes sont mesurés.
            if "pool" in nom.lower():
                emb.add_field(name=f"{nom} — {etat}",
                              value=f"{fmt.humanize_bytes(total)} de disques\n"
                                    f"{fmt.humanize_bytes(free)} non alloués",
                              inline=False)
                continue
            used = max(0, total - free)
            pct = (used / total * 100) if total else 0
            pire = max(pire, 2 if pct >= 90 else (1 if pct >= 75 else 0))
            emb.add_field(
                name=f"{nom} — {etat}",
                value=f"{fmt.pct_bar(pct)}\n{fmt.pct_of(used, total, decimals=1)}",
                inline=False)
        emb.color = (fmt.GREEN, fmt.YELLOW, fmt.RED)[pire]
        emb.set_footer(text="SNMP · « Storage Pool » = le groupe de disques, "
                            "« Volume » = l'espace utilisable dessus")
        return emb, {}

    async def _emb_syno_fiche(self):
        """Fiche détaillée, catégorie Lock : identité de la machine + SMART complet."""
        h = await self.bot.influx.nas_health()
        sysd = (h or {}).get("system") or {}
        if not sysd:
            return self._syno_muet()
        emb = discord.Embed(title="🗄️ NAS Synology — fiche détaillée", color=fmt.BLURPLE)
        emb.timestamp = discord.utils.utcnow()
        emb.add_field(name="Modèle", value=str(sysd.get("model") or "?"))
        emb.add_field(name="DSM", value=str(sysd.get("dsm_version") or "?"))
        serial = sysd.get("serial")
        if serial:
            emb.add_field(name="N° de série", value=f"`{serial}`")
        emb.add_field(name="Adresse", value=f"`{getattr(self.cfg, 'syno_host', '?')}`")

        for d in sorted((h.get("disks") or []), key=lambda x: str(x.get("disk_id"))):
            emb.add_field(
                name=str(d.get("disk_id") or "?"),
                value=f"{d.get('disk_model') or '?'}\n"
                      f"{self._syno_i(d, 'temp_c')} °C · "
                      f"{self._syno_i(d, 'bad_sector')} secteur(s) défectueux",
                inline=False)
        lignes = []
        for r in sorted((h.get("smart") or []),
                        key=lambda x: (str(x.get("dev")), str(x.get("attr_name")))):
            lignes.append(f"`{(r.get('dev') or '?').rsplit('/', 1)[-1]}` "
                          f"{r.get('attr_name')} = **{self._syno_i(r, 'raw')}**")
        if lignes:
            emb.add_field(name="SMART (valeurs brutes)",
                          value="\n".join(lignes[:12]), inline=False)
        emb.set_footer(text="SNMP · lecture seule · M/O SYNO uniquement")
        return emb, {}

    async def _emb_reseau(self):
        """Réseau : routeur CRS305 (SNMP), WAN (ping/speedtest), services publics (uptime)."""
        pending = {}
        inf = self.bot.influx
        rt = await inf.router_status()
        wan = await inf.wan_status()
        vh = await inf.vhost_uptime()
        color = fmt.GREEN
        emb = discord.Embed(title="🌐 Réseau — routeur · WAN · services publics", color=color)
        emb.timestamp = discord.utils.utcnow()

        def f(d, k):
            try:
                return float(d.get(k, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        h = rt.get("host") or {}
        if h:
            mt, mu = f(h, "mem_total_blocks"), f(h, "mem_used_blocks")
            mempct = (mu / mt * 100) if mt else 0
            temp = f(h, "cpu_temp")
            emb.add_field(
                name="🧭 Routeur CRS305",
                value=(f"🟢 en ligne · CPU {f(h, 'cpu_load'):.0f}% · {temp:.0f}°C · "
                       f"RAM {mempct:.0f}% · uptime {fmt.humanize_duration(int(f(h, 'uptime') / 100))}"),
                inline=False)
            if temp >= 65:
                color = fmt.RED
        else:
            emb.add_field(name="🧭 Routeur CRS305", value="⚠️ pas de données SNMP", inline=False)
        # WAN latence + débit
        inet = [r for r in (wan.get("ping") or []) if r.get("url") in ("1.1.1.1", "8.8.8.8")]
        if inet:
            avg = sum(f(r, "average_response_ms") for r in inet) / len(inet)
            loss = max(f(r, "percent_packet_loss") for r in inet)
            we = "🟢" if loss == 0 else ("🟠" if loss < 20 else "🔴")
            emb.add_field(name="🌍 Internet (WAN)",
                          value=f"{we} latence {avg:.0f} ms · perte {loss:.0f}%")
            if loss >= 20:
                color = fmt.RED
        sp = wan.get("speedtest") or {}
        if sp:
            emb.add_field(name="⚡ Débit fibre",
                          value=f"↓ {f(sp, 'down_mbps'):.0f} / ↑ {f(sp, 'up_mbps'):.0f} Mbit/s")
        # services publics (uptime synthétique)
        if vh:
            up, downs = 0, []
            for r in vh:
                code = int(f(r, "http_response_code"))
                (downs.append(r.get("site")) if not (0 < code < 500) else None)
                up += 1 if 0 < code < 500 else 0
            val = f"**{up}/{len(vh)}** en ligne"
            if downs:
                val += "\n🔴 DOWN : " + ", ".join(d for d in downs if d)
                color = fmt.RED
            emb.add_field(name="🔗 Services publics (HTTPS)", value=val, inline=False)
        emb.color = color
        emb.set_footer(text="routeur SNMP · WAN ping/speedtest · uptime synthétique · rafraîchi")
        return emb, pending

    async def _emb_services(self):
        """Santé niveau service (sondes TCP/HTTP) : Postgres/Vaultwarden/mail/*arr…"""
        pending = {}
        sh = await self.bot.influx.service_health()
        color = fmt.GREEN
        emb = discord.Embed(title="🩺 Santé des services", color=color)
        emb.timestamp = discord.utils.utcnow()

        def ms(d):
            try:
                return float(d.get("response_time", 0) or 0) * 1000
            except (TypeError, ValueError):
                return 0.0
        rows = []
        for r in (sh.get("net") or []):
            rows.append((r.get("service"), str(r.get("result_code")) in ("0", "0.0"), ms(r)))
        for r in (sh.get("http") or []):
            try:
                code = int(float(r.get("http_response_code", 0) or 0))
            except (TypeError, ValueError):
                code = 0
            ok = str(r.get("result_code")) in ("0", "0.0") and 0 < code < 500
            rows.append((r.get("service"), ok, ms(r)))
        rows.sort(key=lambda x: (x[1], x[0] or ""))
        body, down = [], 0
        for name, ok, m in rows:
            if not name:
                continue
            body.append(f"{'🟢' if ok else '🔴'} **{name}** · {m:.0f} ms")
            down += 0 if ok else 1
        if down:
            color = fmt.RED
        emb.description = (f"⚠️ **{down} service(s) KO**" if down
                           else "✅ Tous les services répondent")
        if body:
            emb.add_field(name="Services", value="\n".join(body)[:1024], inline=False)
        emb.color = color
        emb.set_footer(text="sondes TCP/HTTP toutes les 30 s · rafraîchi")
        return emb, pending

    # ------------------------------------------------------------------ loops

    @commands.Cog.listener()
    async def on_ready(self):
        if self._done_once or not self.enabled:
            return
        self._done_once = True
        try:
            await self.provision_all()
        except Exception:
            log.exception("provisioning initial échoué")
        if not self.reconcile.is_running():
            self.reconcile.change_interval(
                minutes=getattr(self.cfg, "provision_reconcile_min", 5))
            self.reconcile.start()
        if not self.refresh_topical.is_running():
            self.refresh_topical.change_interval(
                minutes=max(1, self.cfg.dashboard_interval_min))
            self.refresh_topical.start()

    @tasks.loop(minutes=5)
    async def reconcile(self):
        if not self.enabled:
            return
        try:
            await self.provision_all()
        except Exception:
            log.exception("réconciliation échouée")

    @reconcile.before_loop
    async def _before_rec(self):
        await self.bot.wait_until_ready()

    async def refresh_seedbox_embed(self):
        """Rafraîchit l'embed épinglé #seedbox tout de suite (appelé par /setratio
        côté cog Servarr, pour que #seedbox reste synchro avec #ratio sans attendre
        le cycle refresh_topical)."""
        cid = self.prov.get("super", {}).get("seedbox")
        if not cid:
            return
        emb, pending = await self._emb_seedbox()
        await self._pinned_edit(cid, emb, pending)

    @tasks.loop(minutes=2)
    async def refresh_topical(self):
        if not self.enabled or not self.bot.influx.enabled:
            return
        s = self.prov.get("super", {})
        try:
            if s.get("materiel"):
                emb, pending = await self._emb_materiel()
                await self._pinned_edit(s["materiel"], emb, pending)
            if s.get("sauvegardes"):
                emb, pending = await self._emb_sauvegardes()
                await self._pinned_edit(s["sauvegardes"], emb, pending)
            if s.get("stockage"):
                emb, pending = await self._emb_stockage()
                await self._pinned_edit(s["stockage"], emb, pending)
            if s.get("seedbox"):
                emb, pending = await self._emb_seedbox()
                await self._pinned_edit(s["seedbox"], emb, pending)
            if s.get("nas"):
                emb, pending = await self._emb_nas()
                await self._pinned_edit(s["nas"], emb, pending)
            if s.get("reseau"):
                emb, pending = await self._emb_reseau()
                await self._pinned_edit(s["reseau"], emb, pending)
            if s.get("services"):
                emb, pending = await self._emb_services()
                await self._pinned_edit(s["services"], emb, pending)
        except Exception:
            log.exception("rafraîchissement des salons thématiques échoué")

        # Salons du serveur SYNO — dans leur propre try : le NAS ne doit pas pouvoir
        # empêcher le rafraîchissement des salons R820 ci-dessus (ni l'inverse).
        if self._syno_enabled():
            sy = self.prov.get("syno", {})
            for key, builder in (("sante", self._emb_syno_sante),
                                 ("disques", self._emb_syno_disques),
                                 ("volumes", self._emb_syno_volumes),
                                 ("fiche", self._emb_syno_fiche)):
                if not sy.get(key):
                    continue
                try:
                    emb, pending = await builder()
                    await self._pinned_edit(sy[key], emb, pending)
                except Exception:
                    log.exception("rafraîchissement du salon NAS « %s » échoué", key)

    @refresh_topical.before_loop
    async def _before_topical(self):
        await self.bot.wait_until_ready()


class TopicalRefreshView(discord.ui.View):
    """Bouton Rafraîchir pour les embeds épinglés (matériel/sauvegardes/stockage/seedbox).
    Reconstruit le bon embed selon le salon où se trouve le message."""
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="prov:topical:refresh")
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        if not await admin_button_ok(itx):     # rôle Gestion + session 2FA (pas le rôle A)
            return
        await itx.response.defer()
        s = self.cog.prov.get("super", {})
        builders = {
            s.get("materiel"): self.cog._emb_materiel,
            s.get("sauvegardes"): self.cog._emb_sauvegardes,
            s.get("stockage"): self.cog._emb_stockage,
            s.get("seedbox"): self.cog._emb_seedbox,
            s.get("nas"): self.cog._emb_nas,
            s.get("reseau"): self.cog._emb_reseau,
            s.get("services"): self.cog._emb_services,
        }
        fn = builders.get(itx.channel_id)
        if fn is None:
            return
        try:
            emb, pending = await fn()
            await itx.message.edit(embed=emb, view=self)
        except discord.HTTPException:
            return
        # baselines de delta avancées seulement après une édition confirmée (#21)
        for k, v in (pending or {}).items():
            self.cog.bot.state.set(k, v)


async def setup(bot):
    await bot.add_cog(Provision(bot))
