// ============================================================
// Module Manifest : Statistiques
// Métriques et compteurs du serveur
// ============================================================

module.exports = {
  id: 'stats',
  name: 'Statistiques',
  emoji: '📊',
  description: 'Métriques du serveur : membres, messages, activité, graphiques.',
  category: 'management',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
    'ManageChannels',
  ],

  configSchema: {
    statsChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: false,
      label: 'Salon statistiques',
      description: 'Salon où les statistiques sont postées périodiquement.',
    },
    counterChannels: {
      type: 'json',
      required: false,
      default: {},
      label: 'Salons compteurs',
      description: 'Salons vocaux mis à jour comme compteurs (membres, bots, rôles).',
    },
    trackMessages: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'Suivre les messages',
      description: 'Compter les messages pour les statistiques d\'activité.',
    },
  },

  commands: ['stats'],
  events: ['messageCreate', 'guildMemberAdd', 'guildMemberRemove'],
  jobs: ['metricsUpdate'],
};
