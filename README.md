# 🚀 Ultra Suite v2.0

Bot Discord modulaire tout-en-un — **19 modules**, **35+ commandes slash**, **architecture multi-serveur** avec base de données, système de configuration par serveur, et déploiement Docker.

---

## 📋 Table des matières

- [Fonctionnalités](#-fonctionnalités)
- [Prérequis](#-prérequis)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Déploiement Docker](#-déploiement-docker)
- [Architecture](#-architecture)
- [Modules](#-modules)
- [Commandes](#-commandes)
- [Base de données](#-base-de-données)
- [Développement](#-développement)

---

## ✨ Fonctionnalités

- **Multi-serveur** : configuration indépendante par serveur avec cache mémoire
- **19 modules** activables/désactivables individuellement par serveur
- **35+ commandes slash** avec sous-commandes, autocomplete et modals
- **Automod** : anti-spam, anti-lien, anti-mention, filtres regex/mots/domaines
- **Système de sanctions** : case system avec numérotation séquentielle, historique, DM
- **XP & Niveaux** : cooldown, rôles récompenses, leaderboard paginé
- **Économie** : monnaie virtuelle, daily/weekly avec streaks, boutique, vol, classement
- **Tickets** : panel avec boutons persistants, claim, permissions dynamiques
- **Rôles** : menus de rôles avec select menu persistant
- **Tags/FAQ** : réponses rapides avec autocomplete et compteur d'utilisation
- **Candidatures** : formulaire modal, review par boutons accept/reject, DM
- **Événements** : RSVP avec boutons, max participants, statuts
- **RP** : fiches personnage, inventaire, système MJ
- **Commandes custom** : triggers texte personnalisés par serveur
- **Vocaux temporaires** : création auto + gestion propriétaire (lock/rename/kick)
- **Stats** : dashboard serveur, métriques quotidiennes, graphiques ASCII
- **Rappels** : système de rappels personnels avec durées flexibles
- **i18n** : français + anglais avec système de traduction extensible
- **API REST** : healthcheck et endpoints stats (optionnel)

---

## 📦 Prérequis

| Outil | Version |
|-------|---------|
| Node.js | ≥ 18.0 |
| npm | ≥ 9.0 |
| MariaDB / MySQL | ≥ 10.6 / 8.0 |

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
npx knex migrate:latest --knexfile database/knexfile.js

# 5. Déployer les commandes slash
node deploy.js

# 6. Lancer le bot
node index.js
```

---

## ⚙️ Configuration

### Variables d'environnement (.env)

| Variable | Description | Requis |
|----------|-------------|--------|
| `BOT_TOKEN` | Token du bot Discord | ✅ |
| `CLIENT_ID` | ID de l'application Discord | ✅ |
| `GUILD_ID` | ID du serveur de dev (commandes locales) | |
| `DB_HOST` | Hôte de la base de données | ✅ |
| `DB_PORT` | Port (défaut: 3306) | |
| `DB_USER` | Utilisateur DB | ✅ |
| `DB_PASSWORD` | Mot de passe DB | ✅ |
| `DB_NAME` | Nom de la base (défaut: ultrasuite) | ✅ |
| `NODE_ENV` | Environnement (development/production) | |
| `DEFAULT_LOCALE` | Langue par défaut (fr/en) | |
| `OWNER_ID` | ID du propriétaire du bot | |

### Configuration en jeu

```
/setup        → Configuration guidée avec presets
/config view  → Voir toute la configuration
/config set   → Modifier une clé de config
/module list  → État des modules
/module enable/disable → Activer/désactiver un module
```

---

## 🐳 Déploiement Docker

```bash
# Démarrer le bot + MariaDB
docker compose up -d

# Voir les logs
docker compose logs -f bot

# Arrêter
docker compose down

# Rebuild après modification
docker compose up -d --build
```

Variables dans `.env` :
- `DB_PASSWORD` : mot de passe MariaDB (défaut: ultrasuite)
- `DB_ROOT_PASSWORD` : mot de passe root MariaDB
- `DB_EXTERNAL_PORT` : port externe MariaDB (défaut: 3307)

---

## 🏗️ Architecture

```
ultra-suite/
├── index.js                 # Point d'entrée — boot, login, handlers
├── deploy.js                # Déploiement des commandes slash
├── package.json
├── .env.example
├── Dockerfile
├── docker-compose.yml
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
│   ├── index.js             # Pool Knex + healthcheck
│   ├── guildQueries.js      # Requêtes guild helpers
│   ├── knexfile.js
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

| Module | Description | Commandes |
|--------|-------------|-----------|
| ⚙️ admin | Configuration & modules | `/module`, `/config`, `/setup` |
| 🔨 moderation | Sanctions & gestion | `/ban`, `/kick`, `/warn`, `/timeout`, `/sanctions`, `/unban`, `/purge`, `/slowmode`, `/lock`, `/note` |
| 🎫 tickets | Support par tickets | `/ticket`, `/ticketpanel` |
| 📋 logs | Journalisation | *Automatique via events* |
| 🔒 security | Automodération | `/automod` |
| 👋 onboarding | Bienvenue/au revoir | *Automatique via events* |
| ⭐ xp | Niveaux & expérience | `/rank`, `/leaderboard`, `/xpadmin` |
| 💰 economy | Monnaie virtuelle | `/daily`, `/weekly`, `/balance`, `/pay`, `/rob`, `/shop`, `/richest`, `/ecoadmin` |
| 🎭 roles | Menus de rôles | `/rolemenu` |
| 🔧 utility | Utilitaires | `/userinfo`, `/serverinfo`, `/help`, `/ping`, `/avatar`, `/embed`, `/announce`, `/reminder`, `/voice` |
| 🎮 fun | Mini-jeux | `/fun` (8ball, coinflip, dice, rps, rate, hug) |
| 📊 stats | Statistiques | `/stats` |
| 🔊 tempvoice | Vocaux temporaires | `/voice` |
| 🏷️ tags | FAQ/réponses rapides | `/tag` |
| 📢 announcements | Annonces | `/announce` |
| 📝 applications | Candidatures | `/apply` |
| 🎉 events | Événements serveur | `/event` |
| ⚡ custom_commands | Commandes custom | `/customcmd` |
| 🎭 rp | Roleplay | `/rpprofile`, `/rpinventory` |

---

## 🗄️ Base de données

### 3 migrations

**001** — Tables fondamentales : `guilds`, `guild_config`, `guild_modules`, `users`, `sanctions`, `tickets`, `transactions`, `daily_metrics`

**002** — Tables étendues : `tags`, `shop_items`, `role_menus`, `mod_notes`, `automod_filters`, `security_signals` + ALTER `users` + `sanctions`

**003** — Tables modules : `applications`, `server_events`, `custom_commands`, `rp_characters`, `rp_inventory`, `reminders`, `temp_voice_channels`, `logs`

### Commandes Knex

```bash
# Migrer
npx knex migrate:latest --knexfile database/knexfile.js

# Rollback
npx knex migrate:rollback --knexfile database/knexfile.js

# Status
npx knex migrate:status --knexfile database/knexfile.js
```

---

## 🛠️ Développement

### Ajouter une commande

1. Créer un fichier dans `commands/<module>/macommande.js`
2. Exporter : `module`, `data` (SlashCommandBuilder), `execute(interaction)`
3. Relancer `node deploy.js`
4. Restart le bot

### Ajouter un composant

1. Créer un fichier dans `components/<module>/mon-composant.js`
2. Exporter : `prefix`, `type` (button/select/mixed), `execute(interaction)`
3. Le prefix doit correspondre au début du `customId` du composant

### Ajouter une tâche planifiée

1. Créer un fichier dans `core/tasks/maTache.js`
2. Exporter : `name`, `interval` (ms), `execute(client)`
3. Le scheduler les charge automatiquement au boot

### Ajouter une locale

1. Créer `locales/xx.json` en suivant la structure de `fr.json`
2. Utiliser `t('key.subkey', { var: 'value' })` dans les commandes

---

## 📊 Statistiques du projet

| Métrique | Valeur |
|----------|--------|
| Fichiers | ~85 |
| Commandes slash | 35+ |
| Sous-commandes | ~100 |
| Modules | 19 |
| Tables DB | 18 |
| Migrations | 3 |
| Locales | 2 (FR, EN) |
| Tâches planifiées | 4 |
| Composants UI | 5 |

---

## 📄 Licence

MIT — Usage libre, attribution appréciée.

---

*Ultra Suite v2.0 — Développé avec discord.js v14 & Knex.js*