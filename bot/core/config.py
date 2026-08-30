"""Configuration loaded from the environment (systemd EnvironmentFile=config.env)."""
import logging
import os

_log = logging.getLogger("discord-bot.config")


def _csv_ints(v):
    return [int(x) for x in (v or "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()]


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _float(v, default=0.0):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def _bool(v, default=False):
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _verify_ssl(v, default=False):
    """Valeur `verify_ssl` transmise à proxmoxer -> requests.Session.verify : un booléen,
    OU le CHEMIN d'un bundle CA (accepté tel quel s'il existe) pour authentifier
    l'hyperviseur sans dépendre du magasin du CT (2026-08-11).

    ⚠️ Le défaut reste False à dessein : le certificat de PVE est auto-signé et ne porte
    pas l'IP en SAN. Passer à true SANS avoir d'abord régénéré/étendu le certificat
    (`pvenode cert`) ou pointé PVE_HOST sur un nom couvert ferait échouer TOUS les appels
    (hostname mismatch) — c'est-à-dire couper le bot.

    ⚠️ PIÈGE (relecture 2026-08-11) : cette valeur n'est PAS lue que par proxmoxer.
    cogs/terminal.py construit son propre contexte SSL avec `if not cfg.pve_verify_ssl:
    CERT_NONE` — un CHEMIN est vrai, donc la console termproxy repasserait au magasin CA
    du système, qui ne connaît pas le CA de PVE : plus de shell root. Avant de renseigner
    un chemin ici, terminal.py doit faire
    `ssl.create_default_context(cafile=cfg.pve_verify_ssl if isinstance(cfg.pve_verify_ssl, str) else None)`."""
    s = str(v or "").strip()
    if s.startswith("/"):
        if os.path.exists(s):
            return s
        # sinon on retomberait sur False EN SILENCE (faute de frappe dans le chemin =
        # TLS non vérifié sans que rien ne le dise) — 2026-08-11
        _log.error("verify_ssl=%r : bundle CA INTROUVABLE -> TLS NON vérifié", s)
        return default
    return _bool(v, default)


class Config:
    def __init__(self, env=None):
        e = env if env is not None else os.environ
        g = lambda k, d="": (e.get(k) if e.get(k) not in (None, "") else d)

        # --- Discord ---
        self.discord_token = g("DISCORD_TOKEN").strip()
        self.guild_id = _int(g("GUILD_ID"))
        self.admin_ids = _csv_ints(g("ADMIN_IDS"))
        # role-based access: members holding one of these roles are admins / readers
        self.admin_role_ids = _csv_ints(g("ADMIN_ROLE_IDS"))
        self.read_role_ids = _csv_ints(g("READ_ROLE_IDS"))
        # --- Groupes de gestion par SERVEUR (extensible : R820, puis d'autres) ---
        # 3 TIERS par serveur : « clé:G:M:O[,clé2:...] »
        #   G = gestion  -> VISUALISER les salons du serveur (pas de boutons)
        #   M = modération -> tout faire sur ce serveur (boutons + catégorie Lock)
        #   O = owner     -> aucune restriction (sauf 2FA) + délégation /gestion
        # Voir cog gestion.py + core/permissions (is_admin/may_lock/can_delegate).
        self.gestion_servers = self._parse_gestion_servers(g("GESTION_SERVERS"))
        # --- Multi-serveurs (déploiement 1 bot/serveur physique, guild Discord partagé) ---
        # SERVER_KEY = clé de CE serveur (R820, AVEYRON…) : les catégories du bot en dérivent
        # (📊 Supervision <SRV> / Gestion <SRV> / 🔒 Lock <SRV>). IS_PRIMARY : le primaire gère
        # les ressources PARTAGÉES (#logs-2fa, #général, /gestion, /2fa, rôle 2FA Complet) ;
        # une instance secondaire (false) ne gère QUE son serveur.
        self.server_key = g("SERVER_KEY", "R820").strip()
        self.is_primary = _bool(g("IS_PRIMARY"), True)
        # Serveur dont dépend le salon du NŒUD (catégorie Lock, boutons du salon
        # hyperviseur dont 💾 vzdump de TOUS les invités, portail #demandes, shell root SSH).
        # ⚠️ Le repli était `next(iter(GESTION_SERVERS))` : RÉORDONNER la variable
        # d'environnement (ou y ajouter une entrée en tête) transférait silencieusement ces
        # trois pouvoirs aux rôles M/O d'un AUTRE serveur. Repli désormais sur SERVER_KEY
        # (le serveur de CETTE instance, « R820 » par défaut), et clé inconnue = AUCUN rôle
        # ne l'obtient — propriétaire du guild / ADMIN_IDS seuls (2026-08-11).
        self.node_server_key = g("NODE_SERVER_KEY", "").strip() or self.server_key
        if self.node_server_key not in self.gestion_servers:
            _log.error("NODE_SERVER_KEY=%r absent de GESTION_SERVERS -> catégorie Lock, "
                       "boutons du salon-nœud et console du nœud SANS rôle "
                       "(propriétaire/ADMIN_IDS seuls)", self.node_server_key)
        _ns = self.gestion_servers.get(self.node_server_key, {})
        self.node_view_role_id = _ns.get("view", 0)     # G — visualiser
        self.node_mod_role_id = _ns.get("mod", 0)       # M — actions + Lock
        self.node_owner_role_id = _ns.get("owner", 0)   # O — owner
        # rétrocompat helpers (may_lock lit mod+owner ; l'ancien node_o_role_id = owner)
        self.node_o_role_id = self.node_owner_role_id
        # rôles « visionneurs » MANUELS (ex. « A ») : provision leur donne la VUE des salons
        # de gestion (comme le tier G), MAIS le bot ne gère JAMAIS leur ATTRIBUTION (pas de
        # reconcile, pas de retrait) — c'est le rôle de Nico, à la main.
        self.viewer_role_ids = _csv_ints(g("VIEWER_ROLE_IDS"))
        # rôle purement INDICATIF « 2FA Complet » : attribué à tout inscrit 2FA (réconcilié).
        self.twofa_role_id = _int(g("TWOFA_ROLE_ID"))
        # salon « général » : on y REMET le rôle A en vue+écriture SANS commandes slash
        # (les commandes 2FA ont désormais leur salon dédié). 0 = pas de gestion.
        self.general_channel_id = _int(g("GENERAL_CHANNEL_ID"))
        # salon dédié aux commandes /2fa (inscription + statut). Le bot le crée/adopte par
        # NOM et y autorise le rôle A à lancer les commandes ; /2fa n'est utilisable QUE là.
        self.twofa_channel_name = g("TWOFA_CHANNEL_NAME", "logs-2fa").lstrip("#").strip()
        self.admin_channel_id = _int(g("ADMIN_CHANNEL_ID"))
        self.read_channel_ids = _csv_ints(g("READ_CHANNEL_IDS"))
        self.alert_channel_id = _int(g("ALERT_CHANNEL_ID"))
        self.report_channel_id = _int(g("REPORT_CHANNEL_ID"))
        self.live_log_channel_id = _int(g("LIVE_LOG_CHANNEL_ID"))
        # per-CT live channels: "ctname:channelid,ctname:channelid,..."
        self.ct_channels = self._parse_ct_channels(g("CT_CHANNELS"))
        # auto-provisioning des salons (catégories + supervision + 1 salon/guest)
        self.auto_provision = _bool(g("AUTO_PROVISION"), True)
        self.provision_reconcile_min = max(1, _int(g("PROVISION_RECONCILE_MIN", "5"), 5))

        # --- InfluxDB v2 ---
        self.influx_url = g("INFLUX_URL", "http://10.3.10.120:8086")
        self.influx_org = g("INFLUX_ORG", "Home")
        self.influx_bucket = g("INFLUX_BUCKET", "Proxmox")
        self.influx_token = g("INFLUX_TOKEN").strip()
        # bucket DÉDIÉ Aveyron (2026-07-18, même instance/org CT103, jeton SCOPÉ à ce
        # seul bucket) : le cluster Aveyron n'a pas de telegraf (pas d'accès infra) —
        # le bot collecte via l'API PVE déjà utilisée par avy.py et écrit lui-même les
        # points, pour que Grafana affiche Aveyron comme le R820 sans rien installer
        # là-bas. Vide/absent = collecteur désactivé (avy_metrics.py).
        self.avy_influx_bucket = g("AVY_INFLUX_BUCKET", "Aveyron")
        self.avy_influx_token = g("AVY_INFLUX_TOKEN").strip()
        # Jellyfin API (pour le salon #jellyfin : titre en cours de lecture)
        self.jellyfin_url = g("JELLYFIN_URL", "").rstrip("/")
        self.jellyfin_api_key = g("JELLYFIN_API_KEY").strip()
        # Journal d'activité Jellyfin (lecture démarrée, compte créé, connexion…) dans
        # la catégorie 🔒 Lock — propriétaire uniquement (demande Nico 2026-07-18).
        self.jellyfin_logs_enabled = _bool(g("JELLYFIN_LOGS_ENABLED"), True)
        # Journal Dolibarr (CT108) dans #doli-logs (🔒 Lock) via Loki {job="dolibarr"}
        # (demande Nico 2026-08-29). Inactif si LOKI_URL est vide.
        self.dolibarr_logs_enabled = _bool(g("DOLIBARR_LOGS_ENABLED"), True)
        self.dolibarr_logs_channel_id = _int(g("DOLIBARR_LOGS_CHANNEL_ID"))  # repli si non provisionné
        self.dolibarr_logs_poll_seconds = max(10, _int(g("DOLIBARR_LOGS_POLL_SECONDS", "30"), 30))
        # Journaux Pterodactyl (panel CT101 via Alloy + Wings CT100 via journald) dans #ptero-logs
        # (🔒 Lock) — demande Nico 2026-08-29. Inactif si LOKI_URL est vide.
        self.pterodactyl_logs_enabled = _bool(g("PTERODACTYL_LOGS_ENABLED"), True)
        self.pterodactyl_logs_channel_id = _int(g("PTERODACTYL_LOGS_CHANNEL_ID"))  # repli si non provisionné
        self.pterodactyl_logs_poll_seconds = max(10, _int(g("PTERODACTYL_LOGS_POLL_SECONDS", "30"), 30))
        # VPN WireGuard : tableau #vpn (R820 wg-vpn/wg-avy + MikroTik wg0 en secours), demande Nico 2026-08-30
        self.vpn_enabled = _bool(g("VPN_ENABLED"), True)
        self.vpn_channel_id = _int(g("VPN_CHANNEL_ID"))  # repli si le salon n'est pas retrouvé par son id persisté
        self.vpn_poll_seconds = max(30, _int(g("VPN_POLL_SECONDS", "60"), 60))
        self.vpn_status_cmd = g("VPN_STATUS_CMD", "/usr/local/sbin/vpn-status")  # exécuté SUR L'HYPERVISEUR (clé SSH restreinte)
        self.vpn_events = _bool(g("VPN_EVENTS"), True)
        # Heartbeat externe (dead-man's-switch) : URL de ping healthchecks.io/ntfy
        self.heartbeat_url = g("HEARTBEAT_URL", "").strip()

        # --- Proxmox API ---
        self.pve_host = g("PVE_HOST", "10.3.10.200")
        self.pve_port = _int(g("PVE_PORT", "8006"), 8006)
        self.pve_node = g("PVE_NODE", "pve")
        self.pve_verify_ssl = _verify_ssl(g("PVE_VERIFY_SSL"), False)
        self.pve_token_id = g("PVE_TOKEN_ID", "discordbot@pve!bot")
        self.pve_token_secret = g("PVE_TOKEN_SECRET").strip()
        self.pve_action_token_id = g("PVE_ACTION_TOKEN_ID", "discordbot@pve!actions")
        self.pve_action_token_secret = g("PVE_ACTION_TOKEN_SECRET").strip()
        self.pve_pbs_storage = g("PVE_PBS_STORAGE", "pbs")

        # --- Cluster secondaire AVEYRON (2026-07-17, « tout sur le même bot ») ---
        # Le même bot supervise le cluster 3 nœuds d'Aveyron : invités suffixés
        # « -<AVY_SUFFIX> », salons dans « Gestion <AVY_SERVER_KEY> », boutons gardés par
        # les rôles de cette clé dans GESTION_SERVERS. Vide (pas d'hôte/jeton) = inactif.
        self.avy_key = g("AVY_SERVER_KEY", "AVEYRON").strip()
        self.avy_suffix = g("AVY_SUFFIX", "avy").strip()
        # 2026-08-28 (Nico) : TROIS points d'entrée équivalents — 10.0.10.10 (nas),
        # 10.0.10.11 (ms01), 10.0.10.12 (llm). Le bot doit rester opérationnel si l'un
        # d'eux tombe : AVY_PVE_HOSTS (liste) prime, AVY_PVE_HOST reste accepté (1 hôte).
        self.avy_hosts = [x.strip() for x in g("AVY_PVE_HOSTS", "").split(",") if x.strip()]
        if not self.avy_hosts and g("AVY_PVE_HOST", "").strip():
            self.avy_hosts = [g("AVY_PVE_HOST").strip()]
        self.avy_host = self.avy_hosts[0] if self.avy_hosts else ""
        self.avy_port = _int(g("AVY_PVE_PORT", "8006"), 8006)
        self.avy_verify_ssl = _verify_ssl(g("AVY_PVE_VERIFY_SSL"), False)
        self.avy_token_id = g("AVY_PVE_TOKEN_ID", "discordbot@pve!bot")
        self.avy_token_secret = g("AVY_PVE_TOKEN_SECRET").strip()
        self.avy_action_token_id = g("AVY_PVE_ACTION_TOKEN_ID", "discordbot@pve!actions")
        self.avy_action_token_secret = g("AVY_PVE_ACTION_TOKEN_SECRET").strip()
        # Identifiants Proxmox (utilisateur + mot de passe, ticket renouvelé par proxmoxer)
        # demandés par Nico le 2026-08-28 pour Aveyron : quand AVY_PVE_PASSWORD est
        # renseigné, il remplace les DEUX jetons (lecture ET actions) — un seul compte,
        # valable sur les trois nœuds. Sinon, on reste sur les jetons.
        self.avy_user = g("AVY_PVE_USER", "").strip()
        self.avy_password = g("AVY_PVE_PASSWORD", "")
        self.avy_storage = g("AVY_PVE_STORAGE", "nas-backup")
        # nœuds supervisés (STATIQUE : un nœud éteint garde ses salons/catégories) ;
        # chaque nœud = un « serveur » à part entière (clé AVY-<NOM>, cf. pve.avy_server_key)
        self.avy_nodes = [x.strip() for x in g("AVY_NODES", "").split(",") if x.strip()]
        self.avy_enabled = bool(self.avy_hosts and (
            (self.avy_user and self.avy_password) or self.avy_token_secret))

        # Certificat de l'hyperviseur NON authentifié tant que *_VERIFY_SSL est faux : les
        # jetons (dont celui d'ACTION : start/stop/backup/delete-backup) partent sur un
        # canal qu'un équipement du VLAN mgmt peut usurper — alors que la host key SSH du
        # même hôte, elle, est épinglée (nodeshell.py). On ne peut pas l'activer par défaut
        # (certificat auto-signé sans l'IP en SAN : tous les appels échoueraient), mais on
        # cesse de le TAIRE — 2026-08-11.
        if self.pve_verify_ssl is False:
            _log.warning("PVE_VERIFY_SSL=false : API Proxmox %s en TLS NON vérifié "
                         "(jeton d'action exposé à un MITM du réseau mgmt) — voir "
                         "_verify_ssl() pour passer un chemin de bundle CA", self.pve_host)
        if self.avy_enabled and self.avy_verify_ssl is False:
            _log.warning("AVY_PVE_VERIFY_SSL=false : API du cluster %s (%s) en TLS "
                         "NON vérifié", self.avy_key, ", ".join(self.avy_hosts))

        # --- NAS Synology = serveur à part entière (clé SYNO) 2026-08-07 ---
        # Supervisé en SNMP par Telegraf (mesures synology* du bucket Proxmox) : le bot
        # ne fait que LIRE Influx, il n'a aucun accès au NAS et ne peut rien y faire.
        # Le bloc ne s'active que si la clé existe dans GESTION_SERVERS (cf.
        # provision._syno_enabled) — l'hôte ci-dessous n'est qu'un affichage.
        self.syno_key = g("SYNO_SERVER_KEY", "SYNO").strip()
        self.syno_host = g("SYNO_HOST", "10.3.10.251").strip()


        # --- Terminal Discord (console root sur guest LXC via termproxy) ---
        self.terminal_enabled = _bool(g("TERMINAL_ENABLED"), False)
        # ouverture réservée à ces user-ids Discord (défaut : le break-glass ADMIN_IDS)
        self.terminal_owner_ids = _csv_ints(g("TERMINAL_OWNER_IDS")) or list(self.admin_ids)
        # …ou à quiconque porte l'un de ces rôles (comme ADMIN_ROLE_IDS)
        self.terminal_owner_role_ids = _csv_ints(g("TERMINAL_OWNER_ROLE_IDS"))
        # guests sensibles ré-exclus côté bot (double barrière avec l'ACL PVE)
        self.terminal_excluded_guests = {
            x.strip().lower() for x in
            g("TERMINAL_EXCLUDED_GUESTS", "vaultwarden,mailserver,bdd").split(",") if x.strip()}
        # 0 = inactivité ILLIMITÉE par défaut (2026-08-14). Assumé : c'est une console root,
        # mais c'est un choix explicite de conf, et la modale d'ouverture reste maîtresse.
        self.terminal_idle_min = max(0, _int(g("TERMINAL_IDLE_MIN", "10"), 10))
        # plafond de la valeur SAISIE à l'ouverture (modale « inactivité ») : la valeur
        # par défaut ci-dessus reste ce qui s'applique si rien n'est saisi. Le plafond
        # n'est jamais inférieur au défaut, sinon une conf incohérente rendrait le défaut
        # lui-même refusé.
        # **0 = AUCUN plafond** -> « illimité » devient saisissable dans la modale. Le test
        # `if _cap else` est indispensable : passer 0 dans le `max()` le ferait remonter au
        # défaut, donc rétablirait silencieusement un plafond que l'admin vient de lever.
        _cap = max(0, _int(g("TERMINAL_IDLE_MAX_MIN", "120"), 120))
        self.terminal_idle_max_min = max(self.terminal_idle_min, _cap) if _cap else 0
        self.pve_console_user = g("PVE_CONSOLE_USER", "botconsole@pve")
        self.pve_console_password = g("PVE_CONSOLE_PASSWORD").strip()

        # --- Terminal du NŒUD PVE (console root de l'hyperviseur, via SSH) ---
        # Voir bot/core/nodeshell.py pour le « pourquoi SSH et pas termproxy ».
        self.node_terminal_enabled = _bool(g("NODE_TERMINAL_ENABLED"), False)
        # ⚠️ PROPRIÉTAIRE UNIQUEMENT, volontairement SANS repli sur les rôles :
        # contrairement à TERMINAL_OWNER_ROLE_IDS (console LXC), aucun rôle Discord ne
        # peut ouvrir un shell root sur l'hyperviseur. Défaut = break-glass ADMIN_IDS.
        self.node_terminal_owner_ids = _csv_ints(g("NODE_TERMINAL_OWNER_IDS")) or list(self.admin_ids)
        self.node_ssh_host = g("NODE_SSH_HOST", self.pve_host)
        self.node_ssh_port = _int(g("NODE_SSH_PORT", "22"), 22)
        self.node_ssh_user = g("NODE_SSH_USER", "root")
        self.node_ssh_key = g("NODE_SSH_KEY", "/etc/discord-bot/node_ed25519")
        self.node_ssh_known_hosts = g("NODE_SSH_KNOWN_HOSTS", "/etc/discord-bot/node_known_hosts")
        self.node_terminal_idle_min = max(0, _int(g("NODE_TERMINAL_IDLE_MIN", "10"), 10))
        # salon « 🔒 Lock » + dashboard live du nœud
        self.node_channel_enabled = _bool(g("NODE_CHANNEL_ENABLED"), True)
        # bouton 💾 Sauvegarder du salon nœud = vzdump de TOUS les invités
        self.node_backup_enabled = _bool(g("NODE_BACKUP_ENABLED"), True)
        # …sauf ceux-ci (défaut 200 = le PBS lui-même, comme le job pbs-daily-cts)
        self.node_backup_exclude = g("NODE_BACKUP_EXCLUDE", "200").strip()
        # --- Salon temporaire de suivi du transfert média (2026-08-18) ---
        # Le premier remplissage du pool distant dure des jours : ce salon PUBLIC en
        # lecture seule le montre en direct, puis disparaît (cf. cogs/transfert.py).
        self.transfert_enabled = _bool(g("TRANSFERT_ENABLED"), True)
        self.transfert_unit = g("TRANSFERT_UNIT", "media-backup.service")
        self.transfert_log = g("TRANSFERT_LOG", "/var/log/media-backup.log")
        self.transfert_source = g("TRANSFERT_SOURCE", "/mnt/media")
        self.transfert_dest = g("TRANSFERT_DEST", "/mnt/avy-media")
        self.transfert_channel_name = g("TRANSFERT_CHANNEL_NAME", "transfert-medias")
        self.transfert_poll_sec = max(30, _int(g("TRANSFERT_POLL_SEC", "60"), 60))
        # temps de grâce avant suppression du salon une fois le transfert fini
        self.transfert_keep_min = max(0, _int(g("TRANSFERT_KEEP_MIN", "60"), 60))
        # taille TOTALE connue de la bibliothèque (texte libre, ex. « 3,4 Tio ») :
        # affichée tant que rsync analyse encore, pour que le total provisoire ne soit
        # pas pris pour la taille finale (question de Nico 2026-08-18)
        self.transfert_total_hint = g("TRANSFERT_TOTAL_HINT", "").strip()

        # durée de vie ABSOLUE d'une console du nœud, même active (0 = illimité)
        self.node_terminal_max_min = max(0, _int(g("NODE_TERMINAL_MAX_MIN", "120"), 120))
        # plafond de l'inactivité SAISIE pour le shell root de l'hyperviseur. Défaut plus
        # bas que celui des guests (shell root sur l'hôte) et, quand une durée de vie
        # absolue est posée, borné par elle : autoriser « 4 h d'inactivité » alors que la
        # session est tuée à 2 h dans tous les cas ne serait qu'un mensonge d'interface.
        # 0 = pas de plafond -> « illimité » saisissable… MAIS seulement si la durée de vie
        # absolue est elle-même levée : sinon on continue de borner par elle (promettre une
        # inactivité infinie sur une session tuée à 2 h serait un mensonge d'interface).
        _ncap = max(0, _int(g("NODE_TERMINAL_IDLE_MAX_MIN", "60"), 60))
        if not _ncap:
            self.node_terminal_idle_max_min = self.node_terminal_max_min  # 0 si illimitée
        else:
            self.node_terminal_idle_max_min = max(
                self.node_terminal_idle_min,
                min(_ncap, self.node_terminal_max_min or 10 ** 9))

        # --- Live log stream ---
        self.live_log_bind_addr = g("LIVE_LOG_BIND_ADDR", "0.0.0.0")
        self.live_log_bind_port = _int(g("LIVE_LOG_BIND_PORT", "514"), 514)
        self.live_log_min_severity = g("LIVE_LOG_MIN_SEVERITY", "warning").strip().lower()
        self.live_log_flush_interval = _float(g("LIVE_LOG_FLUSH_INTERVAL", "10"), 10.0)
        self.live_log_max_groups = _int(g("LIVE_LOG_MAX_GROUPS_PER_FLUSH", "8"), 8)
        self.log_retry_queue_max = max(1, _int(g("LOG_RETRY_QUEUE_MAX", "20"), 20))
        self.log_repeat_cooldown_seconds = max(
            0, _int(g("LOG_REPEAT_COOLDOWN_SECONDS", "300"), 300))
        self.log_startup_notice = _bool(g("LOG_STARTUP_NOTICE"), True)
        # patterns separated by ';', re.search against "host app: text"
        self.log_ignore_regex = g("LOG_IGNORE_REGEX").strip()
        # per-host severity thresholds "host:sev,..." (sev names, resolved in logstream)
        self.log_min_sev_overrides = self._parse_sev_overrides(g("LOG_MIN_SEV_OVERRIDES"))
        # muted appnames (case-insensitive exact match)
        self.log_mute_programs = {p.strip().lower() for p in
                                  g("LOG_MUTE_PROGRAMS").split(",") if p.strip()}
        self.log_route_per_ct = _bool(g("LOG_ROUTE_PER_CT"), False)
        mode = g("LOG_ROUTE_MODE", "mirror").strip().lower()
        self.log_route_mode = mode if mode in ("mirror", "move") else "mirror"

        # --- Loki (centralized logs) ---
        self.loki_url = g("LOKI_URL").strip()

        # --- Behavior ---
        self.dashboard_interval_min = max(1, _int(g("DASHBOARD_INTERVAL_MIN", "2"), 2))
        self.report_hour = _int(g("REPORT_HOUR", "8"), 8)
        self.report_minute = _int(g("REPORT_MINUTE", "0"), 0)
        self.alert_poll_seconds = max(30, _int(g("ALERT_POLL_SECONDS", "60"), 60))
        self.tz = g("TZ", "Europe/Paris")

        state_dir = g("STATE_DIRECTORY", "/var/lib/discord-bot")
        self.state_path = os.path.join(state_dir, "state.json")

        # --- Serveur de distribution Fronote (CT122 fronote-dist) 2026-08-12 ---
        # API d'admin de la whitelist d'IP (dist/server.php). DIST_ADMIN_TOKEN =
        # admin_token de dist/config.php sur CT122 (partagé, jamais en clair en
        # réponse). Les DEUX vides = cog /dist et veille des refus inactifs.
        self.dist_url = g("DIST_URL", "").rstrip("/")
        self.dist_admin_token = g("DIST_ADMIN_TOKEN").strip()
        # salon des notifications « IP refusée » (défaut : le salon d'alertes)
        self.dist_alert_channel_id = _int(g("DIST_ALERT_CHANNEL_ID"))
        self.dist_poll_seconds = max(30, _int(g("DIST_POLL_SECONDS", "120"), 120))
        # --- Parc d'instances Fronote (phone-home) 2026-08-18 ------------------
        # Chaque instance installée se signale toutes les DIST_PHONE_HOME_HOURS (le cron
        # client). La veille du parc relit GET /admin/park à cette cadence, pousse une
        # métrique par instance dans InfluxDB (dashboard « Parc Fronote ») et alerte en
        # silence-radio quand une instance dépasse ~2,5× son intervalle attendu.
        self.dist_park_poll_seconds = max(60, _int(g("DIST_PARK_POLL_SECONDS", "900"), 900))
        self.dist_phone_home_hours = max(1, _int(g("DIST_PHONE_HOME_HOURS", "24"), 24))
        # âge de sauvegarde (heures) au-delà duquel une instance est signalée « souffrante »
        self.dist_backup_stale_hours = max(1, _int(g("DIST_BACKUP_STALE_HOURS", "48"), 48))
        # --- Propositions du site vitrine (formulaire dist.nicov1.fr) ---------
        # Salon où poster les demandes reçues. 0 = le bot crée/adopte #propositions
        # sous la catégorie Supervision (repli : salon d'alertes).
        self.dist_proposals_channel_id = _int(g("DIST_PROPOSALS_CHANNEL_ID"))
        self.dist_proposals_poll_seconds = max(30, _int(g("DIST_PROPOSALS_POLL_SECONDS", "120"), 120))

        # --- Portail SSO Authelia (CT123 auth) 2026-08-20 ----------------------
        # /sso + veille sso_watch : lecture SEULE de CT123 via l'hyperviseur
        # (nodeshell.run_readonly puis `pct exec`). La table authentication_logs
        # d'Authelia est la seule trace fiable des tentatives (le log texte ne les
        # contient pas au niveau info). SSO_LOGIN_NOTIFY=0 tait les connexions
        # réussies dans #alertes (bans et échecs restent toujours postés).
        self.sso_ct_id = g("SSO_CT_ID", "123").strip()
        self.sso_db = g("SSO_DB", "/opt/auth/authelia/db.sqlite3").strip()
        self.sso_notif_file = g("SSO_NOTIF_FILE", "/opt/auth/authelia/notification.txt").strip()
        self.sso_poll_seconds = max(60, _int(g("SSO_POLL_SECONDS", "120"), 120))
        self.sso_login_notify = g("SSO_LOGIN_NOTIFY", "1").strip().lower() not in ("0", "false", "non", "no")

        # /dns + boucles dns_poll/dns_digest : AdGuard Home (CT125 dns, 10.3.10.53)
        # Lecture du journal des requêtes + règles utilisateur. USER/PASS vides = cog inactif.
        self.adguard_url = g("ADGUARD_URL", "http://10.3.10.53:3000").strip().rstrip("/")
        self.adguard_user = g("ADGUARD_USER", "").strip()
        self.adguard_pass = g("ADGUARD_PASS", "")

        # Relais des alertes Grafana par le bot (cog grafana_alerts, Nico 29/08/2026 :
        # « pas de webhook grafana »). Token = compte de service Grafana « edmine »
        # (Viewer, lecture seule). Salon : GRAFANA_ALERT_CHANNEL_ID, repli ALERT_CHANNEL_ID.
        self.grafana_url = g("GRAFANA_URL", "http://10.3.10.104:3000").strip().rstrip("/")
        self.grafana_token = g("GRAFANA_TOKEN", "").strip()
        self.grafana_alert_channel_id = _int(g("GRAFANA_ALERT_CHANNEL_ID")) or self.alert_channel_id
        self.grafana_poll_seconds = max(30, _int(g("GRAFANA_POLL_SECONDS", "60"), 60))
        self.dns_channel_id = _int(g("DNS_CHANNEL_ID"))          # repli si non provisionné
        self.dns_poll_seconds = max(15, _int(g("DNS_POLL_SECONDS", "60"), 60))
        self.dns_feed_logs = g("DNS_FEED_LOGS", "0").strip().lower() in ("1", "true", "oui", "yes")
        self.dns_blocked_feed = g("DNS_BLOCKED_FEED", "0").strip().lower() in ("1", "true", "oui", "yes")
        self.dns_spike_blocked = max(5, _int(g("DNS_SPIKE_BLOCKED", "30"), 30))
        self.dns_spike_queries = max(50, _int(g("DNS_SPIKE_QUERIES", "600"), 600))

        # --- 2FA (TOTP) --------------------------------------------------------
        # Défaut FALSE volontaire : activer avant d'être inscrit barrerait toutes les
        # commandes sans laisser personne s'inscrire. On active APRÈS un /2fa setup.
        # Break-glass : remettre TWOFA_ENABLED=false ici + redémarrer le bot.
        # --- /docker (conteneurs CT120 via ytgrab) — kill-switch DOCKER_CTL_ENABLED=false
        self.docker_ctl_enabled = _bool(g("DOCKER_CTL_ENABLED"), True)
        self.twofa_enabled = g("TWOFA_ENABLED", "false").lower() in ("1", "true", "yes", "on")
        # Durée par défaut d'une session de confiance, en minutes — **0 = illimitée**
        # (2026-08-14). C'est un simple point de départ : `/2fa duree` la change à chaud et
        # le réglage est persisté à côté des sessions ; cette valeur reprend la main si ce
        # fichier disparaît (repli borné, jamais « illimité par surprise »).
        self.twofa_session_min = max(0, _int(g("TWOFA_SESSION_MIN", "15"), 15))
        # Plafond de ce que `/2fa duree` et la modale de déverrouillage peuvent demander.
        # **0 = aucun plafond** -> « illimité » devient possible. Défaut 0 : Nico est le
        # seul administrateur du bot et a demandé explicitement l'option illimitée ; la
        # borne reste disponible pour qui voudrait la reposer.
        self.twofa_session_max_min = max(0, _int(g("TWOFA_SESSION_MAX_MIN", "0"), 0))
        # Secrets à part de state.json : même dossier (écrit par le bot) mais fichier 0600.
        self.twofa_path = g("TWOFA_PATH", os.path.join(state_dir, "2fa.json"))
        self.audit_path = os.path.join(state_dir, "audit.log")

    @staticmethod
    def _parse_sev_overrides(v):
        out = {}
        for part in (v or "").split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            host, _, sev = part.rpartition(":")
            host, sev = host.strip(), sev.strip().lower()
            if host and sev:
                out[host] = sev
        return out

    @staticmethod
    def _parse_gestion_servers(v):
        """« R820:G:M:O,AVEYRON:G:M:O » -> {R820:{view,mod,owner}, …}. Rétrocompat : une
        entrée à 2 ids « clé:G:O » est lue comme {view:G, mod:G, owner:O} (ancien modèle)."""
        out = {}
        for part in (v or "").split(","):
            part = part.strip()
            if not part:
                continue
            bits = [x.strip() for x in part.split(":")]
            key = bits[0] if bits else ""
            ids = bits[1:]
            if not key or not ids or not all(x.isdigit() for x in ids):
                _log.warning("GESTION_SERVERS: entrée ignorée (mal formée): %r", part)
                continue
            if len(ids) == 3:
                out[key] = {"view": int(ids[0]), "mod": int(ids[1]), "owner": int(ids[2])}
            elif len(ids) == 2:
                # ancien format clé:gestion:o -> le rôle « gestion » = view SEUL ; le rôle O
                # sert de mod ET owner (le rôle « vue » ne doit JAMAIS donner Lock/nœud).
                out[key] = {"view": int(ids[0]), "mod": int(ids[1]), "owner": int(ids[1])}
            else:
                _log.warning("GESTION_SERVERS: %r attend 2 ou 3 ids (G:M:O), ignoré", part)
        return out

    @staticmethod
    def _parse_ct_channels(v):
        out = {}
        for part in (v or "").split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            name, _, cid = part.rpartition(":")
            name, cid = name.strip(), cid.strip()
            if name and cid.isdigit():
                out[name] = int(cid)
        return out

    # token id "user@realm!name" -> (user@realm, name)
    @staticmethod
    def split_token(token_id):
        user, _, name = token_id.partition("!")
        return user, name

    def missing_required(self):
        miss = []
        if not self.discord_token:
            miss.append("DISCORD_TOKEN")
        if not self.guild_id:
            miss.append("GUILD_ID")
        return miss

    @property
    def influx_enabled(self):
        return bool(self.influx_token)

    @property
    def loki_enabled(self):
        return bool(self.loki_url)

    @property
    def adguard_enabled(self):
        return bool(self.adguard_url and self.adguard_user and self.adguard_pass)

    @property
    def grafana_enabled(self):
        return bool(self.grafana_url and self.grafana_token)

    @property
    def pve_enabled(self):
        return bool(self.pve_token_secret)

    @property
    def pve_actions_enabled(self):
        return bool(self.pve_action_token_secret)

    @property
    def terminal_ready(self):
        return bool(self.terminal_enabled and self.pve_console_password
                    and (self.terminal_owner_ids or self.terminal_owner_role_ids))

    @property
    def node_terminal_ready(self):
        """Prêt seulement si la clé existe VRAIMENT et qu'un propriétaire est défini.

        Le test d'existence est délibéré : sans lui, une clé absente ne se manifesterait
        qu'au clic, par une erreur opaque, alors qu'ici le bouton est simplement refusé
        avec un message clair. Aucun repli sur les rôles (cf. node_terminal_owner_ids)."""
        return bool(self.node_terminal_enabled and self.node_terminal_owner_ids
                    and self.node_ssh_key and os.path.exists(self.node_ssh_key))
