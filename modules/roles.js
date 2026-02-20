// ============================================================
// Module Manifest : Rôles
// Menus de rôles et rôles automatiques
// ============================================================

module.exports = {
  id: 'roles',
  name: 'Rôles',
  emoji: '🏷️',
  description: 'Menus de sélection de rôles, rôles à réaction, rôles automatiques.',
  category: 'management',

  dependencies: [],
  requiredPermissions: [
    'ManageRoles',
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    maxMenusPerGuild: {
      type: 'integer',
      min: 1,
      max: 50,
      required: false,
      default: 25,
      label: 'Max menus',
      description: 'Nombre maximum de menus de rôles par serveur.',
    },
    logChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: false,
      label: 'Salon logs rôles',
      description: 'Salon pour journaliser les attributions de rôles par menu.',
    },
  },

  commands: ['rolemenu'],
  events: [],
  jobs: [],
};
