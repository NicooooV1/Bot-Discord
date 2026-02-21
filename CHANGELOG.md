# Changelog

Tous les changements notables de ce projet sont documentés dans ce fichier.

## [2.0.0] — 2024-12-XX

### ✨ Nouvelles fonctionnalités

#### 🛡️ Modération avancée
- `/softban` — Ban + unban pour supprimer les messages récents
- `/lockdown start|end` — Verrouiller/déverrouiller tous les channels simultanément
- `/quarantine add|remove|list` — Isolement temporaire de membres suspects
- `/voicemod mute|deafen|disconnect|moveall` — Modération vocale
- `/massban` — Bannissement en masse par IDs ou critères
- `/modlogs` — Journal de modération avec filtres et stats
- `/sanctions` — Historique complet des sanctions par utilisateur
- `/note add|remove|list` — Notes modérateur (non visibles par l'utilisateur)

#### 💰 Économie étendue
- `/work` — Travailler pour gagner des {currency} (13 métiers)
- `/gamble slots|blackjack|dice|coinflip|roulette` — 5 jeux de casino
- `/weekly` — Récompenses hebdomadaires avec streak bonus
- `/shop view|buy|sell|create|delete` — Boutique complète
- `/ecoadmin give|take|reset|setbalance|multiplier|inflation` — Administration économique

#### 🎵 Système musical complet
- `/music play|pause|resume|skip|stop|queue|volume|loop|shuffle|nowplaying|playlist|lyrics|seek|remove|clear|disconnect`
- Support YouTube, Spotify, SoundCloud via play-dl
- Système de playlists sauvegardées
- Recherche de paroles (Genius API)
- File d'attente avec visualisation

#### 🎫 Tickets améliorés
- `/ticket transcript` — Génération de transcripts HTML stylisés
- `/ticket rename|priority|transfer` — Gestion avancée des tickets
- `/ticket blacklist|unblacklist` — Blacklist d'utilisateurs
- `/ticket stats` — Statistiques des tickets (temps moyen, top staff)
- Catégories de tickets avec 4 niveaux de priorité
- Envoi automatique du transcript au créateur à la fermeture

#### 🎁 Giveaways
- `/giveaway create|end|reroll|list|delete` — Système complet
- Bouton de participation interactif
- Multi-gagnants
- Reroll des gagnants

#### ⭐ Starboard
- `/starboard setup|threshold|channel|blacklist|stats` — Automatic star board
- Détection automatique des réactions ⭐

#### 📊 Sondages & Suggestions
- `/poll create|end|results` — Sondages avec boutons de vote
- `/suggest create|approve|deny|consider|list` — Système de suggestions complet
- Upvote/downvote par boutons

#### 🎮 Fun
- `/meme` — Memes Reddit (11 subreddits)
- `/ship` — Compatibilité amoureuse
- `/mock` — Texte mOcKiNg
- `/joke` — Blagues avec JokeAPI
- `/trivia` — Quizz avec 20 catégories et 3 difficultés
- `/say` — Faire parler le bot
- `/games tictactoe|rps|coinflip|dice` — Mini-jeux

#### 👤 Système social
- `/profile view|bio|color|birthday|badges` — Profils personnalisés
- `/rep` — Points de réputation
- `/marry propose|divorce|status` — Système de mariages
- `/birthday set|list|remove` — Anniversaires

#### ✅ Vérification
- `/verify setup|panel|config|stats` — 4 modes (bouton/captcha/rules/question)
- Panel de vérification avec bouton interactif
- Captcha et question/réponse en modal

#### 📁 Gestion des forums
- `/forum setup|template|config|lock|stats` — Templates et configuration

#### 💾 Backup & Restauration
- `/backup create|list|info|delete|load` — Sauvegarde complète du serveur
- Stockage JSON avec rôles, channels, emoji, messages, paramètres

#### 🛡️ Anti-nuke
- `/antinuke enable|disable|status|whitelist|threshold|logs|emergency`
- Détection de mass-delete channels/rôles, bans massifs
- Mode urgence avec lockdown automatique
- Whitelist par utilisateur
- Logging des actions suspectes

#### ⭐ Premium
- `/premium status|features|activate|admin` — 4 tiers (free/bronze/silver/gold)
- Codes promotionnels
- Fonctionnalités exclusives par tier

#### 🔌 Intégrations
- `/integration twitch|youtube|rss` — Notifications de streams et flux RSS

#### 🎭 Rôles
- `/reactionrole create|add|remove|list` — Reaction roles (normal/unique/required)
- `/autorole add|remove|list` — Auto-attribution de rôles (member/bot/human)

#### 🔧 Utilitaires
- `/afk` — Système AFK avec notification automatique
- `/calculator` — Calculatrice mathématique
- `/translate` — Traduction (12 langues)
- `/weather` — Météo OpenWeatherMap
- `/color` — Information couleur avec preview canvas
- `/qrcode` — Générateur de QR codes
- `/timer set|countdown|stopwatch` — Minuteries
- `/roleinfo|channelinfo|banner|emojiinfo` — Informations détaillées
- `/snipe` — Récupérer les messages supprimés
- `/perm` — Vérificateur de permissions

### 🏗️ Infrastructure
- **Docker** — Dockerfile multi-stage + docker-compose (bot + MySQL + Redis)
- **CI/CD** — GitHub Actions (lint, tests, build validation, Docker build)
- **Web Dashboard** — Express + Passport Discord OAuth2 + SPA
- **ESLint** — Configuration complète avec règles projet
- **Jest** — Suite de tests avec couverture (8 fichiers de test)

### 📦 Événements Discord
- `channelCreate/Delete/Update` — Logs des modifications de channels
- `roleCreate/Delete/Update` — Logs des modifications de rôles + anti-nuke
- `guildBanAdd/Remove` — Logs des bans/unbans
- `inviteCreate/Delete` — Tracking des invitations
- `guildUpdate` — Logs des modifications serveur + anti-nuke
- `threadCreate/Delete` — Gestion des threads avec auto-react forums
- `emojiCreate/Delete/Update` — Logs des émojis
- `webhookUpdate` — Logs des webhooks
- `messageReactionAdd/Remove` — Reaction roles + starboard

### 🌐 Localisation
- FR et EN complètement mis à jour (20+ sections, 200+ clés)
- Support des variables interpolées `{variable}`

### 📚 Documentation
- `CHANGELOG.md` complet
- `README.md` mis à jour
- `.env.example` avec toutes les variables
- `CONTRIBUTING.md` guide de contribution

---

## [1.0.0] — Version initiale

- Architecture modulaire de base
- Commandes: ban, kick, warn, timeout, purge, lock, slowmode, unban
- Système XP, économie basique
- Tickets de support
- Système d'événements
- Rôle menus
- Embeds personnalisés
- Configuration par serveur
