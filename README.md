# Ultra Suite Bot v2.1

Bot Discord modulaire tout-en-un — **28 modules**, **90+ commandes slash**, architecture multi-serveur avec PostgreSQL, Redis, Lavalink, et dashboard web séparé.

Déployé sur **Proxmox LXC** avec PM2.

---

## Infrastructure

| LXC | Service | IP | Description |
|-----|---------|-----|-------------|
| 110 | **Bot** | 192.168.1.235 | Ultra Suite Bot (Node.js 20 + PM2) |
| 112 | **PostgreSQL 16** | 192.168.1.216 | Base de données (Knex ORM) |
| 113 | **Redis 7** | 192.168.1.217 | Cache, sessions, automod, économie |
| 114 | **Lavalink v4** | 192.168.1.218 | Serveur audio (Shoukaku) |
| 115 | **Dashboard** | 192.168.1.219 | Dashboard Web (Express + Nginx) |
| 103 | InfluxDB | 192.168.1.211 | Métriques (optionnel) |
| 104 | Grafana | 192.168.1.210 | Monitoring (optionnel) |

> Voir [oui/README.md](oui/README.md) pour le guide complet de déploiement Proxmox.

---

## Prérequis

| Outil | Version | Où |
|-------|---------|-----|
| Node.js | >= 20.0 | LXC 110 + 115 |
| PostgreSQL | >= 16 | LXC 112 |
| Redis | >= 7.0 | LXC 113 |
| Java | >= 17 | LXC 114 (Lavalink) |

---

## Installation rapide (dev local)

```bash
# 1. Installer les dépendances
npm install

# 2. Configurer l'environnement
cp .env.example .env

# 3. Exécuter les migrations
npm run migrate

# 4. Déployer les commandes slash
npm run deploy

# 5. Lancer le bot
npm start
```

---

## Déploiement Proxmox

```bash
# Sur l'hôte Proxmox
cd oui/ && chmod +x *.sh
bash 00_create_lxc.sh

pct exec 112 -- bash /root/setup.sh   # PostgreSQL
pct exec 113 -- bash /root/setup.sh   # Redis
pct exec 114 -- bash /root/setup.sh   # Lavalink
pct exec 115 -- bash /root/setup.sh   # Dashboard
pct exec 110 -- bash /root/setup.sh   # Bot (en dernier)
```

> Guide détaillé : [oui/README.md](oui/README.md)

---

## Configuration (.env)

| Variable | Description | Requis |
|----------|-------------|--------|
| `BOT_TOKEN` | Token du bot Discord | oui |
| `CLIENT_ID` | ID de l'application Discord | oui |
| `DB_HOST` | Hôte PostgreSQL | oui |
| `DB_PORT` | Port PostgreSQL (défaut: 5432) | |
| `DB_USER` | Utilisateur DB | oui |
| `DB_PASSWORD` | Mot de passe DB | oui |
| `DB_NAME` | Nom de la base | oui |
| `REDIS_HOST` | Hôte Redis | oui |
| `REDIS_PASSWORD` | Mot de passe Redis | oui |
| `LAVALINK_HOST` | Hôte Lavalink | oui |
| `LAVALINK_PASSWORD` | Mot de passe Lavalink | oui |
| `API_PORT` | Port de l'API REST (défaut: 3001) | |
| `OAUTH2_CLIENT_SECRET` | Secret OAuth2 pour dashboard | |
| `JWT_SECRET` | Secret JWT | |
| `DEFAULT_LOCALE` | Langue par défaut (fr/en) | |
| `LOG_LEVEL` | Niveau de log (info/debug/warn/error) | |

### Configuration en jeu

```
/setup                        Configuration guidée avec presets
/config view                  Voir toute la configuration
/config set                   Modifier une clé
/module list                  État des modules
/module enable/disable        Activer/désactiver un module
```

---

## Modules

| Module | Description | Commandes |
|--------|-------------|-----------|
| admin | Configuration & modules | `/module`, `/config`, `/setup` |
| moderation | Sanctions & gestion | `/ban`, `/kick`, `/warn`, `/timeout`, `/purge`, `/modlogs` |
| tickets | Support avancé | `/ticket`, `/ticketpanel` |
| logs | Journalisation complète | *20+ événements automatiques* |
| security | Automodération | `/automod` |
| antinuke | Protection anti-raid | `/antinuke` |
| onboarding | Bienvenue/au revoir | *Automatique* |
| xp | Niveaux & expérience | `/rank`, `/leaderboard`, `/xpadmin` |
| economy | Monnaie virtuelle | `/daily`, `/weekly`, `/balance`, `/pay`, `/rob`, `/work`, `/gamble`, `/shop` |
| roles | Rôles automatiques | `/rolemenu`, `/reactionrole`, `/autorole` |
| utility | Utilitaires (23) | `/ping`, `/help`, `/userinfo`, `/serverinfo`, `/poll`, `/suggest` |
| fun | Mini-jeux (9) | `/8ball`, `/meme`, `/ship`, `/trivia`, `/joke`, `/games` |
| music | Musique Lavalink | `/music` (17 sous-commandes) |
| stats | Statistiques | `/stats` |
| tempvoice | Vocaux temporaires | `/voice` |
| tags | FAQ/réponses rapides | `/tag` |
| announcements | Annonces | `/announce` |
| applications | Candidatures | `/apply` |
| events | Événements serveur | `/event` |
| custom_commands | Commandes custom | `/customcmd` |
| rp | Roleplay | `/rpprofile`, `/rpinventory` |
| giveaway | Giveaways | `/giveaway` |
| starboard | Starboard | `/starboard` |
| social | Profils & social | `/profile`, `/rep`, `/marry`, `/birthday` |
| verify | Vérification | `/verify` |
| forums | Gestion forums | `/forum` |
| backup | Sauvegarde serveur | `/backup` |
| premium | Premium tiers | `/premium` |
| integrations | Twitch/YouTube/RSS | `/integration` |
| polls | Sondages & suggestions | `/poll`, `/suggest` |

---

## Dashboard Web

Déployé séparément sur LXC 115 (`/opt/ultra-suite-dashboard`).

- Authentification Discord OAuth2
- Communication avec le bot via API REST
- Sessions stockées dans Redis (DB3)
- Gestion des modules par serveur
- Leaderboards XP & Économie
- Reverse proxy Nginx + SSL (Certbot)

Le dashboard a son propre `package.json` dans le dossier `dashboard/`.

---

## Base de données

PostgreSQL 16 via Knex ORM. Données séparées par `guild_id`. FK CASCADE.

- 5 migrations idempotentes (50+ tables)
- Retry exponentiel avec jitter
- Health monitoring + reconnexion auto
- Cache hybride Redis + NodeCache

```bash
npm run migrate              # Migrer
npm run migrate:rollback     # Rollback
```

---

## Redis (4 bases)

| DB | Usage |
|----|-------|
| 0 | Cache général (config guilds, cooldowns) |
| 1 | Automod (rate limits, anti-spam) |
| 2 | Économie (cache balances) |
| 3 | Dashboard (sessions OAuth2) |

---

## Scripts

```bash
npm start              # Lancer le bot
npm run deploy         # Commandes slash (global)
npm run deploy:dev     # Commandes slash (serveur de dev)
npm run migrate        # Migrations
```

---

## Architecture

```
index.js                      Point d'entrée
ecosystem.config.js           Configuration PM2
core/
  commandHandler.js           Chargement des commandes
  eventHandler.js             Chargement des événements
  componentHandler.js         Boutons, selects, modals
  configService.js            Cache config hybride (Redis + NodeCache)
  configEngine.js             Validation & migration config
  moduleRegistry.js           Registre des modules
  permissionEngine.js         Permissions par module/commande
  templateEngine.js           Templates messages/embeds
  scheduler.js                Tâches planifiées (CRON)
  logger.js                   Winston logger structuré
  i18n.js                     Traductions (FR/EN)
  api.js                      API REST (pour le dashboard)
  redis.js                    Client Redis multi-DB (ioredis)
  lavalink.js                 Lavalink v4 (Shoukaku)
commands/                     90+ slash commands (par module)
components/                   Handlers d'interactions UI
events/                       19 événements Discord
modules/                      28 manifestes de modules
database/                     Knex ORM + PostgreSQL + migrations
dashboard/                    Dashboard séparé
  server.js                   Serveur standalone
  package.json                Dépendances propres
locales/                      FR + EN
oui/                          Scripts infra Proxmox LXC
utils/                        Helpers (embeds, formatters, perms)
```

---

MIT License
