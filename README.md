<div align="center">

# 🤖 Bot Discord — Modération, Tickets, Logs & Anti-Spam

Bot Discord complet développé avec **discord.js v14** incluant un système de modération avancé, tickets support, logs détaillés, anti-spam, et messages de bienvenue/départ.

[![Discord.js](https://img.shields.io/badge/discord.js-v14-5865F2?logo=discord&logoColor=white)](https://discord.js.org/)
[![Node.js](https://img.shields.io/badge/Node.js-18+-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![SQLite](https://img.shields.io/badge/SQLite-better--sqlite3-003B57?logo=sqlite&logoColor=white)](https://github.com/WiseLibs/better-sqlite3)

</div>

---

## 📑 Table des matières

- [Fonctionnalités](#-fonctionnalités)
- [Prérequis](#-prérequis)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Structure du projet](#-structure-du-projet)
- [Commandes](#-commandes)
- [Événements](#-événements-logs)
- [Anti-Spam](#-anti-spam)
- [Contribuer](#-contribuer)
- [Licence](#-licence)

---

## ✨ Fonctionnalités

| Catégorie | Détails |
|-----------|---------|
| 🔨 **Modération** | 17 commandes : ban, kick, mute, warn, purge, lock, slowmode... |
| 🎫 **Tickets** | 5 catégories, formulaire, transcript auto, quota par user |
| 🛡️ **Anti-Spam** | Flood, doublons, mentions, majuscules — mute auto + warn |
| 📋 **Logs** | 12 types d'événements loggés en embeds dans un salon dédié |
| 👋 **Bienvenue/Départ** | Messages personnalisables avec variables dynamiques |
| ⚙️ **Administration** | Configuration complète via `/setup`, info serveur, aide |

---

## 📋 Prérequis

- [Node.js](https://nodejs.org/) **v18** ou supérieur
- [npm](https://www.npmjs.com/) (inclus avec Node.js)
- Un bot Discord créé sur le [Developer Portal](https://discord.com/developers/applications)

### Intents requis (Developer Portal > Bot)

> **Activez ces 3 intents privilégiés** dans la section "Privileged Gateway Intents" :

- ✅ **Presence Intent**
- ✅ **Server Members Intent**
- ✅ **Message Content Intent**

### Permissions du bot

Le bot nécessite les permissions suivantes (integer: `1632113078534`) :
- Gérer les rôles, salons, surnoms
- Bannir, expulser des membres
- Gérer les messages, voir les logs d'audit
- Envoyer des messages, embeds, fichiers
- Modérer les membres (timeout)

> **Lien d'invitation** (remplacez `CLIENT_ID`) :
> ```
> https://discord.com/api/oauth2/authorize?client_id=CLIENT_ID&permissions=1632113078534&scope=bot%20applications.commands
> ```

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

Éditez `.env` avec vos valeurs :

```env
BOT_TOKEN=votre_token_ici
CLIENT_ID=votre_client_id_ici
GUILD_ID=votre_guild_id_ici   # Optionnel (dev = instantané, vide = global ~1h)
```

> ⚠️ **Ne partagez JAMAIS votre token !** Le fichier `.env` est exclu du dépôt via `.gitignore`.

### 4. Déployer les commandes slash

```bash
npm run deploy
```

### 5. Lancer le bot

```bash
npm start
```

---

## ⚙️ Configuration

Une fois le bot en ligne, configurez-le sur votre serveur avec `/setup` :

```
/setup logs #salon-logs              → Salon des logs
/setup welcome #bienvenue            → Salon bienvenue/départ
/setup ticket-category 🎫 Tickets   → Catégorie des tickets
/setup ticket-logs #ticket-logs      → Salon logs tickets
/setup mod-role @Modérateur          → Rôle modérateur
/setup antispam true                 → Activer l'anti-spam
```

Puis déployez le panel de tickets :
```
/ticket panel
```

---

## 📁 Structure du projet

```
Bot-Discord/
├── index.js                 # Point d'entrée — client Discord
├── deploy-commands.js       # Script de déploiement des slash commands
├── package.json
├── .env.example             # Template des variables d'environnement
├── .gitignore
│
├── commands/                # Commandes slash (chargement récursif)
│   ├── admin/
│   │   ├── help.js          # /help — liste des commandes
│   │   ├── serverinfo.js    # /serverinfo — infos du serveur
│   │   └── setup.js         # /setup — configuration du bot
│   ├── moderation/
│   │   ├── ban.js           # /ban
│   │   ├── banlist.js       # /banlist
│   │   ├── clear.js         # /clear
│   │   ├── kick.js          # /kick
│   │   ├── lock.js          # /lock on|off
│   │   ├── modlogs.js       # /modlogs
│   │   ├── mute.js          # /mute
│   │   ├── nick.js          # /nick
│   │   ├── slowmode.js      # /slowmode
│   │   ├── softban.js       # /softban
│   │   ├── unban.js         # /unban
│   │   ├── unmute.js        # /unmute
│   │   ├── userinfo.js      # /userinfo
│   │   ├── warn.js          # /warn
│   │   └── warnings.js      # /warnings list|remove|clear
│   └── tickets/
│       └── ticket.js        # /ticket panel|close|add|remove
│
├── events/                  # Gestionnaires d'événements Discord
│   ├── channelCreate.js     # Log création de salon
│   ├── channelDelete.js     # Log suppression de salon
│   ├── guildMemberAdd.js    # Bienvenue + log arrivée
│   ├── guildMemberRemove.js # Départ + log
│   ├── guildMemberUpdate.js # Rôles, surnoms, timeouts
│   ├── interactionCreate.js # Routeur d'interactions (slash/boutons/menus)
│   ├── messageCreate.js     # Anti-spam
│   ├── messageDelete.js     # Log suppression message
│   ├── messageUpdate.js     # Log édition message
│   ├── ready.js             # Initialisation du bot
│   └── voiceStateUpdate.js  # Log activité vocale
│
├── utils/                   # Utilitaires
│   ├── antispam.js          # Moteur anti-spam (flood, doublons, caps, mentions)
│   ├── database.js          # SQLite — tables, requêtes, config par serveur
│   ├── helpers.js           # Parsing durées, vérif hiérarchie, réponses
│   └── logger.js            # Envoi d'embeds de logs dans les salons configurés
│
└── data/                    # Données (ignoré par git)
    └── bot.db               # Base de données SQLite (créée automatiquement)
```

---

## 🔨 Commandes

### Modération

| Commande | Description | Permission requise |
|----------|-------------|-------------------|
| `/ban <user> [raison] [jours]` | Bannir un utilisateur | `BAN_MEMBERS` |
| `/unban <id> [raison]` | Débannir par ID | `BAN_MEMBERS` |
| `/kick <user> [raison]` | Expulser | `KICK_MEMBERS` |
| `/softban <user> [raison] [jours]` | Ban + unban (purge messages) | `BAN_MEMBERS` |
| `/mute <user> <durée> [raison]` | Timeout (max 28j) | `MODERATE_MEMBERS` |
| `/unmute <user> [raison]` | Retirer le timeout | `MODERATE_MEMBERS` |
| `/warn <user> <raison>` | Avertissement | `MODERATE_MEMBERS` |
| `/warnings list <user>` | Voir les warns | `MODERATE_MEMBERS` |
| `/warnings remove <id>` | Retirer un warn | `MODERATE_MEMBERS` |
| `/warnings clear <user>` | Supprimer tous les warns | `MODERATE_MEMBERS` |
| `/clear <nombre> [user]` | Purger des messages (1–100) | `MANAGE_MESSAGES` |
| `/lock on\|off [salon] [raison]` | Verrouiller/déverrouiller | `MANAGE_CHANNELS` |
| `/slowmode <secondes> [raison]` | Mode lent (0–21600s) | `MANAGE_CHANNELS` |
| `/nick <user> [surnom] [raison]` | Modifier surnom | `MANAGE_NICKNAMES` |
| `/userinfo <user>` | Infos + historique modération | `MODERATE_MEMBERS` |
| `/modlogs <user>` | Historique complet des actions | `MODERATE_MEMBERS` |
| `/banlist [page]` | Liste paginée des bannis | `BAN_MEMBERS` |

### Tickets

| Commande | Description | Permission requise |
|----------|-------------|-------------------|
| `/ticket panel` | Envoyer le panel de création | `MANAGE_GUILD` |
| `/ticket close` | Fermer le ticket en cours | Staff |
| `/ticket add <user>` | Ajouter un membre au ticket | Staff |
| `/ticket remove <user>` | Retirer un membre du ticket | Staff |

### Administration

| Commande | Description | Permission requise |
|----------|-------------|-------------------|
| `/setup <option> <valeur>` | Configurer le bot | `MANAGE_GUILD` |
| `/serverinfo` | Informations du serveur | Aucune |
| `/help [catégorie]` | Liste des commandes | Aucune |

---

## 📋 Événements (Logs)

Le bot surveille et logge automatiquement les événements suivants dans le salon configuré :

| Événement | Détails |
|-----------|---------|
| 📝 Message modifié | Avant/après |
| 🗑️ Message supprimé | Contenu + pièces jointes |
| 👋 Membre rejoint | Compte + numéro membre |
| 👋 Membre parti | Rôles qu'il avait |
| 🎭 Rôle ajouté/retiré | Quel rôle, à qui |
| 📝 Surnom changé | Ancien → nouveau |
| 🔇 Timeout ajouté/retiré | Durée, par qui |
| 🔊 Vocal (join/leave/move) | Salons concernés |
| 📌 Salon créé/supprimé | Nom, type, par qui |
| 🔨 Actions de modération | Ban, kick, warn, mute... |

---

## 🛡️ Anti-Spam

Système automatique configurable via `/setup antispam true|false` :

| Détection | Seuil | Action |
|-----------|-------|--------|
| **Flood** | 5+ messages / 5 secondes | Mute 5min + warn |
| **Doublons** | 3+ messages identiques / 10s | Mute 5min + warn |
| **Mentions** | 5+ mentions par message | Mute 5min + warn |
| **Majuscules** | 70%+ caps (messages > 15 chars) | Mute 5min + warn |

> Les modérateurs et admins sont automatiquement exemptés.

---

## 🔧 Stack technique

| Technologie | Usage |
|-------------|-------|
| [discord.js v14](https://discord.js.org/) | Librairie Discord |
| [better-sqlite3](https://github.com/WiseLibs/better-sqlite3) | Base de données locale |
| [dotenv](https://github.com/motdotla/dotenv) | Variables d'environnement |
| [Node.js 18+](https://nodejs.org/) | Runtime JavaScript |

**Zéro service externe** — tout fonctionne en local, aucune API tierce requise.

---

## 🤝 Contribuer

Les contributions sont les bienvenues ! Consultez [CONTRIBUTING.md](CONTRIBUTING.md) pour les guidelines.

1. **Fork** le projet
2. **Créez** votre branche (`git checkout -b feature/ma-fonctionnalite`)
3. **Committez** vos changements (`git commit -m 'feat: ajout de ma fonctionnalité'`)
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
