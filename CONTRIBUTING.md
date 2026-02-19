# 🤝 Contribuer au Bot Discord

Merci de vouloir contribuer ! Voici les guidelines pour nous aider à garder un code propre et cohérent.

---

## 📋 Prérequis

- [Node.js 18+](https://nodejs.org/)
- Un serveur Discord de test
- Un bot de test (ne pas utiliser le bot de production)

## 🚀 Démarrer

1. **Forkez** le dépôt
2. **Clonez** votre fork
   ```bash
   git clone https://github.com/VOTRE_USER/Bot-Discord.git
   cd Bot-Discord
   ```
3. **Installez** les dépendances
   ```bash
   npm install
   ```
4. **Configurez** le `.env`
   ```bash
   cp .env.example .env
   # Éditez avec vos valeurs de test
   ```

## 📐 Conventions de code

### Structure des commandes

Chaque commande exporte un objet avec `data` (SlashCommandBuilder) et `execute` :

```js
const { SlashCommandBuilder } = require('discord.js');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('exemple')
    .setDescription('Description de la commande'),

  async execute(interaction) {
    // Logique ici
  },
};
```

### Structure des événements

```js
module.exports = {
  name: 'eventName',
  once: false, // true pour événements one-shot comme 'ready'
  async execute(...args) {
    // Logique ici
  },
};
```

### Style

- **Pas de point-virgule** oublié — soyez consistant
- **Embeds** pour les réponses utilisateur (pas de texte brut pour les résultats)
- **Réponses éphémères** pour les erreurs et confirmations sensibles
- **Gestion d'erreurs** : `try/catch` avec message utilisateur + `console.error`
- **Logs** : utiliser `utils/logger.js` pour les logs Discord

## 🔀 Workflow Git

### Branches

- `main` — branche stable
- `feature/nom` — nouvelles fonctionnalités
- `fix/nom` — corrections de bugs
- `docs/nom` — documentation

### Commits (Conventional Commits)

```
feat: ajout de la commande /purge
fix: correction du timeout sur /mute
docs: mise à jour du README
refactor: restructuration du système de logs
```

### Pull Requests

1. Créez une branche depuis `main`
2. Faites vos changements
3. Testez sur un serveur Discord de test
4. Ouvrez une PR avec :
   - **Description** claire des changements
   - **Screenshots** si changements visuels (embeds, etc.)
   - **Tests effectués** (quelles commandes / scénarios)

## 🐛 Signaler un bug

Ouvrez une [Issue](https://github.com/NicooooV1/Bot-Discord/issues) avec :
- Description du problème
- Étapes de reproduction
- Comportement attendu vs obtenu
- Version de Node.js et discord.js

## 💡 Proposer une fonctionnalité

Ouvrez une [Issue](https://github.com/NicooooV1/Bot-Discord/issues) avec le label `enhancement` :
- Description de la fonctionnalité
- Cas d'usage
- Maquette / exemple si possible

---

Merci pour votre contribution ! 🎉
