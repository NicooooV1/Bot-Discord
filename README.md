# 🤖 Bot Discord — Modération, Tickets & Logs

Bot Discord complet développé avec **discord.js v14** incluant un système de modération, de tickets support, de logs et de messages de bienvenue/départ.

---

## ✨ Fonctionnalités

### 🔨 Modération
| Commande | Description |
|----------|-------------|
| `/ban` | Bannir un utilisateur (avec suppression de messages optionnelle) |
| `/kick` | Expulser un utilisateur |
| `/mute` | Timeout un utilisateur (durée personnalisable) |
| `/unmute` | Retirer le timeout d'un utilisateur |
| `/warn` | Avertir un utilisateur |
| `/warnings list` | Voir les avertissements d'un utilisateur |
| `/warnings remove` | Retirer un avertissement par ID |
| `/warnings clear` | Supprimer tous les avertissements |
| `/clear` | Supprimer des messages (avec filtre par utilisateur) |
| `/userinfo` | Voir les infos et l'historique de modération |

### 🎫 Tickets
| Commande | Description |
|----------|-------------|
| `/ticket panel` | Créer un panneau de tickets interactif |
| `/ticket close` | Fermer le ticket actuel |
| `/ticket add` | Ajouter un utilisateur au ticket |
| `/ticket remove` | Retirer un utilisateur du ticket |

**Fonctionnalités des tickets :**
- 📂 5 catégories (Question, Bug, Suggestion, Signalement, Autre)
- 📩 Création via menu déroulant ou bouton rapide
- 📋 Sauvegarde automatique des transcripts à la fermeture
- 🔒 Maximum 3 tickets ouverts par utilisateur
- ➕ Ajout/retrait de membres dans un ticket

### 📋 Logs
- 📝 Messages supprimés (avec pièces jointes)
- ✏️ Messages modifiés (avant/après)
- 👥 Arrivées et départs de membres
- 🔨 Actions de modération (ban, kick, mute, warn)
- 🎫 Fermeture de tickets (avec transcript)

### 👋 Bienvenue / Départ
- Messages personnalisables avec variables
- Embeds automatiques avec avatar et compteur de membres

### ⚙️ Configuration
| Commande | Description |
|----------|-------------|
| `/setup logs` | Définir le salon de logs |
| `/setup welcome` | Définir le salon de bienvenue |
| `/setup welcome-message` | Personnaliser le message de bienvenue |
| `/setup leave-message` | Personnaliser le message de départ |
| `/setup ticket-category` | Définir la catégorie pour les tickets |
| `/setup ticket-logs` | Définir le salon de logs des tickets |
| `/setup mod-role` | Définir le rôle modérateur |
| `/setup view` | Voir la configuration actuelle |

---

## 🚀 Installation

### Prérequis
- [Node.js](https://nodejs.org/) v18 ou supérieur
- Un bot Discord créé sur le [Discord Developer Portal](https://discord.com/developers/applications)

### 1. Cloner et installer

```bash
cd discord-bot
npm install
```

### 2. Configurer le bot

Copiez le fichier `.env.example` en `.env` et remplissez les valeurs :

```bash
cp .env.example .env
```

```env
BOT_TOKEN=votre_token_ici
CLIENT_ID=votre_client_id_ici
GUILD_ID=votre_guild_id_ici
```

### 3. Configurer les intents sur Discord Developer Portal

Allez dans votre application > **Bot** > activez :
- ✅ **Presence Intent**
- ✅ **Server Members Intent**
- ✅ **Message Content Intent**

### 4. Inviter le bot

Générez un lien d'invitation dans **OAuth2 > URL Generator** avec les scopes :
- `bot`
- `applications.commands`

Et les permissions :
- Administrator (ou les permissions spécifiques nécessaires)

### 5. Déployer les commandes

```bash
npm run deploy
```

### 6. Lancer le bot

```bash
npm start
```

---

## ⚙️ Configuration initiale sur le serveur

Une fois le bot en ligne, configurez-le avec les commandes `/setup` :

```
/setup logs #salon-logs
/setup welcome #salon-bienvenue
/setup ticket-category Catégorie Tickets
/setup ticket-logs #ticket-logs
/setup mod-role @Modérateur
```

### Variables disponibles pour les messages

| Variable | Remplacée par |
|----------|---------------|
| `{user}` | Mention de l'utilisateur |
| `{username}` | Nom d'utilisateur |
| `{tag}` | Tag complet (ex: User#1234) |
| `{server}` | Nom du serveur |
| `{memberCount}` | Nombre de membres |

---

## 📁 Structure du projet

```
discord-bot/
├── index.js                  # Point d'entrée principal
├── deploy-commands.js        # Script d'enregistrement des commandes
├── package.json
├── .env.example
├── commands/
│   ├── moderation/
│   │   ├── ban.js
│   │   ├── kick.js
│   │   ├── mute.js
│   │   ├── unmute.js
│   │   ├── warn.js
│   │   ├── warnings.js
│   │   ├── clear.js
│   │   └── userinfo.js
│   ├── tickets/
│   │   └── ticket.js
│   └── admin/
│       └── setup.js
├── events/
│   ├── ready.js
│   ├── interactionCreate.js
│   ├── guildMemberAdd.js
│   ├── guildMemberRemove.js
│   ├── messageDelete.js
│   └── messageUpdate.js
├── utils/
│   ├── database.js           # SQLite (better-sqlite3)
│   ├── logger.js             # Système de logs en embeds
│   └── helpers.js            # Utilitaires (permissions, durées)
└── data/
    └── bot.db                # Base de données (créée automatiquement)
```

---

## 📝 Licence

MIT — Libre d'utilisation et de modification.
