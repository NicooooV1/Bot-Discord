"""Auto-provisioning des salons Discord (demande utilisateur 2026-07-09).

Le bot possède la permission « Gérer les salons » : il CRÉE et maintient lui-même la
structure du serveur au lieu de dépendre d'IDs collés à la main dans config.env.

Deux volets :
  1) Catégorie « 📊 Supervision » + salons dédiés (alertes, rapports, journaux-live,
     materiel, sauvegardes, stockage). Les rendus périodiques (matériel/sauvegardes/
     stockage) sont épinglés et édités sur place (pas de spam).
  2) Un salon par guest (LXC/VM), réconcilié dynamiquement contre l'API Proxmox :
       - nouveau guest        -> nouveau salon créé automatiquement,
       - guest disparu de PVE -> le salon RESTE EXACTEMENT où il est (l'archivage
         automatique a été retiré le 2026-07-18 à la demande de Nico ; ce docstring
         décrivait encore l'ancien comportement, corrigé 2026-08-11). Après trois
         cycles d'absence, seul le SUIVI est relâché (cf. _purge_ghosts) : le salon
         cesse d'être renommé et re-sondé, il n'est jamais déplacé ni supprimé,
       - ordre des salons      = ordre des VMID (l'ID donne la position).

Idempotent : les IDs résolus sont persistés dans bot.state["prov"]. Au redémarrage le
bot ré-adopte les salons existants (par ID persisté, puis par nom — avec ou sans son
emoji de statut) au lieu d'en recréer.
Après réconciliation, le routage temps réel est recâblé vers les salons résolus :
  - cfg.alert_channel_id / report_channel_id  (lus en direct par Alerts/Reports),
  - cfg.ct_channels                           (lu en direct par le routeur de logs),
  - CtChannels.map + Logs.stream.channel_id   (capturés à l'init -> mis à jour ici).

DÉCOUPAGE (2026-08-11) — ce fichier ne garde que l'ORCHESTRATION (réconciliation,
étapes 1..4b, _rewire, _save, les boucles). Deux modules frères, qui ne sont PAS des
cogs (rien à ajouter à COGS dans `__main__.py`), portent les parties quasi pures :
  - provision_perms.py  : guild + cfg -> dict d'overwrites (schéma des permissions) ;
  - provision_embeds.py : données InfluxDB -> discord.Embed (rendus épinglés).
provision reste le PROPRIÉTAIRE des catégories : il les crée avec leurs overwrites et
publie leurs ids dans state["prov"]["categories"] — c'est `core/channels.py` qui les
relit pour les autres cogs, jamais l'inverse.
"""
import asyncio
import logging
import time

import discord
from discord.ext import commands, tasks

from ..core import channels, format as fmt
from ..core.gates import GatedView
from ..core.ui import pin_edit
from . import provision_embeds as embeds
from . import provision_perms as perms

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
    ("dns",           "dns",           "DNS maison (AdGuard Home CT125) : requêtes bloquées, bilan horaire."),
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


class Provision(commands.Cog):
    # Cycles de `reconcile` (5 min) pendant lesquels un salon doit rester SANS invité
    # PVE avant d'être supprimé. 3 -> ~15 min : assez pour absorber une lecture PVE
    # partielle ou un nœud qui redémarre, assez court pour que le ménage suive.
    GHOST_DELETE_CYCLES = 3
    # …ET au moins ce délai depuis la PREMIÈRE fois qu'on l'a vu orphelin. Les deux
    # conditions, parce que « cycle » n'est pas une durée : au démarrage, `on_ready` et
    # la boucle `reconcile` déclenchent deux réconciliations dans la même seconde — on
    # a vu les compteurs passer de 1/3 à 2/3 sans qu'une minute s'écoule. Sans cette
    # borne de temps, trois redémarrages rapprochés suffiraient à supprimer un salon.
    GHOST_DELETE_MIN_S = 600

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
        # nas/ms01/llm), chacun avec « 📊 Supervision AVY-X » + « Gestion AVY-X » et les
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
            ("rapports",    "rapports",    "Rapport quotidien du nœud : charge, invités, stockages, sauvegardes, disques."),
            ("journaux",    "journaux",    "Journal des tâches Proxmox du nœud (Aveyron ne pousse pas ses logs vers Loki)."),
        ]
        # NAS Synology = serveur à part (demande Nico) : mêmes catégories et mêmes
        # 3 tiers que les autres, mais SANS catégorie « Gestion » — il n'a aucun
        # invité à piloter, la valeur est dans la supervision. La fiche sensible
        # (n° de série, SMART détaillé) va dans « 🔒 Lock SYNO », comme l'hyperviseur.
        self.syno_key = getattr(bot.cfg, "syno_key", "SYNO")
        self.CAT_SYNO_SUP = f"📊 Supervision {self.syno_key}"
        self.CAT_SYNO_LOCK = f"🔒 Lock {self.syno_key}"
        self.is_primary = getattr(bot.cfg, "is_primary", True)
        # Schémas d'overwrites du serveur principal, sous la forme `want_fn(guild)`
        # attendue par `_enforce_perms` (les constructeurs vivent dans provision_perms
        # et prennent `cfg` en argument explicite — ils ne dépendent plus du cog).
        # ⚠️ Ils renvoient None quand un rôle M/O manque : « ne touche à rien », JAMAIS
        # « aucun overwrite » (cf. l'en-tête de provision_perms).
        self._managed_fn = perms.managed_overwrites_fn(self.cfg)
        self._lock_fn = perms.node_lock_overwrites_fn(self.cfg)
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
        self.prov.setdefault("ghost_chan", {})   # {id salon: cycles sans invité}

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

    async def _ensure_category(self, guild, name, key, overwrites=None):
        """Adopte (par id persisté puis par nom) ou crée une catégorie. Renvoie l'objet.

        `overwrites` n'est utilisé QU'À LA CRÉATION, et uniquement par les catégories
        privées (Supervision/Gestion/Lock) : une catégorie créée sans
        overwrites naît PUBLIQUE et ses salons héritent de cette visibilité, le temps
        que `_enforce_perms` passe — ou pour toujours si les rôles ne se résolvent pas
        (correction 2026-08-11). Une catégorie ADOPTÉE n'est jamais touchée ici : elle
        appartient à l'utilisateur, et `edit(overwrites=)` REMPLACE l'ensemble."""
        cid = self.prov["categories"].get(key)
        cat = guild.get_channel(cid) if cid else None
        if isinstance(cat, discord.CategoryChannel):
            return cat
        cat = discord.utils.get(guild.categories, name=name)
        if cat is None:
            # fallback tolérant aux emojis/espaces : « Archive » adopte « 🗄️ Archive »
            # (et inversement) au lieu de créer un DOUBLON. Comparaison alphanumérique —
            # `channels.norm`, la MÊME que celle dont se servent les cogs satellites pour
            # retrouver ces catégories (quatre copies locales avant le 2026-08-11).
            target = channels.norm(name)
            cat = next((c for c in guild.categories
                        if channels.norm(c.name) == target), None)
        if cat is None:
            kw = {}
            if overwrites is not None:
                kw["overwrites"] = overwrites
            cat = await guild.create_category(name=name, reason="auto-provision bot", **kw)
            log.info("catégorie créée: %s", name)
        self.prov["categories"][key] = cat.id
        return cat

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

    async def _ensure_twofa_channel(self, guild):
        """Crée/adopte le JOURNAL d'audit 2FA (#logs-2fa) : rangé dans « 🗄️ Archive »,
        VISIBLE PAR PERSONNE (@everyone deny, seul le bot écrit + owner par bypass), et
        publie son id dans le state pour que le cog 2fa y journalise les événements."""
        name = getattr(self.cfg, "twofa_channel_name", "") or ""
        if not name:
            return
        want = perms.twofa_overwrites(guild)
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
            elif perms.ovw_drifted(ch, want):
                try:
                    await ch.edit(overwrites=want, reason="journal 2FA : permissions")
                    log.info("journal 2FA : permissions (ré)appliquées")
                except discord.HTTPException:
                    log.warning("journal 2FA : permissions non appliquées")

        self.prov["twofa_channel"] = ch.id
        # publié pour le cog 2fa : c'est le salon où JOURNALISER (pas où confiner /2fa)
        if self.bot.state.get("twofa_log_channel_id") != ch.id:
            self.bot.state.set("twofa_log_channel_id", ch.id)

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
                if perms.ovw_drifted(ch, want):
                    await ch.edit(overwrites=want, reason="permissions R820 (cahier des charges)")
                    log.info("permissions (ré)appliquées: %s", getattr(ch, "name", "?"))
            except discord.HTTPException:
                log.warning("permissions non appliquées: %s", getattr(ch, "name", "?"))

    # ------------------------------------------------- catégorie « 🔒 Lock » (nœud)
    # Le SCHÉMA d'overwrites vit dans provision_perms (`lock_overwrites`) ; ici, seule
    # l'orchestration : adopter/créer la catégorie et y ranger les salons sensibles.

    async def _ensure_lock_category(self, guild):
        """Adopte la catégorie « Lock » si elle existe, sinon la crée.

        ⚠️ Les overwrites ne sont posés QUE sur une catégorie que le bot vient de créer.
        Une catégorie pré-existante appartient à l'utilisateur — elle peut contenir ses
        propres salons (ici : back/ratio/help/demandes) avec ses propres règles — et
        `edit(overwrites=...)` REMPLACE l'ensemble : le bot effacerait silencieusement des
        accès qu'il n'a pas posés. La confidentialité qui compte est de toute façon portée
        par le salon du nœud lui-même, dont les overwrites, eux, sont maintenus.

        ⚠️ Les overwrites sont posés DÈS `create_category` (2026-08-11) : avant, la
        catégorie naissait publique puis était refermée par un `edit()` — fenêtre
        d'exposition, et exposition DÉFINITIVE quand `perms.lock_overwrites` renvoyait None
        (rôles M/O non résolus) puisque `edit(overwrites=None)` est un PATCH vide."""
        key_before = self.prov["categories"].get("lock")
        existed = key_before is not None or discord.utils.get(
            guild.categories, name=self.CAT_LOCK) is not None
        want = self._lock_fn(guild)
        cat = await self._ensure_category(
            guild, self.CAT_LOCK, "lock",
            overwrites=want if want is not None else perms.private_seed_overwrites(guild))
        if not existed and want is not None:
            try:
                await cat.edit(overwrites=want,
                               reason="catégorie Lock créée par le bot : propriétaire seul")
                log.info("catégorie Lock : permissions initiales appliquées")
            except discord.HTTPException:
                log.exception("application des permissions de la catégorie Lock")
        return cat

    async def _provision_node_channel(self, guild):
        """Salon du nœud PVE dans « 🔒 Lock » (dashboard live + console root)."""
        if not getattr(self.cfg, "node_channel_enabled", True):
            return
        # `perms.lock_overwrites` renvoie None par conception (garde anti-lockout). Le passer
        # tel quel à create_text_channel lève un TypeError — PAS une HTTPException — qui
        # remontait jusqu'au try de l'étape 4 et emportait AUSSI #jellyfin-logs et
        # `_enforce_perms` de toute la catégorie Lock (corrigé 2026-08-11).
        ow = self._lock_fn(guild)
        if ow is None:
            log.warning("rôles M/O non résolus — salon du nœud NON provisionné ce cycle")
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
                name=f"🟢-{fmt.slug(self.cfg.pve_node)}", category=cat, overwrites=ow,
                topic=f"Hyperviseur Proxmox « {self.cfg.pve_node} » — "
                      f"état live + console root. Propriétaire uniquement.",
                reason="auto-provision : salon de l'hyperviseur")
            log.info("salon du nœud créé: #%s", ch.name)
        else:
            if ch.category_id != cat.id:
                try:
                    await ch.edit(category=cat, sync_permissions=True,
                                  reason="auto-provision: rangement dans Lock")
                except discord.HTTPException:
                    log.warning("salon du nœud : rangement dans Lock impossible")
            if perms.lock_perms_drifted(ch, guild, self.cfg):
                try:
                    await ch.edit(overwrites=ow,
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
        # même piège que _provision_node_channel : overwrites=None => TypeError (2026-08-11)
        ow = self._lock_fn(guild)
        if ow is None:
            log.warning("rôles M/O non résolus — #jellyfin-logs NON provisionné ce cycle")
            return
        cat = await self._ensure_lock_category(guild)
        name = "jelly-logs"        # fusion Jellyfin+Jellyseerr (Nico 24/08) : UN salon
        cid = self.prov.get("jellyfin_logs")
        ch = guild.get_channel(cid) if cid else None
        if not isinstance(ch, discord.TextChannel):
            ch = discord.utils.get(cat.text_channels, name=name)
        if not isinstance(ch, discord.TextChannel):
            ch = await guild.create_text_channel(
                name=name, category=cat, overwrites=ow,
                topic="Journal d'activité Jellyfin (lectures, comptes, connexions) — "
                      "propriétaire uniquement.",
                reason="auto-provision : journal Jellyfin")
            log.info("salon jellyfin-logs créé: #%s", ch.name)
        else:
            if ch.name != name:
                try:
                    await ch.edit(name=name, reason="fusion des journaux médias")
                except discord.HTTPException:
                    log.warning("#%s : renommage impossible", name)
            if ch.category_id != cat.id:
                try:
                    await ch.edit(category=cat, sync_permissions=True,
                                  reason="auto-provision: rangement dans Lock")
                except discord.HTTPException:
                    log.warning("#jelly-logs : rangement dans Lock impossible")
            if perms.lock_perms_drifted(ch, guild, self.cfg):
                try:
                    await ch.edit(overwrites=ow,
                                  reason="salon jelly-logs : propriétaire uniquement")
                except discord.HTTPException:
                    log.exception("application des permissions de jelly-logs")
        self.prov["jellyfin_logs"] = ch.id
        return ch

    async def _ensure_text(self, guild, name, category, key_store, key, topic=None,
                           global_fallback=True, overwrites=None):
        """Adopte (id persisté -> nom dans la catégorie) ou crée un salon texte.

        global_fallback=False : n'adopte PAS un salon homonyme d'une autre catégorie
        (utilisé pour les salons de guests, dont le nom peut être générique).

        `overwrites` : posé DÈS la création (salons privés). Renvoie None si la création
        a échoué — les appelants qui se servent du salon doivent le tester."""
        cid = key_store.get(key)
        ch = guild.get_channel(cid) if cid else None
        if isinstance(ch, discord.TextChannel):
            if category and ch.category_id != category.id:
                try:
                    # sync_permissions : un salon d'invité MIGRÉ d'un serveur à l'autre
                    # gardait sinon les overwrites EXPLICITES de son ancienne catégorie
                    # jusqu'au prochain _enforce_perms — donc visible des rôles de
                    # l'ancien nœud, invisible de ceux du nouveau (corrigé 2026-08-11).
                    await ch.edit(category=category, sync_permissions=True,
                                  reason="auto-provision: rangement")
                except discord.HTTPException:
                    log.warning("salon #%s : rangement dans « %s » impossible",
                                getattr(ch, "name", "?"), getattr(category, "name", "?"))
            return ch
        # match par nom dans la catégorie cible (adoption d'un salon créé à la main)
        ch = None
        if category:
            ch = discord.utils.get(category.text_channels, name=name)
            if ch is None:
                # RÉ-adoption d'un salon que le bot a lui-même déjà renommé « 🟢-<nom> »
                # (CtChannels y colle l'emoji de statut) : sans ça, un state.json perdu
                # faisait recréer un salon EN DOUBLE pour chaque invité (2026-08-11).
                # Volontairement limité à la catégorie cible : sur le repli GLOBAL, des
                # noms génériques (« nas », « services ») adopteraient un salon d'invité.
                ch = next((c for c in category.text_channels
                           if fmt.strip_status_emoji(c.name) == name), None)
        if ch is None and global_fallback:
            ch = discord.utils.get(guild.text_channels, name=name)
        if ch is None:
            if category is not None and len(category.channels) >= 50:
                # limite Discord : 50 salons par catégorie. Sans ce garde-fou, l'échec
                # remontait en HTTPException opaque à chaque cycle (2026-08-11).
                log.error("catégorie « %s » pleine (50 salons) — salon #%s NON créé ; "
                          "déplacer des salons ou scinder la catégorie",
                          category.name, name)
                return None
            try:
                kw = {"overwrites": overwrites} if overwrites is not None else {}
                ch = await guild.create_text_channel(
                    name=name, category=category, topic=topic,
                    reason="auto-provision bot", **kw)
            except discord.HTTPException:
                # ne PAS laisser remonter : l'appelant est au milieu d'un cycle de
                # provisioning dont la fin (_rewire/_save) doit avoir lieu quand même.
                log.exception("création du salon #%s impossible", name)
                return None
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
            cont_cat = None
            try:
                # 1) Catégorie + salons de supervision
                #    Overwrites posés dès la CRÉATION : une catégorie créée nue est
                #    publique, et ses salons héritent de cette visibilité jusqu'au
                #    passage de _enforce_perms — voire pour toujours si les rôles ne se
                #    résolvent pas (2026-08-11).
                sup_want = self._managed_fn(guild)
                sup_cat = await self._ensure_category(
                    guild, self.CAT_SUPERVISION, "supervision",
                    overwrites=sup_want or perms.private_seed_overwrites(guild))
                for key, cname, topic in SUPER_CHANNELS:
                    await self._ensure_text(guild, cname, sup_cat, self.prov["super"],
                                            key, topic)
                # accès : @everyone rien, rôle A vision, rôle Gestion boutons (tous les salons)
                await self._enforce_perms(guild, sup_cat, self._managed_fn)

                # 1b) ressources PARTAGÉES (#général, #logs-2fa) : primaire uniquement — une
                #     instance secondaire n'y touche pas (elles sont communes à tout le guild).
                if self.is_primary:
                    await self._ensure_general_perms(guild)
                    await self._ensure_twofa_channel(guild)

                # 2) Catégorie conteneurs : réutiliser celle des salons CT existants si possible
                cont_cat = await self._resolve_containers_category(guild)
            except Exception:
                # étape isolée (2026-08-11) : un échec ici ne doit pas emporter la
                # réconciliation des salons d'invités NI le recâblage final.
                log.exception("provisioning de la supervision %s",
                              getattr(self.cfg, "server_key", "R820"))

            # 2b) serveurs Aveyron : Supervision + Gestion PAR NŒUD (AVY-NAS/MS01/LLM)
            avy_cats = {}          # clé serveur -> catégorie Gestion
            if getattr(self.bot.pve, "avy_enabled", False):
                # acall : sans AVY_NODES statique, avy_nodes() part interroger l'API du
                # cluster distant — un appel proxmoxer SYNCHRONE qui bloquerait toute la
                # boucle asyncio le temps du timeout WireGuard (2026-08-11).
                try:
                    avy_nodes = await self.bot.pve.acall(self.bot.pve.avy_nodes)
                except Exception:
                    log.exception("liste des nœuds Aveyron illisible ce cycle")
                    avy_nodes = []
                for node in avy_nodes:
                    key = self.bot.pve.avy_server_key(node)
                    # Même garde que _syno_enabled : sans les rôles G/M/O de la clé dans
                    # GESTION_SERVERS, perms.srv_overwrites_fn refuse de poser les overwrites
                    # et on créerait « 📊 Supervision / Gestion / 🔒 Lock AVY-X » +
                    # #hyperviseur-x PUBLICS, à vie (2026-08-11). Ne rien créer du tout
                    # est le seul comportement fail-closed. Déclencheur non théorique :
                    # avy_nodes() découvre dynamiquement les nœuds en ligne.
                    if key not in (getattr(self.cfg, "gestion_servers", {}) or {}):
                        log.warning("serveur %s absent de GESTION_SERVERS — salons NON "
                                    "créés (déclarer « %s:G:M:O » pour l'activer)",
                                    key, key)
                        continue
                    try:
                        ow_fn = perms.srv_overwrites_fn(self.cfg, key)
                        lock_fn = perms.lock_overwrites_fn(self.cfg, key)
                        seed = perms.private_seed_overwrites(guild)
                        sup = await self._ensure_category(
                            guild, f"📊 Supervision {key}", f"avy_sup_{node}",
                            overwrites=ow_fn(guild) or seed)
                        gest = await self._ensure_category(
                            guild, f"Gestion {key}", f"avy_gest_{node}",
                            overwrites=ow_fn(guild) or seed)
                        lock = await self._ensure_category(
                            guild, f"🔒 Lock {key}", f"avy_lock_{node}",
                            overwrites=lock_fn(guild) or seed)
                        await self._ensure_avy_sup_channels(guild, node, sup)
                        await self._ensure_avy_lock_hyperviseur(guild, node, lock)
                        avy_cats[key] = gest
                        await self._enforce_perms(guild, sup, ow_fn)
                        await self._enforce_perms(guild, gest, ow_fn)
                        await self._enforce_perms(guild, lock, lock_fn)
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
            #    Isolée elle aussi : c'est ici que se créent les salons, donc c'est ici
            #    que peut lever la limite Discord des 50 salons par catégorie.
            try:
                await self._reconcile_containers(guild, cont_cat, avy_cats)
                await self._enforce_perms(guild, cont_cat, self._managed_fn)
            except Exception:
                log.exception("réconciliation des salons d'invités")

            # 4) Catégorie « 🔒 Lock » + salon de l'hyperviseur (propriétaire uniquement).
            #    Isolé du reste : un échec ici (permissions Discord) ne doit pas empêcher
            #    le provisioning des salons d'invités, qui est la fonction principale.
            try:
                lock_cat = await self._ensure_lock_category(guild)
                await self._provision_node_channel(guild)
                await self._provision_jellyfin_log_channel(guild)
                # toute la catégorie Lock (salon nœud + salons persos) = Gestion+O R820
                await self._enforce_perms(guild, lock_cat, self._lock_fn)
            except Exception:
                log.exception("provisioning du salon du nœud")

            # 5) Recâblage du routage temps réel vers les salons résolus.
            #    ⚠️ Hors de tout `try` d'étape, et TOUJOURS exécuté : c'est _rewire() qui
            #    DÉMARRE les boucles CtChannels.refresh / NodeChannel.refresh /
            #    JellyfinActivity.poll. Une exception d'étape le sautait, et au premier
            #    cycle ces boucles ne démarraient jamais (2026-08-11). Il s'exécute donc
            #    sur un état éventuellement PARTIEL — acceptable : il ne fait que publier
            #    des ids déjà résolus.
            try:
                self._rewire()
            except Exception:
                log.exception("recâblage du routage temps réel")
            try:
                self._save()
            except Exception:
                log.exception("persistance de l'état de provisioning")

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
        # c) sinon créer une catégorie dédiée — overwrites dès la création (2026-08-11) :
        #    créée nue, elle est publique et les salons d'invités qu'on y range en
        #    héritent le temps que _enforce_perms passe (voire toujours si les rôles
        #    M/O ne se résolvent pas).
        return await self._ensure_category(
            guild, self.CAT_CONTAINERS, "containers",
            overwrites=(self._managed_fn(guild)
                        or perms.private_seed_overwrites(guild)))

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
                    # Discord limite à 2 renommages / 10 min / salon : re-tenté au cycle
                    # suivant. Journalisé quand même, sinon un refus permanent (403) est
                    # invisible en exploitation.
                    log.warning("salon #%s : renommage en « %s » refusé", ch.name, wanted)

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
                log.warning("salon #%s : renommage en « %s » refusé", ch.name, wanted)
        return ch

    def _syno_enabled(self):
        """Le NAS n'est un « serveur » que si ses rôles G/M/O existent dans
        GESTION_SERVERS. Sans eux, perms.srv_overwrites_fn refuserait de poser les
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
        # overwrites dès la création (2026-08-11) : une catégorie créée nue est publique
        # et ses salons héritent de cette visibilité (fiche NAS = n° de série, SMART).
        srv_fn = perms.srv_overwrites_fn(self.cfg, self.syno_key)
        lock_fn = perms.lock_overwrites_fn(self.cfg, self.syno_key)
        seed = perms.private_seed_overwrites(guild)
        sup = await self._ensure_category(guild, self.CAT_SYNO_SUP, "syno_sup",
                                          overwrites=srv_fn(guild) or seed)
        lock = await self._ensure_category(guild, self.CAT_SYNO_LOCK, "syno_lock",
                                           overwrites=lock_fn(guild) or seed)
        for key, cname, topic in SYNO_SUP_CHANNELS:
            wanted = f"{cname}-nas"
            ch = await self._ensure_text(guild, wanted, sup, store, key, topic,
                                         global_fallback=False)
            if ch is not None and ch.name != wanted:
                try:
                    await ch.edit(name=wanted, reason="supervision NAS : nom suffixé")
                except discord.HTTPException:
                    log.warning("salon #%s : renommage en « %s » refusé", ch.name, wanted)
        await self._ensure_text(
            guild, "synology", lock, store, "fiche",
            topic="NAS Synology — fiche détaillée (série, DSM, SMART). M/O uniquement.",
            global_fallback=False)
        await self._enforce_perms(guild, sup, srv_fn)
        await self._enforce_perms(guild, lock, lock_fn)

    async def _reconcile_containers(self, guild, cont_cat, avy_cats=None):
        """Crée les salons des VM/conteneurs présents, ordonne par VMID — chacun dans
        la catégorie Gestion de SON serveur (R820, ou AVY-<nœud> pour les -avy ; une
        VM/un conteneur migré change de catégorie).

        Un guest disparu de PVE garde son salon là où il est (aucun archivage
        automatique, retiré à la demande de Nico 2026-07-18 : un salon ne doit pas
        bouger juste parce que la VM/le conteneur est éteint ou temporairement
        injoignable)."""
        if cont_cat is None:
            log.warning("catégorie « %s » non résolue — réconciliation reportée "
                        "(ne PAS créer les salons d'invités hors catégorie)",
                        self.CAT_CONTAINERS)
            return
        # seed initial du mapping name->channel depuis la config (adoption)
        if not self.prov["ct"] and self.cfg.ct_channels:
            self.prov["ct"] = dict(self.cfg.ct_channels)

        guests = await self._guests()   # {name: (vmid, clé serveur|None)} (None si PVE indispo)
        if guests is None:
            log.info("PVE indisponible — salons CT conservés tels quels, pas de réconciliation")
            return
        self._purge_ghosts(guests)

        def _gcat(name):
            key = guests[name][1]
            if key is not None:
                # Serveur Aveyron dont la catégorie « Gestion <clé> » n'a PAS été
                # provisionnée ce cycle (clé absente de GESTION_SERVERS -> `continue` de
                # l'étape 2b, ou étape 2b en échec). NE PAS se rabattre sur `cont_cat` :
                # ce serait remettre exactement le bug que _guests() documente — des
                # salons « -avy » atterrissant dans « Gestion R820 », donc visibles et
                # actionnables par les rôles du R820. Renvoyer None = « on ne conclut
                # rien ce cycle », le salon reste où il est (2026-08-11).
                return (avy_cats or {}).get(key)
            return cont_cat

        # a) guests présents -> création (ou recalage de catégorie) ; les guests qui ne
        #    sont plus dans `guests` sont simplement ignorés ici, leur salon ne bouge pas
        for name in guests:
            cat = _gcat(name)
            if cat is None:
                log.debug("invité %s : catégorie de son serveur non provisionnée — "
                          "salon inchangé ce cycle", name)
                continue
            if name in self.prov["ct"]:
                await self._ensure_text(guild, fmt.slug(name), cat,
                                        self.prov["ct"], name,
                                        topic=f"VM/conteneur Proxmox « {name} ».",
                                        global_fallback=False)
                continue
            if name in self.prov["archived"]:            # ancien salon archivé -> réactiver
                cid = self.prov["archived"].pop(name)
                ch = guild.get_channel(cid)
                if isinstance(ch, discord.TextChannel):
                    try:
                        await ch.edit(category=cat, sync_permissions=True,
                                      reason=f"guest {name} recréé")
                    except discord.HTTPException:
                        log.warning("salon #%s : sortie d'Archive impossible", ch.name)
                    self.prov["ct"][name] = cid
                    continue
            await self._ensure_text(guild, fmt.slug(name), cat,
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
            # l'ordre n'est mémorisé QUE s'il a réellement été appliqué (2026-08-11) :
            # sinon un seul move() en échec figeait le tri pour de bon, la comparaison
            # `prim == prov["order"]` court-circuitant toutes les tentatives suivantes.
            if await self._apply_order(guild, prim, cont_cat):
                self.prov["order"] = prim
        for key, cat in (avy_cats or {}).items():
            lst = sorted(groups.get(key, []), key=lambda n: guests[n][0])
            if lst != self.prov.get(f"order_{key}"):
                if await self._apply_order(guild, lst, cat):
                    self.prov[f"order_{key}"] = lst

        # d) ménage : les salons des invités qui n'existent plus sont SUPPRIMÉS
        await self._sweep_ghost_channels(guild, guests, cont_cat, avy_cats)

    def _purge_ghosts(self, guests):
        """Retire de prov['ct'] les invités DISPARUS de PVE (supprimés ou renommés).

        Sans ça (depuis le retrait de l'archivage automatique 2026-07-18), `_rewire`
        réinjecte l'entrée fantôme dans `CtChannels.map` à chaque cycle : le salon est
        renommé « 🔴-<ancien-nom> » puis re-sondé à vie (3-4 requêtes Flux / 2 min, ou un
        fetch_channel en 404 si le salon a été supprimé à la main), et le `map.pop()`
        défensif de ct_channels ne survit jamais au cycle suivant. Corrigé 2026-08-11.

        Le salon Discord lui-même est supprimé par `_sweep_ghost_channels`, qui tourne
        dans le même cycle avec les mêmes garde-fous (Nico 2026-08-18 : « quand une VM
        disparaît, on la supprime »). Ici on ne fait que cesser de le SONDER.

        ⚠️ DEUX garde-fous, parce qu'une absence n'est pas une disparition :
          - hystérésis de 3 cycles : une réponse PVE partielle ne purge rien ;
          - les invités « -avy » sont épargnés tant qu'AUCUN invité Aveyron n'est vu —
            `Pve._avy_resources()` renvoie [] sur coupure WireGuard prolongée, ce qui
            ferait sinon décrocher d'un coup TOUS les salons d'Aveyron.
        """
        misses = self.prov.setdefault("ct_missing", {})
        avy_seen = any(key is not None for _, key in guests.values())
        for name in list(self.prov["ct"]):
            if name in guests:
                misses.pop(name, None)
                continue
            if self.bot.pve.is_avy_name(name) and not avy_seen:
                continue           # Aveyron injoignable : on ne conclut rien
            n = misses.get(name, 0) + 1
            if n < 3:
                misses[name] = n
                continue
            misses.pop(name, None)
            cid = self.prov["ct"].pop(name, None)
            log.info("invité %s absent de PVE depuis 3 cycles — salon %s détaché du "
                     "suivi (suppression par _sweep_ghost_channels)", name, cid)
        # entrées de compteur devenues sans objet (invité déjà purgé/renommé)
        for name in [n for n in misses if n not in self.prov["ct"]]:
            misses.pop(name, None)

    @staticmethod
    def _salon_est_un_invite(nom, cid, suivis):
        """Un salon est CANDIDAT au ménage s'il a la forme d'un salon d'invité.

        Deux formes acceptées, et seulement elles : le nom préfixé d'un emoji de statut
        (c'est `ct_channels._sync_channel_emoji` qui les pose, donc c'est bien le bot
        qui l'a nommé), ou un id déjà présent dans le mapping `prov['ct']`. Un salon
        rangé à la main dans « Gestion … » n'a ni l'un ni l'autre : il ne sera JAMAIS
        supprimé, quoi qu'il arrive côté PVE."""
        return cid in suivis or nom.startswith(fmt.STATUS_EMOJI)

    async def _sweep_ghost_channels(self, guild, guests, cont_cat, avy_cats):
        """SUPPRIME les salons dont la VM/le conteneur n'existe plus côté PVE.

        Demande explicite de Nico (2026-08-18) : « quand une VM disparaît, on la
        supprime ». Jusque-là `_purge_ghosts` se contentait de cesser de les sonder, et
        16 salons morts s'étaient accumulés (6 « fronote-* » de tests, les invités de
        l'ancien nœud Aveyron `pve`, un « clipper-gpu-avy »…).

        ⚠️ Opération DESTRUCTRICE et irréversible côté Discord (l'historique part avec
        le salon). Quatre garde-fous, un par panne déjà observée :
          1. `guests` vide -> on ne conclut RIEN (une lecture PVE en échec fait paraître
             tout le monde disparu) ;
          2. par catégorie, il faut avoir vu au moins un invité VIVANT du serveur
             correspondant : un nœud Aveyron injoignable ne peut pas vider sa catégorie ;
          3. seuls les salons en forme d'invité sont candidats (_salon_est_un_invite) ;
          4. hystérésis DOUBLE : GHOST_DELETE_CYCLES constats consécutifs ET
             GHOST_DELETE_MIN_S secondes écoulées depuis le premier — un compteur remis
             à zéro dès que l'invité reparaît.
        Chaque suppression est auditée (donc reprise dans #journaux-live)."""
        if not guests:
            return
        vivants = {fmt.slug(n) for n in guests}
        # clés serveur RÉELLEMENT vues ce cycle (None = R820) : une catégorie dont le
        # serveur n'a aucun invité vivant est intégralement épargnée.
        vues = {key for _, key in guests.values()}
        suivis = set(self.prov["ct"].values()) | set(self.prov["archived"].values())
        compteurs = self.prov.setdefault("ghost_chan", {})

        cibles = [(cont_cat, None)] + [(cat, key) for key, cat in (avy_cats or {}).items()]
        vus_ce_cycle = set()
        for cat, key in cibles:
            if cat is None or key not in vues:
                continue
            for ch in list(getattr(cat, "text_channels", [])):
                if not self._salon_est_un_invite(ch.name, ch.id, suivis):
                    continue
                if fmt.strip_status_emoji(ch.name) in vivants:
                    compteurs.pop(str(ch.id), None)
                    continue
                vus_ce_cycle.add(str(ch.id))
                vu = compteurs.get(str(ch.id))
                # tolère l'ancien format (un entier nu) laissé par une version d'avant
                n, depuis = (vu, time.time()) if isinstance(vu, (int, float)) \
                    else (vu[0], vu[1]) if vu else (0, time.time())
                n += 1
                compteurs[str(ch.id)] = [n, depuis]
                assez_vieux = (time.time() - depuis) >= self.GHOST_DELETE_MIN_S
                if n < self.GHOST_DELETE_CYCLES or not assez_vieux:
                    log.info("salon #%s sans invité PVE (%d/%d, %.0f s) — suppression "
                             "si ça dure", ch.name, n, self.GHOST_DELETE_CYCLES,
                             time.time() - depuis)
                    continue
                try:
                    await ch.delete(reason="invité supprimé côté Proxmox "
                                           "(absent depuis %d cycles)" % n)
                except discord.HTTPException as e:
                    log.warning("suppression du salon #%s impossible (%s)", ch.name, e)
                    continue
                compteurs.pop(str(ch.id), None)
                vus_ce_cycle.discard(str(ch.id))
                for m in (self.prov["ct"], self.prov["archived"]):
                    for nom in [k for k, v in m.items() if v == ch.id]:
                        m.pop(nom, None)
                log.warning("salon #%s SUPPRIMÉ : plus aucun invité PVE de ce nom", ch.name)
                self.bot.audit.record(user="system", action="salon-supprime",
                                      target=ch.name, result="invité disparu de PVE")
        # compteurs devenus sans objet (salon supprimé à la main, invité revenu)
        for cid in [c for c in compteurs if c not in vus_ce_cycle]:
            compteurs.pop(cid, None)

    async def _apply_order(self, guild, order, cont_cat):
        """Trie les salons d'une catégorie par VMID. Renvoie True si TOUS les
        déplacements ont réussi — l'appelant ne mémorise l'ordre que dans ce cas."""
        ok = True
        # move() gère les positions INTRA-catégorie (edit(position=) est global au guild)
        for i, name in enumerate(order):
            ch = guild.get_channel(self.prov["ct"][name])
            if isinstance(ch, discord.TextChannel):
                try:
                    # sync_permissions UNIQUEMENT si le salon change réellement de
                    # catégorie : dans ce cas il doit hériter des overwrites de la
                    # nouvelle (sinon fuite de visibilité jusqu'au prochain
                    # _enforce_perms). Le poser sur TOUS les salons du tri écraserait à
                    # chaque réordonnancement leurs overwrites par ceux — potentiellement
                    # vides — du CACHE de la catégorie ; quand perms.managed_overwrites renvoie
                    # None (rôles M/O non résolus), _enforce_perms ne repasse pas derrière
                    # et le salon resterait ouvert. (2026-08-11)
                    moving = ch.category_id != getattr(cont_cat, "id", None)
                    await ch.move(category=cont_cat, beginning=True, offset=i,
                                  sync_permissions=moving, reason="tri par VMID")
                except discord.HTTPException:
                    log.warning("tri : salon #%s non déplacé dans « %s »",
                                ch.name, getattr(cont_cat, "name", "?"))
                    ok = False
        return ok

    async def _guests(self):
        """{name: (vmid, clé serveur)} pour tous les LXC/VM, ou None si PVE indisponible.
        Clé serveur = None pour le R820, « AVY-<NŒUD> » pour un invité Aveyron."""
        if not self.bot.pve.enabled:
            return None
        try:
            # enveloppe async obligatoire : proxmoxer est SYNCHRONE (2026-08-11)
            res = await self.bot.pve.aresources()
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
        if s.get("dns"):
            self.cfg.dns_channel_id = s["dns"]
            dnscog = self.bot.get_cog("Dns")
            if dnscog is not None:
                dnscog.channel_id = s["dns"]
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
        # journal des demandes Jellyseerr : même mécanique que JellyfinActivity
        seerrcog = self.bot.get_cog("SeerrActivity")
        if seerrcog is not None and seerrcog.enabled and self.prov.get("jellyfin_logs"):
            seerrcog.channel_id = self.prov["jellyfin_logs"]
            if not seerrcog.poll.is_running():
                seerrcog.poll.start()

    # ------------------------------------ rendus thématiques (message épinglé)
    # Les embeds eux-mêmes sont dans provision_embeds ; ici, seule la publication.

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
            except discord.NotFound:
                # salon supprimé à la main : le signaler, sinon l'embed disparaît en
                # silence et personne ne comprend pourquoi (2026-08-11).
                log.warning("salon %s introuvable — embed épinglé non rafraîchi", channel_id)
                return
            except Exception:  # noqa: BLE001
                log.warning("salon %s injoignable ce cycle — embed non rafraîchi",
                            channel_id)
                return
        # ⚠️ Le STOCKAGE de l'id reste ICI (prov["pins"][str(salon)]) : seule la danse
        # Discord (fetch / NotFound / send / pin / edit) est déléguée à ui.pin_edit.
        # Migrer le stockage orphelinerait les messages déjà épinglés et en ferait
        # poster des DOUBLONS (bug vécu le 2026-07-17 sur 12 salons -avy).
        pins = self.prov.setdefault("pins", {})
        _, mid = await pin_edit(ch, embed, message_id=pins.get(str(channel_id)),
                                view=TopicalRefreshView(self),
                                label=f"#{getattr(ch, 'name', '?')}", log=log)
        if mid is None:
            return
        if pins.get(str(channel_id)) != mid:
            pins[str(channel_id)] = mid
            self._save()
        # baselines de delta avancées seulement après une écriture confirmée (#21)
        for k, v in (pending or {}).items():
            self.bot.state.set(k, v)

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
        try:
            emb, pending = await embeds.emb_seedbox(self.bot)
        except Exception:
            # appelé depuis /setratio (cog Servarr) : une erreur d'embed ne doit pas
            # faire échouer la commande de l'utilisateur (2026-08-11).
            log.exception("rafraîchissement immédiat de #seedbox échoué")
            return
        await self._pinned_edit(cid, emb, pending)

    @tasks.loop(minutes=2)
    async def refresh_topical(self):
        if not self.enabled or not self.bot.influx.enabled:
            return
        s = self.prov.get("super", {})
        sy = self.prov.get("syno", {}) if self._syno_enabled() else {}
        syno_todo = [(k, b) for k, b in embeds.SYNO_BUILDERS.items() if sy.get(k)]
        # UN SEUL relevé SNMP par cycle (2026-08-11) : nas_health() enchaîne 4 requêtes
        # Flux sans aucun cache et il était appelé par les 5 embeds du NAS — 20 requêtes
        # identiques toutes les 2 min. Bonus : les 5 embeds décrivent le même instantané.
        nas_h = None
        if s.get("nas") or syno_todo:
            try:
                nas_h = await embeds.nas_health(self.bot)
            except Exception:
                log.exception("relevé SNMP du NAS illisible ce cycle")

        # un try PAR salon (2026-08-11) : les 7 salons partageaient un seul try, donc la
        # première exception sautait tous les suivants — #reseau et #services muets à
        # cause d'une donnée SNMP du NAS. Le bloc SYNO ci-dessous faisait déjà l'inverse.
        for key, builder in embeds.BUILDERS.items():
            if not s.get(key):
                continue
            try:
                # tous les constructeurs ont la même signature (bot, h) : ceux qui
                # n'ont pas besoin du relevé SNMP l'ignorent.
                emb, pending = await builder(self.bot, nas_h)
                await self._pinned_edit(s[key], emb, pending)
            except Exception:
                log.exception("rafraîchissement du salon « %s » échoué", key)

        # Salons du serveur SYNO — dans leur propre try : le NAS ne doit pas pouvoir
        # empêcher le rafraîchissement des salons R820 ci-dessus (ni l'inverse).
        for key, builder in syno_todo:
            try:
                emb, pending = await builder(self.bot, nas_h)
                await self._pinned_edit(sy[key], emb, pending)
            except Exception:
                log.exception("rafraîchissement du salon NAS « %s » échoué", key)

    @refresh_topical.before_loop
    async def _before_topical(self):
        await self.bot.wait_until_ready()


class TopicalRefreshView(GatedView):
    """Bouton Rafraîchir des embeds épinglés (R820 : matériel/sauvegardes/stockage/
    seedbox/nas/reseau/services — et NAS : sante/disques/volumes/fiche).
    Reconstruit le bon embed selon le salon où se trouve le message.

    Porte « mod » = rôle Gestion + session 2FA (pas le rôle A, qui voit sans agir),
    BORNÉE PAR SERVEUR : dans les 4 salons du NAS, ce sont les rôles M/O SYNO qui
    ouvrent le bouton, pas ceux du R820 (cf. resolve_server)."""

    gate = "mod"

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    def _syno_ids(self):
        return {cid for cid in (self.cog.prov.get("syno", {}) or {}).values() if cid}

    def _builders(self):
        """{id de salon: constructeur d'embed}. Les 4 salons du NAS y étaient ABSENTS
        (2026-08-11) alors que `_pinned_edit` attache cette vue à TOUS les embeds
        épinglés : le bouton y acquittait sans rien faire ni rien dire. Les deux tables
        de provision_embeds ferment la classe : un salon ajouté là-bas est rafraîchi
        par la boucle ET par le bouton, sans liste à tenir à jour ici."""
        s = self.cog.prov.get("super", {}) or {}
        sy = self.cog.prov.get("syno", {}) or {}
        out = {s.get(k): fn for k, fn in embeds.BUILDERS.items()}
        out.update({sy.get(k): fn for k, fn in embeds.SYNO_BUILDERS.items()})
        out.pop(None, None)          # salon non provisionné -> clé None parasite
        return out

    async def resolve_server(self, interaction):
        """Les salons du NAS sont gardés par les rôles M/O SYNO (ils sont provisionnés
        avec perms.srv_overwrites_fn(cfg, syno_key)) ; ailleurs, tier R820 comme avant."""
        if interaction.channel_id in self._syno_ids():
            return self.cog.syno_key
        return None

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="prov:topical:refresh")
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        fn = self._builders().get(itx.channel_id)
        if fn is None:
            # ne JAMAIS acquitter en silence : un clic sans réponse ressemble à une panne
            await itx.response.send_message(
                "Aucun embed rafraîchissable n'est rattaché à ce salon.", ephemeral=True)
            return
        await itx.response.defer()
        try:
            # h=None : le bouton relève lui-même le NAS (pas de cycle à partager)
            emb, pending = await fn(self.cog.bot)
        except Exception:
            log.exception("bouton Rafraîchir : construction de l'embed échouée (salon %s)",
                          itx.channel_id)
            await itx.followup.send("❌ Rafraîchissement impossible — voir les logs.",
                                    ephemeral=True)
            return
        try:
            await itx.message.edit(embed=emb, view=self)
        except discord.HTTPException:
            log.warning("bouton Rafraîchir : édition du message %s refusée", itx.message.id)
            # 2026-08-20 : après defer(), une édition refusée était un silence total —
            # le clic SEMBLAIT avoir marché alors que l'embed n'a pas bougé. Le dire.
            try:
                await itx.followup.send(
                    "❌ Rafraîchissement impossible (édition refusée).", ephemeral=True)
            except discord.HTTPException:
                pass
            return
        # baselines de delta avancées seulement après une édition confirmée (#21)
        for k, v in (pending or {}).items():
            self.cog.bot.state.set(k, v)


async def setup(bot):
    await bot.add_cog(Provision(bot))
