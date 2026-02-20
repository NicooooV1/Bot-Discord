// ============================================================
// Module Manifest : Fun
// Commandes ludiques et divertissantes
// ============================================================

module.exports = {
  id: 'fun',
  name: 'Fun',
  emoji: '🎮',
  description: '8ball, avatar stylisé, et autres commandes amusantes.',
  category: 'community',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {},

  commands: ['8ball', 'avatar', 'fun'],
  events: [],
  jobs: [],
};
