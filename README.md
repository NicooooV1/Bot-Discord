<div align="center">

# 🚀 Ultra Suite Bot — v2.0

Bot Discord modulaire tout-en-un développé avec **discord.js v14** : 22 modules activables indépendamment, architecture Pterodactyl-ready, SQLite embarqué.

[![Discord.js](https://img.shields.io/badge/discord.js-v14-5865F2?logo=discord&logoColor=white)](https://discord.js.org/)
[![Node.js](https://img.shields.io/badge/Node.js-20+-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![SQLite](https://img.shields.io/badge/SQLite-better--sqlite3-003B57?logo=sqlite&logoColor=white)](https://github.com/WiseLibs/better-sqlite3)

</div>

---

## 📑 Table des matières

- [Modules](#-modules)
- [Architecture](#-architecture)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Structure du projet](#-structure-du-projet)
- [Commandes](#-commandes)
- [Déploiement Pterodactyl](#-déploiement-pterodactyl)
- [Contribuer](#-contribuer)
- [Licence](#-licence)

---

## ✨ Modules

Chaque module est **activable/désactivable indépendamment** via `/setup module`.

| # | Module | Commandes | Description |
|---|--------|-----------|-------------|
| 1 | 🔨 **Modération** | ban, kick, warn, timeout, unban, clear, slowmode, lock, nick, modlogs, warnings, case | Système complet avec cases, auto-actions, tempban |
| 2 | 🎫 **Tickets** | ticket (panel, open, close, add, remove, assign) | Tickets support avec transcript, notation |
| 3 | 📋 **Logs** | — (événements automatiques) | Messages, membres, voix, modération |
| 4 | 🛡️ **Sécurité** | — (automatique) | Anti-spam, anti-lien, anti-mass-mention |
| 5 | 👋 **Onboarding** | — (automatique) | Bienvenue, départ, auto-rôle |
| 6 | ⭐ **XP & Niveaux** | rank, leaderboard | Niveaux, rôles récompenses, classement |
| 7 | 💰 **Économie** | balance, daily, pay, bank | Monnaie virtuelle, banque, transactions |
| 8 | 🎭 **Rôles** | rolemenu (create, add, send) | Menus de rôles auto-assignables (boutons/select) |
| 9 | 🔧 **Utilitaire** | serverinfo, userinfo, avatar, reminder, ping | Outils pratiques |
| 10 | 🎮 **Fun** | poll, 8ball, coinflip, dice | Mini-jeux et sondages |
| 11 | 🏷️ **Tags / FAQ** | tag (show, create, delete, list) | Réponses pré-enregistrées avec autocomplete |
| 12 | 📢 **Annonces** | announce | Embeds personnalisés dans n'importe quel salon |
| 13 | 📊 **Statistiques** | stats | Métriques bot & serveur temps réel |
| 14 | 🔊 **TempVoice** | tempvoice (name, limit, lock, unlock, permit, reject) | Salons vocaux temporaires personnalisables |
| 15 | 📝 **Candidatures** | apply (start, review, accept, deny) | Système de candidatures avec formulaire modal |
| 16 | 🎉 **Événements** | event (create, info, list, cancel) | Événements avec inscription par bouton |
| 17 | ⚙️ **Commandes custom** | customcmd (create, delete, list) | Commandes personnalisées par serveur |
| 18 | 🎵 **Musique** | music (play, stop, skip, queue, pause, volume) | Préparé pour intégration future |
| 19 | 🎭 **RP** | rp (create, profile, delete) | Fiches personnage jeu de rôle |
| 20 | ⚙️ **Admin** | setup, help | Configuration complète du bot |

---

## 🏗️ Architecture

```
JavaScript pur (CommonJS) — Pas de TypeScript, pas de build step
├── discord.js v14           — Framework Discord
├── better-sqlite3 + Knex    — Base de données locale (WAL mode)
├── node-cron                — Tâches planifiées (sanctions, rappels, nettoyage)
├── node-cache               — Cache mémoire TTL pour configs
├── winston                  — Logging rotatif fichier + console
├── express                  — API REST optionnelle (health check)
└── Pterodactyl-ready        — Single process, pas de Docker-in-Docker
```

**Zéro service externe** — SQLite embarqué, pas de Redis, pas de PostgreSQL.

---

## 🚀 Installation

### 1. Cloner le dépôt

```bash
git clone https://github.com/NicooooV1/Bot-Discord.git
cd Bot-Discord
```

### 2. Installer les dépendances

```bash
npm install
```

### 3. Configurer les variables d'environnement

```bash
cp .env.example .env
```

Éditez `.env` :

```env
BOT_TOKEN=votre_token_ici
CLIENT_ID=votre_client_id_ici
GUILD_ID=votre_guild_id_ici   # Dev = instantané, vide = global (~1h)
NODE_ENV=production
LOG_LEVEL=info
DEFAULT_LOCALE=fr
```

> ⚠️ **Ne partagez JAMAIS votre token !** Le fichier `.env` est exclu via `.gitignore`.

### 4. Déployer les commandes slash

```bash
npm run deploy
```

### 5. Lancer le bot

```bash
npm start
```

La base de données SQLite et les tables sont créées automatiquement au premier lancement.

### Intents requis (Developer Portal > Bot)

- ✅ **Presence Intent**
- ✅ **Server Members Intent**
- ✅ **Message Content Intent**

> **Lien d'invitation** (remplacez `CLIENT_ID`) :
> ```
> https://discord.com/api/oauth2/authorize?client_id=CLIENT_ID&permissions=1632113078534&scope=bot%20applications.commands
> ```

---

## ⚙️ Configuration

### Configuration par serveur via `/setup`

```
/setup module moderation true     → Active la modération
/setup module xp true             → Active le système XP
/setup logs #salon-logs           → Salon des logs
/setup modlogs #mod-logs          → Salon des logs de modération
/setup welcome #bienvenue         → Salon bienvenue/départ
/setup tickets #catégorie         → Catégorie des tickets
/setup muterole @Muted            → Rôle mute legacy
/setup view                       → Voir la config actuelle
/setup reset                      → Réinitialiser
```

### Modules activables

Chaque module peut être activé/désactivé indépendamment :
```
/setup module <nom> <true|false>
```

Modules disponibles : `moderation`, `tickets`, `logs`, `security`, `onboarding`, `xp`, `economy`, `roles`, `utility`, `fun`, `tags`, `announcements`, `stats`, `tempvoice`, `applications`, `events`, `custom_commands`, `music`, `rp`

---

## 📁 Structure du projet

```
Bot-Discord/
├── src/                          # ← Code v2.0
│   ├── index.js                  # Point d'entrée principal
│   ├── deploy-commands.js        # Déploiement des slash commands
│   │
│   ├── core/                     # Framework
│   │   ├── logger.js             # Winston (console + fichiers rotatifs)
│   │   ├── configService.js      # Config par serveur avec cache
│   │   ├── eventBus.js           # Bus d'événements interne
│   │   ├── i18n.js               # Internationalisation (fr/en)
│   │   ├── commandHandler.js     # Chargement dynamique des commandes
│   │   ├── eventHandler.js       # Chargement dynamique des événements
│   │   ├── componentHandler.js   # Chargement boutons/selects/modals
│   │   ├── scheduler.js          # Tâches cron planifiées
│   │   └── api.js                # API REST optionnelle
│   │
│   ├── database/                 # Couche données
│   │   ├── knexfile.js           # Config Knex + SQLite
│   │   ├── index.js              # Init DB + migrations
│   │   ├── migrations/           # Schéma (27 tables)
│   │   ├── guildQueries.js       # Requêtes guilds
│   │   ├── userQueries.js        # Requêtes users
│   │   ├── sanctionQueries.js    # Requêtes sanctions
│   │   ├── logQueries.js         # Requêtes logs
│   │   └── ticketQueries.js      # Requêtes tickets
│   │
│   ├── commands/                 # Commandes par module
│   │   ├── admin/                # setup, help
│   │   ├── moderation/           # ban, kick, warn, timeout, ...
│   │   ├── tickets/              # ticket
│   │   ├── xp/                   # rank, leaderboard
│   │   ├── economy/              # balance, daily, pay, bank
│   │   ├── roles/                # rolemenu
│   │   ├── utility/              # serverinfo, userinfo, avatar, reminder, ping
│   │   ├── fun/                  # poll, 8ball, coinflip, dice
│   │   ├── tags/                 # tag
│   │   ├── announcements/        # announce
│   │   ├── stats/                # stats
│   │   ├── tempvoice/            # tempvoice
│   │   ├── applications/         # apply
│   │   ├── events/               # event
│   │   ├── custom_commands/      # customcmd
│   │   ├── music/                # music (stub)
│   │   └── rp/                   # rp
│   │
│   ├── events/                   # Événements Discord
│   │   ├── client/               # ready, interactionCreate, guildCreate
│   │   ├── logs/                 # messageDelete, messageUpdate
│   │   └── guild/                # memberAdd/Remove, voiceState, messageCreate
│   │
│   ├── components/               # Composants interactifs
│   │   ├── buttons/              # ticket_open, ticket_close, rolebtn, event_join/leave
│   │   ├── selects/              # rolemenu
│   │   └── modals/               # apply_modal
│   │
│   ├── utils/                    # Utilitaires
│   │   ├── embeds.js             # Constructeurs d'embeds
│   │   ├── permissions.js        # Vérifications hiérarchie
│   │   └── formatters.js         # Durées, XP, barres de progression
│   │
│   └── locales/                  # Traductions
│       ├── fr.json               # Français
│       └── en.json               # English
│
├── data/                         # SQLite DB (auto-créé, ignoré par git)
├── logs/                         # Fichiers de logs (auto-créé, ignoré par git)
│
├── index.js                      # Legacy v1 (conservé)
├── deploy-commands.js            # Legacy v1 (conservé)
├── commands/                     # Legacy v1 (conservé)
├── events/                       # Legacy v1 (conservé)
└── utils/                        # Legacy v1 (conservé)
```

---

## 🔨 Commandes

### Modération (12 commandes)

| Commande | Description | Permission |
|----------|-------------|-----------|
| `/ban <user> [raison] [durée] [purge]` | Bannir / tempban | `BAN_MEMBERS` |
| `/unban <id> [raison]` | Débannir | `BAN_MEMBERS` |
| `/kick <user> [raison]` | Expulser | `KICK_MEMBERS` |
| `/warn <user> <raison>` | Avertissement (auto-action au seuil) | `MODERATE_MEMBERS` |
| `/timeout <user> <durée> [raison]` | Timeout | `MODERATE_MEMBERS` |
| `/clear <nombre> [user]` | Purger messages (1–100) | `MANAGE_MESSAGES` |
| `/lock on\|off [salon]` | Verrouiller/déverrouiller | `MANAGE_CHANNELS` |
| `/slowmode <secondes>` | Mode lent (0–21600s) | `MANAGE_CHANNELS` |
| `/nick <user> [surnom]` | Modifier surnom | `MANAGE_NICKNAMES` |
| `/modlogs <user> [type]` | Historique avec filtre et stats | `MODERATE_MEMBERS` |
| `/warnings <user>` | Warns actifs | `MODERATE_MEMBERS` |
| `/case <numéro> [revoke]` | Voir/révoquer une sanction | `MODERATE_MEMBERS` |

### Tickets

| Commande | Description |
|----------|-------------|
| `/ticket panel` | Panel de création |
| `/ticket open [sujet]` | Ouvrir manuellement |
| `/ticket close` | Fermer avec transcript |
| `/ticket add/remove <user>` | Gérer les accès |
| `/ticket assign <user>` | Assigner un staff |

### XP & Économie

| Commande | Description |
|----------|-------------|
| `/rank [user]` | Rang, niveau, XP, progression |
| `/leaderboard [type]` | Top 20 (XP, messages, voix) |
| `/balance [user]` | Solde portefeuille + banque |
| `/daily` | Récompense quotidienne |
| `/pay <user> <montant>` | Transférer de l'argent |
| `/bank deposit\|withdraw <montant>` | Gérer la banque |

### Utilitaire & Fun

| Commande | Description |
|----------|-------------|
| `/serverinfo` | Informations serveur |
| `/userinfo [user]` | Profil utilisateur + stats |
| `/avatar [user]` | Avatar en haute qualité |
| `/reminder set\|list\|cancel` | Rappels personnels |
| `/ping` | Latence API + WebSocket |
| `/poll <question> [choix]` | Sondage avec réactions |
| `/8ball <question>` | Boule magique |
| `/coinflip` | Pile ou face |
| `/dice [faces] [nombre]` | Lancer de dés |

### Autres modules

| Commande | Module | Description |
|----------|--------|-------------|
| `/rolemenu create\|add\|send` | Rôles | Menus de rôles (boutons/select) |
| `/tag show\|create\|delete\|list` | Tags | FAQ avec autocomplete |
| `/announce` | Annonces | Embeds personnalisés |
| `/stats` | Stats | Métriques serveur + bot |
| `/tempvoice name\|limit\|lock\|...` | TempVoice | Gérer son salon vocal |
| `/apply start\|review\|accept\|deny` | Candidatures | Système de recrutement |
| `/event create\|info\|list\|cancel` | Événements | Événements avec inscription |
| `/customcmd create\|delete\|list` | Custom | Commandes personnalisées |
| `/music play\|stop\|skip\|...` | Musique | *Préparé pour le futur* |
| `/rp create\|profile\|delete` | RP | Fiches personnage |

---

## 🦾 Déploiement Pterodactyl

Le bot est conçu pour fonctionner sur un hébergement **Pterodactyl** :

1. **Egg** : Node.js Generic (ou équivalent)
2. **Startup** : `node src/index.js`
3. **Node version** : 20+
4. **Variables** : Configurez via le panneau Pterodactyl (`.env`)

> Le bot fonctionne en **single process**, pas de Docker-in-Docker, pas de services externes.
> SQLite crée automatiquement `data/ultra.db` au premier lancement.

---

## 🔧 Stack technique

| Technologie | Version | Usage |
|-------------|---------|-------|
| [discord.js](https://discord.js.org/) | v14 | Framework Discord |
| [better-sqlite3](https://github.com/WiseLibs/better-sqlite3) | v11 | Base de données embarquée |
| [Knex.js](https://knexjs.org/) | v3 | Query builder + migrations |
| [node-cron](https://github.com/node-cron/node-cron) | v3 | Tâches planifiées |
| [node-cache](https://github.com/node-cache/node-cache) | v5 | Cache mémoire TTL |
| [winston](https://github.com/winstonjs/winston) | v3 | Logging rotatif |
| [express](https://expressjs.com/) | v4 | API REST optionnelle |
| [Node.js](https://nodejs.org/) | 20+ | Runtime |

---

## 🤝 Contribuer

Les contributions sont les bienvenues ! Consultez [CONTRIBUTING.md](CONTRIBUTING.md).

1. **Fork** le projet
2. **Créez** votre branche (`git checkout -b feature/ma-fonctionnalite`)
3. **Committez** (`git commit -m 'feat: ajout de ma fonctionnalité'`)
4. **Pushez** (`git push origin feature/ma-fonctionnalite`)
5. **Ouvrez** une Pull Request

---

## 📝 Licence

Ce projet est sous licence [MIT](LICENSE) — libre d'utilisation et de modification.

---

<div align="center">

**Développé avec ❤️ par [NicooooV1](https://github.com/NicooooV1)**

⭐ **N'hésitez pas à mettre une étoile si le projet vous plaît !** ⭐

</div>
