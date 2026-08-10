# discord-bot — bot Discord gateway de monitoring Proxmox (CT106)

Vrai bot Discord (slash-commands, **pas** de webhook) qui interroge **directement**
InfluxDB v2 + l'API Proxmox et rend des **graphiques matplotlib** dans Discord :
monitoring (système, matériel, stockage, sauvegardes), logs (à la demande + flux live),
actions sûres (start/stop/restart CT, backup), dashboard live, alertes proactives sur
les trous de Grafana, rapport quotidien.

## Architecture

- Tourne dans **CT106** (`discord-bot`, `10.3.10.106`, Debian 13), service systemd durci.
- Sources : InfluxDB `10.3.10.120:8086` (bucket `Proxmox`, **token read-only**), API PVE
  `10.3.10.200:8006` (deux tokens : `!bot` PVEAuditor + `!actions` BotSafeActions).
- Complète l'alerting Grafana→Discord existant (ne le duplique pas).

## Déploiement (depuis l'hôte pve)

```bash
cd /root/pve-improvements/r820-deploy/discord-bot
./pve_setup_token.sh           # crée user + rôles + 2 tokens PVE (note les secrets imprimés)
./provision_ct106.sh           # crée CT106, push le code, venv+deps, service (firewall en dernier)
```

Puis renseigner les secrets et démarrer :

```bash
pct exec 106 -- nano /opt/discord-bot/config.env
#   DISCORD_TOKEN, GUILD_ID, ADMIN_IDS, *_CHANNEL_ID
#   INFLUX_TOKEN (token READ créé dans l'UI InfluxDB sur le bucket Proxmox)
#   PVE_TOKEN_SECRET / PVE_ACTION_TOKEN_SECRET (sortie de pve_setup_token.sh)
pct exec 106 -- systemctl restart discord-bot
pct exec 106 -- journalctl -u discord-bot -f
```

`provision_ct106.sh` est idempotent : relancé, il met à jour le code et redémarre sans
écraser `config.env`.

## Côté Discord (à faire une fois)

1. https://discord.com/developers/applications → New Application → Bot → copier le **token**.
2. OAuth2 → URL Generator : scopes `bot` + `applications.commands` ; perms : Send Messages,
   Embed Links, Attach Files, Read Message History, Manage Messages (pour épingler). Inviter.
3. Activer le *Mode développeur* Discord pour copier `GUILD_ID`, votre user id, les channel ids.

## Commandes

27 commandes de premier niveau :

- Lecture : `/status` `/ping` `/node` `/ct` `/cts` `/graph` `/storage` `/thinpool`
  `/raid` `/smart` `/temps` `/ipmi` `/backups` `/logs` `/journal` `/tail` `/tasks`
- Loki (si `LOKI_URL`) : `/logsearch` `/ctlogs` `/apperrors`
- Actions (admin + salon admin + confirmation) : `/ctctl start|stop|restart` `/backup` `/audit`
- Admin flux de logs : `/logstream stats|severity|pause|resume`
- Dashboard live : `/dashboard create|stop`

## Flux de logs live (optionnel)

Le bot écoute en UDP/514 (si `LIVE_LOG_CHANNEL_ID` est renseigné). Pointer les émetteurs
vers `10.3.10.106:514` :

- Hôte pve (rsyslog non installé par défaut) : `apt-get install -y rsyslog` puis
  `echo '*.warning @10.3.10.106:514' > /etc/rsyslog.d/90-discord-bot.conf && systemctl restart rsyslog`
- CRS305 / RouterOS : `/system logging action set remote target=10.3.10.106:514`
- Un CT au choix (logs applicatifs) : même ligne rsyslog → il apparaît dans le canal flux-logs.

Le pare-feu CT106 (`106.fw`) n'autorise UDP/514 que depuis l'hôte et le routeur.

## Pipeline de logs v2

Améliorations du flux live (toutes optionnelles, comportement inchangé par défaut) :

- **Appname** : le tag syslog (RFC3164 `app[pid]:` ou APP-NAME RFC5424) est extrait,
  affiché (`⚠️ \`warning\` **host** \`app\`: message`) et entre dans la clé de coalescence.
- **Résilience d'envoi** : les messages Discord en échec sont gardés dans une file
  (`LOG_RETRY_QUEUE_MAX`, défaut 20) et re-tentés au flush suivant ; à l'arrêt (SIGTERM),
  flush final préfixé `⏹️ arrêt du flux —` avant la fermeture de la gateway.
- **Digest anti-flood** : au-delà de `LIVE_LOG_MAX_GROUPS_PER_FLUSH`, une seule ligne
  `📦 +N groupes: host xN (pire: sev) · …` au lieu de messages perdus.
- **Anti-répétition** : un même groupe déjà posté est supprimé pendant
  `LOG_REPEAT_COOLDOWN_SECONDS` (défaut 300, 0 = off) puis reposté avec
  `(xM, toujours en cours depuis HH:MM)`.
- **Filtres** (appliqués à la réception) : `LOG_IGNORE_REGEX` (motifs `;`-séparés sur
  `host app: texte`), `LOG_MIN_SEV_OVERRIDES` (`host:sev,…` — remplace le seuil global ;
  en pratique ne peut que durcir car les émetteurs ne forwardent que `*.warning`),
  `LOG_MUTE_PROGRAMS` (appnames csv, insensible à la casse).
- **Routage par CT** : `LOG_ROUTE_PER_CT=true` route les groupes d'un hôte mappé dans
  `CT_CHANNELS` vers son salon — `LOG_ROUTE_MODE=mirror` (copie, défaut) ou `move`
  (le salon de logs ne garde alors que les hôtes non mappés, ex. `pve`).
- **Admin runtime** : `/logstream stats` (compteurs), `/logstream severity <niveau>`
  (non persistant), `/logstream pause` / `resume`. Ligne de démarrage `📡 Flux de logs
  démarré…` désactivable via `LOG_STARTUP_NOTICE=false`.

## Loki (logs centralisés, optionnel)

Si `LOKI_URL` est renseigné (endpoint prévu : `http://10.3.10.123:3100`), trois
commandes de lecture interrogent l'API HTTP Loki (labels figés : `host` = hostname
machine, `unit` = unité systemd, `level` = vocabulaire journald, `job` =
`systemd-journal|jellyfin|caddy`) :

- `/logsearch [requete] [host] [unit] [level] [contains] [plage]` — recherche libre ;
  `requete` commençant par `{` = LogQL brut, sinon texte recherché. Plages : 15 min à 7 j.
- `/ctlogs <ct> [unit] [level] [plage]` — logs d'un conteneur (label `host` = nom du CT).
- `/apperrors <ct> [plage]` — erreurs (`level=~"err|crit|alert|emerg"`), avec repli
  texte `error` si aucun résultat.

Le rapport quotidien gagne un champ **Top logs (24h)** (top 5 hôtes par volume de
logs ≥ warning). Le pare-feu `106.fw` autorise la sortie TCP/3100 vers Loki.

## Salon « 🔒 Lock » — hyperviseur (2026-07-15)

`provision.py` crée la catégorie **« 🔒 Lock »** (@everyone : *Voir le salon* refusé) et, dedans,
le salon **`{🟢|🟠|🔴}-pve`** de l'hôte : embed épinglé rafraîchi toutes les
`DASHBOARD_INTERVAL_MIN` min (uptime, CPU/charge, RAM, swap, `/`, stockages, invités, IPMI
températures/ventilateurs/conso, RAID, SMART, sauvegardes PBS, noyau) et trois boutons
persistants — **🔄 Rafraîchir**, **💾 Sauvegarder** (`vzdump --all`, avec confirmation),
**🖥️ Terminal** (shell root sur l'hôte). **Pas de Start/Stop/Reboot** : volontaire.

⚠️ **La permission du salon n'est pas la frontière de sécurité.** Un membre portant la
permission Discord « Administrateur » voit le salon malgré les overwrites — Discord ne
permet pas de les lui cacher. Ce qui protège réellement, c'est la porte des boutons :
`NODE_TERMINAL_OWNER_IDS`, **sans repli sur les rôles** (à la différence de
`TERMINAL_OWNER_ROLE_IDS` qui ouvre la console des LXC).

### Console root du nœud : pourquoi SSH et pas `termproxy`

PVE ne donne un shell root sur le **nœud** qu'à `root@pam` (`PVE/API2/Nodes.pm`,
`get_shell_command` : *« non-root must always login for now »*) ; tout autre compte, même
avec `Sys.Console`, tombe sur un prompt `login:`. Un **token** d'API ne s'en sort pas non
plus (l'utilisateur authentifié devient `root@pam!nom` ≠ `root@pam`). La voie termproxy
imposait donc de stocker le mot de passe root de PVE dans `config.env` — d'où, en cas de
compromission de CT106, l'admin complet de l'API PVE, console comprise sur les guests
pourtant exclus (`vaultwarden`/`mailserver`/`bdd`).

SSH avec une clé dédiée est strictement moins privilégié :

| | mot de passe `root@pam` | clé SSH dédiée (retenu) |
|---|---|---|
| Secret stocké | mot de passe root réutilisable | clé ed25519 dédiée, 0400 |
| Portée | shell **+ API PVE complète** | shell uniquement, aucune ACL PVE |
| Restriction source | aucune | `from="10.3.10.106"` (vérifié : refusée ailleurs) |
| Révocation | changer le mot de passe root | supprimer 1 ligne d'`authorized_keys` |
| Trace côté hôte | — | sshd : `Accepted publickey … from 10.3.10.106` |

Mise en place (cf. `config.env.example`) : clé dans `/etc/discord-bot/node_ed25519`
(0400 `discordbot`), host key épinglée dans `node_known_hosts` (`known_hosts=None` est
proscrit : ce serait un MITM possible depuis le réseau mgmt), ligne
`restrict,pty,from="10.3.10.106"` dans `/etc/pve/priv/authorized_keys` de l'hôte, et
`OUT ACCEPT -dest 10.3.10.200 -p tcp -dport 22` dans `106.fw` (`policy_out: DROP`).

**Kill-switch** : `NODE_TERMINAL_ENABLED=false` **ou** supprimer la ligne d'`authorized_keys`
(effet immédiat, sans redémarrer le bot).

## Limites v1 (volontaires)

- ~~Pas de `fstrim`~~ — une clé SSH restreinte vers l'hôte existe désormais (console du nœud) ;
  `fstrim` reste néanmoins non exposé en bouton.
- Pas de compteur de mises à jour APT dans l'embed du nœud : `GET /nodes/{node}/apt/update`
  exige `Sys.Modify`, permission bien trop large pour un compteur (le token du bot est
  `PVEAuditor` → 403).
- Pas de « Pics sur 24 h » pour le nœud (contrairement aux invités) : dans InfluxDB,
  `object=="nodes"` ne porte que `uptime`.
- Pas de miroir des alertes Grafana (Grafana garde le paging critique ; le bot couvre les trous).
- Logs applicatifs par-CT via forward rsyslog (le bot dans CT106 ne peut pas `pct exec`).
- Quand l'hôte est éteint la nuit, CT106 (et le bot) le sont aussi ; reprise auto via `onboot=1`
  + rattrapage du rapport au réveil. L'alerting critique reste assuré par Grafana.
