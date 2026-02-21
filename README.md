# 🚀 Ultra Suite v2.0

Bot Discord modulaire tout-en-un — **28 modules**, **70+ commandes slash**, **200+ sous-commandes**, architecture multi-serveur avec base de données optimisée, dashboard web, Docker ready.

---

## 📋 Table des matières

- [Fonctionnalités](#-fonctionnalités)
- [Prérequis](#-prérequis)
- [Installation](#-installation)
- [Docker](#-docker)
- [Configuration](#-configuration)
- [Architecture](#-architecture)
- [Modules](#-modules)
- [Commandes](#-commandes)
- [Dashboard Web](#-dashboard-web)
- [Base de données](#-base-de-données)
- [Tests](#-tests)
- [Développement](#-développement)

---

## ✨ Fonctionnalités

### Infrastructure
- **Multi-serveur** : configuration indépendante par serveur avec cache mémoire
- **28 modules** activables/désactivables individuellement par serveur
- **70+ commandes slash** avec sous-commandes, autocomplete et modals
- **Dashboard web** : Express + Passport Discord OAuth2 + SPA
- **Docker** : Dockerfile multi-stage + docker-compose (bot + MySQL + Redis)
- **CI/CD** : GitHub Actions (lint, tests, build, Docker)
- **i18n** : français + anglais (200+ clés de traduction)
- **API REST** : healthcheck, stats, gestion de config

### Modération
- **Sanctions avancées** : ban, kick, warn, timeout, softban, quarantine, massban
- **Lockdown** : verrouillage simultané de tous les channels
- **Modération vocale** : mute, deafen, disconnect, move all
- **Notes** : système de notes invisibles par l'utilisateur
- **Case system** : numérotation séquentielle, historique, DM automatiques
- **Automod** : anti-spam, anti-lien, anti-mention, filtres regex/mots/domaines

### Engagement
- **XP & Niveaux** : cooldown, rôles récompenses, leaderboard paginé
- **Économie** : daily/weekly avec streaks, boutique, vol, work (13 métiers), casino (5 jeux)
- **Giveaways** : création, reroll, multi-gagnants, boutons interactifs
- **Starboard** : automatic star board avec threshold configurable
- **Sondages** : vote par boutons, multi-choix, timer
- **Suggestions** : upvote/downvote, statuts (approuvé/refusé/considéré)

### Support
- **Tickets** : panneaux, transcripts HTML, priorités, catégories, blacklist, stats
- **Vérification** : 4 modes (bouton, captcha, rules, question/réponse)
- **Tags/FAQ** : réponses rapides avec autocomplete et compteur

### Social
- **Profils** : bio, couleur, badges automatiques, anniversaires
- **Réputation** : système de rep avec cooldown
- **Mariages** : propose, accepte, divorce avec confirmation
- **Anniversaires** : liste automatique avec tri

### Systèmes
- **Musique** : YouTube, Spotify, SoundCloud, playlists, paroles
- **Reaction roles** : normal, unique, required + auto-roles (member/bot/human)
- **Vocaux temporaires** : création auto + gestion propriétaire
- **Événements** : RSVP avec boutons, max participants, statuts
- **Forums** : templates, auto-tag, auto-react
- **Intégrations** : Twitch, YouTube, RSS

### Sécurité
- **Anti-nuke** : détection mass-delete, mass-ban, emergency lockdown
- **Backup** : sauvegarde/restauration complète du serveur
- **Premium** : 4 tiers avec fonctionnalités exclusives

---

## 📦 Prérequis

| Outil | Version |
|-------|---------|
| Node.js | ≥ 20.0 |
| npm | ≥ 9.0 |
| MySQL | ≥ 8.0 |
| Redis | ≥ 7.0 (optionnel) |
| FFmpeg | Pour la musique |

---

## 🔧 Installation

```bash
# 1. Cloner le repo
git clone https://github.com/votre-user/ultra-suite.git
cd ultra-suite

# 2. Installer les dépendances
npm install

# 3. Copier et configurer l'environnement
cp .env.example .env
# Éditer .env avec vos tokens et identifiants

# 4. Exécuter les migrations
npm run migrate

# 5. Déployer les commandes slash
npm run deploy

# 6. Lancer le bot
npm start
```

---

## 🐳 Docker

```bash
# Démarrer avec Docker Compose (bot + MySQL + Redis)
docker-compose up -d

# Voir les logs
docker-compose logs -f bot

# Arrêter
docker-compose down
```

---

## ⚙️ Configuration

### Variables d'environnement (.env)

| Variable | Description | Requis |
|----------|-------------|--------|
| `BOT_TOKEN` | Token du bot Discord | ✅ |
| `CLIENT_ID` | ID de l'application Discord | ✅ |
| `DB_HOST` | Hôte de la base de données | ✅ |
| `DB_PORT` | Port (défaut: 3306) | |
| `DB_USER` | Utilisateur DB | ✅ |
| `DB_PASSWORD` | Mot de passe DB | ✅ |
| `DB_NAME` | Nom de la base | ✅ |
| `REDIS_HOST` | Hôte Redis (optionnel) | |
| `API_PORT` | Port du dashboard (défaut: 3000) | |
| `OAUTH2_CLIENT_SECRET` | Secret OAuth2 pour dashboard | |
| `OPENWEATHER_API_KEY` | Clé API OpenWeatherMap | |
| `PERSPECTIVE_API_KEY` | Clé API Perspective (automod) | |
| `SENTRY_DSN` | DSN Sentry (monitoring) | |

### Configuration en jeu

```
/setup        → Configuration guidée avec presets
/config view  → Voir toute la configuration
/config set   → Modifier une clé de config
/module list  → État des modules
/module enable/disable → Activer/désactiver un module
```

---

## 🏗️ Architecture

```
ultra-suite/
├── index.js                 # Point d'entrée — boot, login, handlers
├── deploy.js                # Déploiement des commandes slash
├── package.json
├── .env.example
│
├── core/
│   ├── configService.js     # Cache config multi-serveur
│   ├── commandHandler.js    # Chargement récursif des commandes
│   ├── eventHandler.js      # Chargement des événements
│   ├── componentHandler.js  # Chargement des composants (boutons/selects)
│   ├── logger.js            # Winston logger avec rotation
│   ├── i18n.js              # Système de traduction
│   ├── scheduler.js         # Tâches planifiées
│   ├── api.js               # API REST optionnelle
│   └── tasks/
│       ├── reminderTask.js
│       ├── tempbanTask.js
│       ├── tempvoiceTask.js
│       └── eventCleanupTask.js
│
├── database/
│   ├── index.js             # Pool Knex + healthcheck + reconnexion auto
│   ├── guildQueries.js      # Requêtes guild helpers (CRUD, export/import)
│   ├── queryHelpers.js      # Helpers multi-serveur (users, sanctions, logs, leaderboards)
│   ├── knexfile.js          # Config MySQL multi-serveur
│   └── migrations/
│       ├── 001_initial_schema.js
│       ├── 002_extended_tables.js
│       └── 003_modules_tables.js
│
├── events/
│   ├── guildCreate.js
│   ├── guildDelete.js
│   ├── guildMemberAdd.js
│   ├── guildMemberRemove.js
│   ├── guildMemberUpdate.js
│   ├── messageCreate.js
│   ├── messageDelete.js
│   ├── messageUpdate.js
│   └── voiceStateUpdate.js
│
├── commands/
│   ├── admin/
│   │   ├── module.js         # /module list|enable|disable
│   │   ├── config.js         # /config view|set|reset
│   │   └── setup.js          # /setup (wizard avec presets)
│   ├── moderation/
│   │   ├── ban.js            # /ban (perma + tempban)
│   │   ├── kick.js           # /kick
│   │   ├── warn.js           # /warn (auto-action)
│   │   ├── timeout.js        # /timeout
│   │   ├── sanctions.js      # /sanctions user|case|clear
│   │   ├── unban.js          # /unban
│   │   ├── purge.js          # /purge (filtres avancés)
│   │   ├── slowmode.js       # /slowmode
│   │   ├── lock.js           # /lock on|off
│   │   └── note.js           # /note add|list|delete
│   ├── tickets/
│   │   ├── ticket.js         # /ticket create|close|add|remove|claim
│   │   └── ticketpanel.js    # /ticketpanel
│   ├── xp/
│   │   ├── rank.js           # /rank
│   │   ├── leaderboard.js    # /leaderboard
│   │   └── xpadmin.js        # /xpadmin set|add|remove|reset|config
│   ├── economy/
│   │   ├── daily.js          # /daily (streaks)
│   │   ├── weekly.js         # /weekly
│   │   ├── balance.js        # /balance
│   │   ├── pay.js            # /pay
│   │   ├── rob.js            # /rob (risque/récompense)
│   │   ├── shop.js           # /shop list|buy|add|remove
│   │   ├── ecoleaderboard.js # /richest
│   │   └── ecoadmin.js       # /ecoadmin give|take|set|reset|config
│   ├── security/
│   │   └── automod.js        # /automod status|toggle|filter-*|config
│   ├── tags/
│   │   └── tag.js            # /tag use|create|edit|delete|list|info
│   ├── stats/
│   │   └── stats.js          # /stats overview|members|messages|moderation
│   ├── utility/
│   │   ├── userinfo.js       # /userinfo
│   │   ├── serverinfo.js     # /serverinfo
│   │   ├── help.js           # /help (dynamique)
│   │   ├── ping.js           # /ping (latence + santé)
│   │   ├── avatar.js         # /avatar
│   │   ├── embed.js          # /embed
│   │   ├── announce.js       # /announce
│   │   ├── reminder.js       # /reminder set|list|delete
│   │   └── tempvoice.js      # /voice name|limit|lock|unlock|invite|kick
│   ├── fun/
│   │   └── fun.js            # /fun 8ball|coinflip|dice|rps|rate|hug
│   ├── roles/
│   │   └── rolemenu.js       # /rolemenu create|add|remove|send
│   ├── applications/
│   │   └── apply.js          # /apply submit|setup|list
│   ├── events/
│   │   └── event.js          # /event create|list|cancel|info
│   ├── customcmd/
│   │   └── customcmd.js      # /customcmd create|edit|delete|list
│   └── rp/
│       ├── rpprofile.js      # /rpprofile create|view|edit|delete|list
│       └── rpinventory.js    # /rpinventory view|give|use
│
├── components/
│   ├── tickets/
│   │   └── ticket-buttons.js
│   ├── roles/
│   │   └── rolemenu-select.js
│   ├── help/
│   │   └── help-select.js
│   ├── applications/
│   │   └── application-handlers.js
│   └── events/
│       └── event-buttons.js
│
└── locales/
    ├── fr.json
    └── en.json
```

---

## 📦 Modules

| Module | Description | Commandes principales |
|--------|-------------|-----------|
| ⚙️ admin | Configuration & modules | `/module`, `/config`, `/setup` |
| 🔨 moderation | Sanctions & gestion | `/ban`, `/kick`, `/warn`, `/timeout`, `/softban`, `/lockdown`, `/quarantine`, `/massban`, `/purge`, `/modlogs` |
| 🎫 tickets | Support avancé | `/ticket create\|close\|transcript\|rename\|priority\|transfer\|blacklist\|stats`, `/ticketpanel` |
| 📋 logs | Journalisation complète | *20+ événements automatiques* |
| 🔒 security | Automodération | `/automod` |
| 🛡️ antinuke | Protection anti-raid | `/antinuke enable\|disable\|whitelist\|threshold\|emergency` |
| 👋 onboarding | Bienvenue/au revoir | *Automatique via events* |
| ⭐ xp | Niveaux & expérience | `/rank`, `/leaderboard`, `/xpadmin` |
| 💰 economy | Monnaie virtuelle | `/daily`, `/weekly`, `/balance`, `/pay`, `/rob`, `/work`, `/gamble`, `/shop`, `/ecoadmin` |
| 🎭 roles | Rôles automatiques | `/rolemenu`, `/reactionrole`, `/autorole` |
| 🔧 utility | Utilitaires (15+) | `/ping`, `/help`, `/userinfo`, `/serverinfo`, `/poll`, `/suggest`, `/afk`, `/translate`, `/weather`, etc. |
| 🎮 fun | Mini-jeux (7+) | `/8ball`, `/meme`, `/ship`, `/trivia`, `/joke`, `/games`, `/mock`, `/say` |
| 🎵 music | Musique complète | `/music play\|pause\|skip\|queue\|volume\|loop\|shuffle\|lyrics\|playlist` |
| 📊 stats | Statistiques | `/stats overview\|members\|messages\|moderation` |
| 🔊 tempvoice | Vocaux temporaires | `/voice name\|limit\|lock\|unlock\|invite\|kick` |
| 🏷️ tags | FAQ/réponses rapides | `/tag use\|create\|edit\|delete\|list` |
| 📢 announcements | Annonces | `/announce` |
| 📝 applications | Candidatures | `/apply submit\|setup\|list` |
| 🎉 events | Événements serveur | `/event create\|list\|cancel\|info` |
| ⚡ custom_commands | Commandes custom | `/customcmd create\|edit\|delete\|list` |
| 🎭 rp | Roleplay | `/rpprofile`, `/rpinventory` |
| 🎁 giveaway | Giveaways | `/giveaway create\|end\|reroll\|list\|delete` |
| ⭐ starboard | Starboard | `/starboard setup\|threshold\|channel\|stats` |
| 👤 social | Profils & social | `/profile`, `/rep`, `/marry`, `/birthday` |
| ✅ verify | Vérification | `/verify setup\|panel\|config\|stats` |
| 📁 forums | Gestion forums | `/forum setup\|template\|config\|lock\|stats` |
| 💾 backup | Sauvegarde serveur | `/backup create\|list\|info\|delete\|load` |
| ⭐ premium | Premium tiers | `/premium status\|features\|activate` |
| 🔌 integrations | Twitch/YouTube/RSS | `/integration twitch\|youtube\|rss` |
| 📊 polls | Sondages & suggestions | `/poll`, `/suggest` |

---

## 🌐 Dashboard Web

Le dashboard est accessible à `http://localhost:3000` (ou le port `API_PORT`).

**Fonctionnalités :**
- Authentification Discord OAuth2
- Vue d'ensemble du bot (guilds, users, uptime, mémoire)
- Liste des serveurs gérables
- Activation/désactivation des modules par serveur
- Configuration de chaque serveur (channels, rôles, paramètres)
- Leaderboards XP & Économie
- Statistiques par serveur

**Configuration :** Ajouter `OAUTH2_CLIENT_SECRET` et `CLIENT_ID` dans `.env`.

---

## 🗄️ Base de données

### Architecture multi-serveur

Un seul pool de connexions MySQL, les données séparées par `guild_id` dans chaque table. Les FK CASCADE assurent le nettoyage automatique quand une guild est supprimée.

**Fonctionnalités du layer DB :**
- Retry exponentiel avec jitter à l'initialisation
- Health monitoring périodique (60s) avec reconnexion automatique
- Migration lock cleanup (récupération après crash)
- Transaction helper pour les opérations atomiques
- Query helpers : pagination, bulk insert, leaderboards
- Export/Import de configuration par guild

### 5 migrations (idempotentes)

**001** — Tables fondamentales : `guilds`, `guild_config`, `guild_modules`, `users`, `sanctions`, `tickets`, `transactions`, `daily_metrics`

**002** — Tables étendues : `tags`, `shop_items`, `role_menus`, `mod_notes`, `automod_filters`, `security_signals` + ALTER `users` + `sanctions`

**003** — Tables modules : `applications`, `server_events`, `custom_commands`, `rp_characters`, `rp_inventory`, `reminders`, `temp_voice_channels`, `logs`

**004** — Config system : tables config avancées

**005** — Full features : `giveaways`, `starboard_*`, `verification_config`, `polls`, `suggestions`, `social_profiles`, `playlists`, `backups`, `premium_guilds`, `promo_codes`, `antinuke_*`, `afk_users`, `sticky_messages`, `auto_responders`, `invite_tracking`, `persistent_roles`, `warn_config`, `forum_config`, `ticket_blacklist`, `work_cooldowns`, `gamble_history` + ALTERs

### Commandes DB

```bash
npm run migrate              # Migrer
npm run migrate:rollback     # Rollback
npx knex migrate:status --knexfile database/knexfile.js  # Status
```

### Variables DB (.env)

| Variable | Description | Défaut |
|----------|-------------|--------|
| `DB_HOST` | Hôte MySQL | `127.0.0.1` |
| `DB_PORT` | Port MySQL | `3306` |
| `DB_USER` | Utilisateur | `root` |
| `DB_PASSWORD` | Mot de passe | |
| `DB_NAME` | Base de données | `ultra_suite` |
| `DB_POOL_MAX` | Max connexions pool | `10` |
| `DB_MAX_RETRIES` | Tentatives de connexion | `7` |
| `DB_HEALTH_INTERVAL` | Intervalle health check (ms) | `60000` |
| `DB_SSL` | Activer SSL | `false` |
| `DB_DEBUG` | Mode debug SQL | `false` |

---

## 🛠️ Développement

### Scripts npm

```bash
npm start              # Lancer le bot
npm run dev            # Mode développement (watch)
npm test               # Lancer les tests
npm run test:coverage  # Tests avec couverture
npm run lint           # Vérifier le code
npm run lint:fix       # Corriger automatiquement
npm run migrate        # Exécuter les migrations
npm run deploy         # Déployer les commandes slash
npm run docker:up      # Démarrer Docker
npm run validate       # Valider toutes les commandes
```

### Ajouter une commande

1. Créer un fichier dans `commands/<module>/macommande.js`
2. Exporter : `module`, `data` (SlashCommandBuilder), `execute(interaction)`
3. Relancer `npm run deploy`
4. Restart le bot

### Ajouter un composant

1. Créer un fichier dans `components/<module>/mon-composant.js`
2. Exporter : `prefix` ou `customId` ou `customIds` (array), `type`, `execute(interaction)`

### Ajouter une tâche planifiée

1. Créer un fichier dans `core/tasks/maTache.js`
2. Exporter : `name`, `interval` (ms), `execute(client)`
3. Le scheduler les charge automatiquement au boot

### Ajouter une locale

1. Créer/modifier `locales/xx.json` en suivant la structure de `fr.json`
2. Utiliser `t(guildId, 'key.subkey', { var: 'value' })` dans les commandes

---

## 🧪 Tests

```bash
npm test               # Lancer tous les tests
npm run test:coverage  # Avec rapport de couverture
npm run test:watch     # Mode watch
```

**Suites de tests :**
- `tests/core/` — Config, modules, i18n, commands, components
- `tests/utils/` — Formatters, embeds, permissions
- `tests/locales/` — Validation des fichiers de traduction
- `tests/modules/` — Validation des manifestes

---

## 📊 Statistiques du projet

| Métrique | Valeur |
|----------|--------|
| Fichiers JS | ~130 |
| Commandes slash | 70+ |
| Sous-commandes | ~200 |
| Modules | 28 |
| Tables DB | 50+ |
| Migrations | 5 |
| Locales | 2 (FR, EN) |
| Clés de traduction | 200+ |
| Événements Discord | 20+ |
| Composants UI | 10+ |
| Tests | 8 suites |
| Tâches planifiées | 4+ |

---

## 📄 Licence

MIT — Usage libre, attribution appréciée.

---

*Ultra Suite v2.0 — Développé avec discord.js v14, Knex.js, Express — Docker & CI/CD ready*