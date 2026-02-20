// ============================================================
// Module Manifest : Tags
// Commandes de réponses sauvegardées
// ============================================================

module.exports = {
  id: 'tags',
  name: 'Tags',
  emoji: '🔖',
  description: 'Réponses rapides et réutilisables créées par le staff.',
  category: 'utility',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    staffOnly: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'Staff uniquement',
      description: 'Seul le staff (ManageMessages) peut créer/modifier des tags.',
    },
    maxTags: {
      type: 'integer',
      min: 1,
      max: 500,
      required: false,
      default: 100,
      label: 'Max tags',
      description: 'Nombre maximum de tags par serveur.',
    },
  },

  commands: ['tag'],
  events: [],
  jobs: [],
};
