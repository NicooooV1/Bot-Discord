// ============================================================
// Module Manifest : Annonces
// Système d'annonces programmées et embed
// ============================================================

module.exports = {
  id: 'announcements',
  name: 'Annonces',
  emoji: '📢',
  description: 'Création d\'annonces stylisées, programmation, templates.',
  category: 'community',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
    'MentionEveryone',
  ],

  configSchema: {
    defaultChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT', 'GUILD_ANNOUNCEMENT'],
      required: false,
      label: 'Salon par défaut',
      description: 'Salon utilisé par défaut pour les annonces.',
    },
    mentionRole: {
      type: 'role',
      required: false,
      label: 'Rôle à mentionner',
      description: 'Rôle mentionné automatiquement dans les annonces.',
    },
  },

  commands: ['announce'],
  events: [],
  jobs: ['scheduledAnnouncements'],
};
