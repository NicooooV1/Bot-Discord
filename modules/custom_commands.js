// ============================================================
// Module Manifest : Commandes Personnalisées
// Commandes custom créées par le staff
// ============================================================

module.exports = {
  id: 'custom_commands',
  name: 'Commandes Custom',
  emoji: '⚡',
  description: 'Création de commandes personnalisées avec réponses texte, embed, ou actions.',
  category: 'utility',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    maxCommands: {
      type: 'integer',
      min: 1,
      max: 200,
      required: false,
      default: 50,
      label: 'Max commandes',
      description: 'Nombre maximum de commandes custom par serveur.',
    },
    staffOnly: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'Création staff',
      description: 'Seul le staff peut créer des commandes custom.',
    },
  },

  commands: ['customcmd'],
  events: [],
  jobs: [],
};
