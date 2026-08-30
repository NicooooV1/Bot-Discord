# Edmine — bot Discord de supervision et de pilotage du homelab (CT106)

Vrai bot Discord (slash-commands + boutons persistants, **pas** de webhook) qui interroge
**directement** InfluxDB v2, l'API Proxmox, Loki et quelques API applicatives, et rend des
**graphiques matplotlib** dans Discord : supervision (système, matériel, stockage,
sauvegardes), logs (à la demande + flux live), actions sûres (start/stop/restart d'un
invité, sauvegarde), consoles root, salons auto-provisionnés par invité, seedbox et
médias, téléchargements YouTube/Twitch, assistant IA local, alertes proactives sur les
trous de Grafana, rapport quotidien.

> Ce README a été **relu ligne à ligne contre le code le 2026-08-11**. Cinq familles
> d'affirmations avaient dérivé (nombre de commandes, porte de la console du nœud, nom des
> catégories, mode de déploiement, périmètre du bot) : elles sont corrigées ci-dessous. En
> cas de doute, **le code fait foi** — et les docstrings des modules encodent les pièges
> réellement vécus.

## Architecture

- Tourne dans **CT106** (`discord-bot`, `10.3.10.106`, Debian 13), service systemd durci
  (`discord-bot.service` : `ProtectSystem=strict`, état dans `StateDirectory`, seul
  `.mplcache` inscriptible).
- Sources : InfluxDB `10.3.10.120:8086` (bucket `Proxmox`, **token read-only**), API PVE
  `10.3.10.200:8006` (deux tokens : `!bot` PVEAuditor et `!actions` BotSafeActions),
  Loki (optionnel), API servarr/Jellyfin/qBittorrent (optionnelles,
  `servarr-apis.json`).
- **Plusieurs serveurs physiques** dans le même Discord : `SERVER_KEY` nomme celui-ci
  (`R820`), et le bot supervise en plus les nœuds **Aveyron** (`AVY-PVE` / `AVY-NAS` /
  `AVY-LLM`, API PVE distante) et le **NAS Synology** (`SYNO`). Chaque clé a ses propres
  catégories, ses propres rôles et ses propres boutons.
- Complète l'alerting Grafana→Discord existant (ne le duplique pas).

## Modèle d'accès (ce qui protège réellement)

- **Trois tiers de rôles PAR SERVEUR**, déclarés dans `GESTION_SERVERS` (`CLÉ:G:M:O`) :
  **G** voit les salons, **M** agit (boutons, catégorie Lock), **O** est propriétaire du
  serveur en question. Le propriétaire du guild et `ADMIN_IDS` restent le break-glass.
  Attribution déléguée par `/gestion add|remove|list`.
- **Fail-closed** : une clé serveur absente de `GESTION_SERVERS` fait REFUSER l'action au
  lieu de retomber sur les rôles globaux.
- **2FA (TOTP) sur TOUTES les commandes** quand `TWOFA_ENABLED=true` — seule la famille
  `/2fa` est exemptée (sinon la clé serait enfermée à l'intérieur). Les **boutons** aussi :
  chaque vue déclare sa porte via `GatedView` (`read` / `mod` / `owner`).
- La permission Discord « Administrateur » **n'ouvre rien** : seul le rôle Gestion accordé
  via `/gestion` (ou le break-glass) autorise une action.

## Déploiement

### Première installation (depuis l'hôte pve)

```bash
cd <répertoire des sources>            # un dépôt git : le déploiement en dépend
./pve_setup_token.sh                   # crée user + rôles + 2 tokens PVE (noter les secrets)
./provision_ct106.sh                   # crée CT106, y pousse le code, venv+deps, service
```

Puis renseigner les secrets et démarrer :

```bash
pct exec 106 -- nano /opt/discord-bot/config.env
#   DISCORD_TOKEN, GUILD_ID, ADMIN_IDS, GESTION_SERVERS, *_CHANNEL_ID
#   INFLUX_TOKEN (token READ créé dans l'UI InfluxDB sur le bucket Proxmox)
#   PVE_TOKEN_SECRET / PVE_ACTION_TOKEN_SECRET (sortie de pve_setup_token.sh)
pct exec 106 -- systemctl restart discord-bot
pct exec 106 -- journalctl -u discord-bot -f
```

`provision_ct106.sh` est idempotent : relancé, il met à jour le code et redémarre sans
écraser `config.env`.

**Le code voyage par git, plus par un tar.** Un `tar -x` par-dessus l'existant ne supprime
rien : un fichier retiré de la source survivait dans `/opt` et continuait d'être chargé
(constat d'audit 2026-08-11). Le script envoie maintenant un *bundle* git, puis, dans le
CT, `git reset --hard` + `git clean` alignent `/opt/discord-bot` sur le commit — les
fichiers ignorés (`config.env`, `venv/`, `servarr-apis.json`) étant, eux, préservés.
Conséquence utile : `/opt/discord-bot` est un vrai dépôt, donc `deploy.sh` sait revenir en
arrière. Le script refuse de déployer si des modifications ne sont pas commitées
(`--force` pour passer outre : il déploiera alors le dernier commit, pas ton travail en
cours).

⚠️ **Corollaire du passage tar → git : ce qui n'est dans aucun commit ne part pas.** Le
tar poussait tout le répertoire ; git ne pousse que ce qui est *suivi*. Un fichier neuf
jamais `git add`-é reste donc à quai, pendant que les modules qui l'importent, eux,
partent — soit un `ImportError` et un service en boucle. `provision_ct106.sh` **refuse**
désormais quand du `.py` neuf de `bot/` ou `tests/` n'est dans aucun commit, et liste les
autres fichiers non suivis (`106.fw`, qui vit légitimement à côté des sources, n'est
qu'un avertissement).

### Mise à jour de routine — `./deploy.sh` (dans le CT)

`install.sh` installe, `deploy.sh` **déploie avec un filet** :

```bash
pct exec 106 -- bash -lc 'cd /opt/discord-bot && ./deploy.sh'
```

1. refuse un arbre git sale (`--force` pour passer outre) et affiche le diff résumé ;
2. lance la suite de tests — **abandon** si un test échoue ;
3. importe TOUS les modules de `bot/` (`pkgutil.walk_packages`) : un cog cassé ne se
   découvre sinon qu'après le redémarrage ;
4. mémorise la version, redémarre `discord-bot` ;
5. attend ~20 s (et jusqu'à 60 s si Discord traîne) puis vérifie le démarrage RÉEL :
   service actif, `synced` et `Connecté` présents dans le journal de **cette** invocation
   (`_SYSTEMD_INVOCATION_ID`), et lecture des `ERROR`/`CRITICAL` ;
6. si ça se passe mal : **rollback automatique** (`git reset --hard` sur la dernière
   version validée + redémarrage), et sortie en erreur avec un message clair.

Les `ERROR` du journal sont **affichées**, mais seules celles qui mettent en cause le code
déployé (un `CRITICAL`, un cog qui ne se charge pas, une config incomplète) déclenchent le
rollback. Le bot compte une centaine de points de `log.error`, la plupart dans des boucles
de fond dont le premier tour tombe précisément dans cette fenêtre : une source externe
injoignable (Aveyron, Influx, PVE) faisait sinon annuler un déploiement parfaitement sain
au profit d'une version qui journalise la même erreur.

⚠️ `deploy.sh` est le filet qu'on passe **derrière** : lancé depuis l'hôte,
`provision_ct106.sh` appelle `install.sh`, qui a **déjà** redémarré le service sur le code
neuf. Un échec à l'étape 2 ou 3 signifie donc « ce script n'a rien redémarré », pas « rien
n'est parti ».

La « dernière version validée » est mémorisée dans
`/var/lib/discord-bot/dernier-deploiement-ok` après chaque déploiement réussi — c'est la
seule cible de rollback qui ait un sens (revenir sur `HEAD`, c'est-à-dire sur la version
qu'on vient de déployer, ne servirait à rien). Au tout premier passage, ce marqueur
n'existe pas encore : le script le dit au lieu de faire semblant.

`deploy.sh` ne réinstalle pas les dépendances : après un changement de `requirements.txt`,
lancer `./install.sh` d'abord. Celui-ci **ne sort sur le réseau que si un pin diffère
réellement de l'installé** — sur un CT106 déjà pare-feuté, les dépôts sont injoignables et
un `pip install` inutile ferait échouer l'installation avant la mise à jour de l'unité
systemd.

### Dépôt git et retour arrière

Le code est versionné (`git log --oneline`). Points de repère :

| Repère | Ce que c'est |
|---|---|
| tag `avant-corrections-audit` | l'état **identique à la production** avant la campagne de correction de l'audit du 2026-08-11 |
| `dernier-deploiement-ok` | commit de la dernière version dont le démarrage a été VÉRIFIÉ (cf. `deploy.sh`) |

```bash
git log --oneline                       # historique
git diff avant-corrections-audit --stat # tout ce qu'a changé la campagne
git reset --hard avant-corrections-audit && systemctl restart discord-bot   # retour total
```

Si git répond « detected dubious ownership » : le dépôt appartient à `discordbot` et tu es
root — `git config --global --add safe.directory /opt/discord-bot` (posé par `install.sh`).

### Suite de tests

```bash
cd /opt/discord-bot && ./venv/bin/python -m unittest discover -s tests -v
```

Stdlib `unittest`, zéro dépendance ajoutée, aucun accès réseau externe (les tests HTTP
parlent à un `http.server` local sur `127.0.0.1`).

- `tests/test_edmine.py` — invariants du bot : parsing de `GESTION_SERVERS`, `is_admin`
  fail-closed, anti-rejeu TOTP, parsing syslog, validation d'URL, filet des boucles… et
  surtout **`TestToutesLesVuesSontGardees`**, qui échoue dès qu'une `discord.ui.View` est
  ajoutée sans passer par `GatedView`.
- `tests/test_shared.py` — modules partagés : `channels.norm` / `channels.resolve`,
  `http.request_json` (timeout transmis, `None` = échec, `{}` = corps vide),
  `ApiClient.url`, `load_service_apis`, `format.outcome_text`.

### Modules partagés (`bot/core/`) — à utiliser, jamais à recopier

| Module | Ce qu'il porte | Règle |
|---|---|---|
| `gates.py` | `GatedView` : la porte d'une vue est une **donnée déclarée** (`gate = "read" \| "mod" \| "owner" \| None`) | **Toute vue hérite de `GatedView`.** Une exemption exige un `gate_reason` écrit ; un test le vérifie. |
| `channels.py` | résolution des catégories provisionnées (`lock_category`, `supervision_category`, `resolve`, `ensure_channel`, `seal_if_public`, `norm`) | **Aucun cog ne crée de salon hors d'une catégorie provisionnée** : sans catégorie, pas de salon (un salon créé à la racine est PUBLIC). |
| `http.py` | client HTTP JSON (`request_json`, `ApiClient`, `load_service_apis`) | Timeout toujours posé ; `None` signifie « appel en échec » et **rien d'autre** — ne jamais le confondre avec une liste vide. |
| `ui.py` | `pin_edit` : la danse fetch/NotFound/send/pin/edit d'un message épinglé | Le **stockage de l'id reste chez l'appelant** : le migrer orphelinerait les messages épinglés existants et en ferait poster des doublons. |
| `bg.py` | `bg.spawn`, filet des `tasks.loop` (une exception non rattrapée arrête une boucle DÉFINITIVEMENT) | `guard_cog_loops` est appelé centralement : rien à refaire dans les cogs. |
| `pve.py` | API Proxmox asynchrone (`a*`), `apoll_task` | `outcome == "lost"` = **notre suivi** s'est arrêté, pas la tâche PVE. Rendu par `format.outcome_text`. |

`provision.py` est le **propriétaire** des catégories : il les crée avec leurs overwrites
et publie leurs ids dans `state["prov"]["categories"]` ; `core/channels.py` les relit pour
les autres cogs, jamais l'inverse.

## Côté Discord (à faire une fois)

1. https://discord.com/developers/applications → New Application → Bot → copier le **token**.
2. OAuth2 → URL Generator : scopes `bot` + `applications.commands` ; perms : Send Messages,
   Embed Links, Attach Files, Read Message History, Manage Messages (épingler),
   **Manage Channels + Manage Roles** (auto-provisioning des salons et des permissions).
   Inviter.
3. Activer le *Mode développeur* Discord pour copier `GUILD_ID`, votre user id, les ids de
   salons et de rôles.

## Commandes

**46 commandes de premier niveau** (dont 4 groupes : `/2fa`, `/backup`, `/gestion`,
`/logstream`). `/help` en donne la liste à jour, générée depuis l'arbre de commandes —
c'est elle qui fait foi, pas ce tableau. 🔒 = réservé aux tiers M/O.

- État & métriques : `/status` `/health` `/ping` `/node` `/ct` `/cts` `/graph`
- Stockage & matériel : `/storage` `/thinpool` `/raid` `/smart` `/temps` `/ipmi`
- Sauvegardes : `/backups`
- Logs & journaux : `/journal` `/logs` `/tail` `/tasks` `/logstream` 🔒 `/logsearch`
  `/ctlogs` `/apperrors`
- Dans l'invité : `/sys` `/df`
- Alertes & dashboard : `/alerts` `/dashboard` 🔒
- Seedbox & médias : `/ratio` `/setratio` 🔒 `/langues` `/film` `/serie`
- Docker & torrents : `/docker` 🔒 `/torrents` 🔒
- Téléchargements : `/yt` `/tw` `/musique` `/dl` `/yt-config`
- Assistant IA locale : `/assistant`
- Sécurité & accès : `/2fa setup|unlock|lock|duree|status|disable` · `/gestion add|remove|list` 🔒
- Actions : `/ctctl start|stop|restart` 🔒 · `/backup create|delete` 🔒 · `/audit` 🔒
- Divers : `/whoami` `/help`

## Salons provisionnés

`provision.py` crée et maintient lui-même la structure (permission « Gérer les salons ») :

- **📊 Supervision `<SERVER_KEY>`** : `#alertes`, `#rapports`, `#journaux-live`,
  `#materiel`, `#sauvegardes`, `#stockage`, `#seedbox`, `#nas`, `#reseau`, `#services`
  (embeds épinglés, édités sur place — pas de spam).
- **Gestion `<SERVER_KEY>`** : un salon par invité LXC/VM, réconcilié contre l'API PVE.
  Nouveau guest → nouveau salon ; guest disparu → **le salon reste exactement où il est**
  (l'archivage automatique a été retiré le 2026-07-18 à la demande de Nico et ne doit pas
  revenir) ; l'ordre des salons suit les VMID.
- **🔒 Lock `<SERVER_KEY>`** : `#{🟢|🟠|🔴}-pve` (l'hyperviseur) et `#jellyfin-logs`.
- **🗄️ Archive** : `#logs-2fa` (journal d'audit du 2FA).
- Même schéma pour les nœuds Aveyron (`📊 Supervision AVY-X`, `Gestion AVY-X`,
  `🔒 Lock AVY-X`) et pour le NAS (`📊 Supervision SYNO`, `🔒 Lock SYNO`).

## Salon « 🔒 Lock » — l'hyperviseur

Dans la catégorie **« 🔒 Lock `<SERVER_KEY>` »** (@everyone : *Voir le salon* refusé), le
salon **`{🟢|🟠|🔴}-pve`** porte un embed épinglé rafraîchi toutes les
`DASHBOARD_INTERVAL_MIN` min (uptime, CPU/charge, RAM, swap, `/`, stockages, invités, IPMI
températures/ventilateurs/conso, RAID, SMART, sauvegardes PBS, noyau) et trois boutons
persistants — **🔄 Rafraîchir**, **💾 Sauvegarder** (`vzdump --all`, avec confirmation),
**🖥️ Terminal** (shell root sur l'hôte). **Pas de Start/Stop/Reboot** : volontaire.

⚠️ **La permission du salon n'est pas la frontière de sécurité.** Un membre portant la
permission Discord « Administrateur » voit le salon malgré les overwrites — Discord ne
permet pas de les lui cacher. Ce qui protège réellement, c'est la porte des boutons :
🔄 et 💾 acceptent le tier **M** du serveur du nœud ; **🖥️ Terminal est réservé aux
`NODE_TERMINAL_OWNER_IDS` et au tier O** (`_may_open_node`), avec session 2FA exigée en
plus, même pour le propriétaire.

### Console root du nœud : qui y a droit, et pourquoi SSH plutôt que `termproxy`

**Qui.** `NODE_TERMINAL_OWNER_IDS` **ou** le rôle **O** du serveur du nœud
(`NODE_SERVER_KEY`) — le tier M est refusé.

> Correction 2026-08-11 : ce paragraphe annonçait une « porte propriétaire stricte,
> aucun rôle Discord n'y donne accès ». C'est FAUX depuis la refonte des rôles du
> 2026-07-16, qui a délibérément ouvert la console du nœud au tier O. C'est le texte qui
> avait dérivé, pas le code. Raison du choix (revue sécu du 2026-07-16) : le shell root du
> nœud permet `pct enter` vers n'importe quel conteneur, y compris ceux exclus de la
> console LXC (`vaultwarden`/`mailserver`/`bdd`) — d'où O/propriétaire seulement, jamais M.

**Pourquoi SSH.** PVE ne donne un shell root sur le **nœud** qu'à `root@pam`
(`PVE/API2/Nodes.pm`, `get_shell_command` : *« non-root must always login for now »*) ;
tout autre compte, même avec `Sys.Console`, tombe sur un prompt `login:`. Un **token**
d'API ne s'en sort pas non plus (l'utilisateur authentifié devient `root@pam!nom` ≠
`root@pam`). La voie termproxy imposait donc de stocker le mot de passe root de PVE dans
`config.env` — d'où, en cas de compromission de CT106, l'admin complet de l'API PVE,
console comprise sur les guests pourtant exclus.

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

La console des **guests** LXC est une autre porte : `termproxy` via le compte dédié
least-privilege `botconsole@pve`, ouverte aux `TERMINAL_OWNER_IDS` / `TERMINAL_OWNER_ROLE_IDS`,
avec les invités sensibles ré-exclus côté bot (`TERMINAL_EXCLUDED_GUESTS`).

## Flux de logs live (optionnel)

Le bot écoute en UDP/514 (si `LIVE_LOG_CHANNEL_ID` est renseigné, ou dès que provision a
créé `#journaux-live`). Pointer les émetteurs vers `10.3.10.106:514` :

- Hôte pve (rsyslog non installé par défaut) : `apt-get install -y rsyslog` puis
  `echo '*.warning @10.3.10.106:514' > /etc/rsyslog.d/90-discord-bot.conf && systemctl restart rsyslog`
- CRS305 / RouterOS : `/system logging action set remote target=10.3.10.106:514`
- Un CT au choix (logs applicatifs) : même ligne rsyslog → il apparaît dans le canal flux-logs.

Le pare-feu CT106 (`106.fw`, posé par `provision_ct106.sh` s'il est présent à côté des
sources) n'autorise UDP/514 que depuis l'hôte et le routeur.

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

Si `LOKI_URL` est renseigné, trois commandes de lecture interrogent l'API HTTP Loki
(labels figés : `host` = hostname machine, `unit` = unité systemd, `level` = vocabulaire
journald, `job` = `systemd-journal|jellyfin|caddy`) :

- `/logsearch [requete] [host] [unit] [level] [contains] [plage]` — recherche libre ;
  `requete` commençant par `{` = LogQL brut, sinon texte recherché. Plages : 15 min à 7 j.
- `/ctlogs <ct> [unit] [level] [plage]` — logs d'un conteneur (label `host` = nom du CT).
- `/apperrors <ct> [plage]` — erreurs (`level=~"err|crit|alert|emerg"`), avec repli
  texte `error` si aucun résultat.

Le rapport quotidien gagne un champ **Top logs (24h)** (top 5 hôtes par volume de
logs ≥ warning). Le pare-feu `106.fw` autorise la sortie TCP/3100 vers Loki.

## Surveillance du bot lui-même

- `/health` : état des sources (Influx/PVE/Loki) et **boucles de fond mortes ou vivantes**.
- `HEARTBEAT_URL` (healthchecks.io ou équivalent) : ping périodique — c'est ce qui prévient
  quand le bot, lui, n'est plus là pour prévenir. Vide = désactivé.

## Limites (volontaires)

- Pas de compteur de mises à jour APT dans l'embed du nœud : `GET /nodes/{node}/apt/update`
  exige `Sys.Modify`, permission bien trop large pour un compteur (le token du bot est
  `PVEAuditor` → 403).
- Pas de « Pics sur 24 h » pour le nœud (contrairement aux invités) : dans InfluxDB,
  `object=="nodes"` ne porte que `uptime`.
- Pas de miroir des alertes Grafana (Grafana garde le paging critique ; le bot couvre les trous).
- Logs applicatifs par-CT via forward rsyslog (le bot dans CT106 ne peut pas `pct exec`).
- Aucun `fstrim` ni aucun verbe destructeur en bouton (la suppression de sauvegarde passe
  par `/backup delete`, avec confirmation).
- Sur les nœuds **Aveyron**, le bot est en **lecture seule** côté infrastructure (choix de
  Nico) : supervision et alertes, pas d'administration.
- Quand l'hôte est éteint la nuit, CT106 (et le bot) le sont aussi ; reprise auto via `onboot=1`
  + rattrapage du rapport au réveil. L'alerting critique reste assuré par Grafana.

### `#vpn` (🔒 Lock)

État des VPN WireGuard, règle Nico 2026-08-30 : **tous les VPN se terminent sur le R820**
(`wg-vpn` udp/39671 nomades Pierre + PC Nico, `wg-avy` udp/39672 site-à-site Aveyron) ; le
MikroTik CRS305 (`wg0`, même clé) ne prend le relais que si son netwatch voit le R820 injoignable
(dst-nat udp/39671 désactivé, pair « Hub Aveyron » activé). Le cog `vpn` :

- exécute `tools/vpn-status` **sur l'hyperviseur** (clé SSH restreinte) toutes les 60 s ; c'est ce
  script qui lit `wg show … dump` et le MikroTik (mot de passe `/root/.mt_pw` jamais copié dans CT106) ;
- tient un message épinglé : mode PRIMAIRE / SECOURS, chaque pair (🟢 handshake < 3 min, 🟠 dernier
  handshake il y a …, ⚪ jamais vu depuis le démarrage de l'interface), endpoint, ping min/moy/max +
  perte depuis le R820, rx/tx cumulés et débit (delta entre relevés, ignoré si compteurs remis à zéro),
  hub Aveyron + PVE 10.0.10.10, MikroTik (netwatch, dst-nat, route 10.3.99.0/24, pairs wg0), IP WAN et
  cohérence DNS `nicov1.fr` ;
- poste un événement par transition (connexion, fin de session, changement d'endpoint, bascule,
  MikroTik (in)joignable, DNS ≠ WAN) ; relaie dans #alertes : mode secours, hub Aveyron sans handshake,
  MikroTik injoignable, DNS ≠ WAN ;
- `/vpn` (cap `services`) : le même tableau, éphémère. Lecture seule : rien n'est modifié.

### `#ptero-logs` (🔒 Lock)
Journaux Pterodactyl : panel CT101 (Laravel `storage/logs/laravel.log`, apache erreurs + HTTP ≥ 400,
via Alloy `/etc/alloy/20-pterodactyl.alloy`, `{job="pterodactyl"}`) et Wings CT100 (journald
`unit="wings.service"`, niveau lu dans le préfixe texte). Cog `pterodactyl_logs` (même modèle que
`dolibarr_logs`) : curseur Loki persistant, 40 lignes/cycle (30 s), INFO Wings limités aux événements
utiles (power/install/backup/SFTP), incidents (Laravel ≥ err, Wings ERROR/FATAL, 5xx, PHP Fatal) →
`#alertes` edge-trigger + « Résolu » après 60 min. Config : `PTERODACTYL_LOGS_ENABLED`,
`PTERODACTYL_LOGS_CHANNEL_ID`, `PTERODACTYL_LOGS_POLL_SECONDS`.

## Fonctionnalités « serveur Discord » (2026-08-30)

Reprises **en idée** du projet Ultra Suite (bot JS de Nico, archivé sur GitHub sous le tag
`ultra-suite-v2.1`) et réécrites pour Edmine : état JSON local, permissions G/M/O, 2FA, pas de
PostgreSQL/Redis. Ultra Suite avait le concept juste mais l'exécution cassée (table `guild_config`
absente de toute migration → 9 fichiers d'événements ne postaient jamais rien, backup sans
overwrites, jobs jamais branchés) : rien n'a été copié.

| Cog | Commandes | Ce que ça fait |
|---|---|---|
| `discord_logs` | `/journal-discord statut\|test` | `#discord-logs` (🔒 Lock) : messages supprimés/édités, arrivées/départs (kick/ban via journal d'audit), rôles, salons et **overwrites**, rôles et **permissions**, invitations, webhooks, threads, emojis, vocal ; signaux de sécurité relayés dans `#alertes` ; rôle de bienvenue `WELCOME_ROLE_ID`. Sans intent `members`, il dit lui-même ce qu'il ne voit pas. |
| `snapshot` | `/snapshot creer\|liste\|voir\|diff\|supprimer` | instantané JSON déterministe rôles/salons/**overwrites** + diff lisible ; quotidien 04:00 si changement, `#alertes` si des permissions bougent. Pas de restauration automatique (volontaire). Owner. |
| `sondage` | `/sondage creer\|fermer\|resultats\|liste` | sondages persistants (votes par personne, choix multiple, anonymat réel, clôture programmée idempotente, boutons survivant au redémarrage). |
| `rappel` | `/rappel creer\|liste\|supprimer` | rappels en salon ou DM, récurrence, anti-doublon au redémarrage, repli DM si le salon disparaît. |
| `faq` | `/faq voir\|ajouter\|modifier\|supprimer\|liste` | réponses enregistrées (VPN, Jellyfin…), lisibles par tout membre 2FA, mentions neutralisées. Gestion = M/O (cap `faq`). |
| `moderation` | `/purge`, `/lock`, `/unlock`, `/slowmode`, `/note` | modération légère (pas de ban/kick : serveur privé) ; `/unlock` restaure l'overwrite exact d'avant ; cap `moderation`. |

Intents privilégiés `DISCORD_INTENT_MEMBERS` / `DISCORD_INTENT_MESSAGE_CONTENT` : à cocher dans
le portail développeur **avant** de les passer à `true` (sinon le bot refuse de démarrer).
