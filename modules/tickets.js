// ============================================================
// Module Manifest : Tickets
// Système de support par tickets avec panels et transcripts
// ============================================================

module.exports = {
  id: 'tickets',
  name: 'Tickets',
  emoji: '🎫',
  description: 'Système de tickets de support avec panels, staff, transcripts.',
  category: 'management',

  dependencies: [],
  requiredPermissions: [
    'ManageChannels',
    'ManageRoles',
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    ticketCategory: {
      type: 'channel',
      channelTypes: ['GUILD_CATEGORY'],
      required: true,
      label: 'Catégorie tickets',
      description: 'Catégorie où les salons de tickets sont créés.',
    },
    ticketStaffRole: {
      type: 'role',
      required: true,
      label: 'Rôle staff',
      description: 'Rôle ayant accès à tous les tickets.',
    },
    ticketLogChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: false,
      label: 'Salon logs tickets',
      description: 'Salon où les actions sur les tickets sont journalisées.',
    },
    maxTicketsPerUser: {
      type: 'integer',
      min: 1,
      max: 25,
      required: false,
      default: 3,
      label: 'Max tickets / utilisateur',
      description: 'Nombre maximum de tickets ouverts simultanément par utilisateur.',
    },
    dmOnClose: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'DM à la fermeture',
      description: 'Envoyer un DM au membre quand son ticket est fermé.',
    },
    transcriptEnabled: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'Transcripts',
      description: 'Générer un transcript automatique à la fermeture.',
    },
  },

  commands: ['ticket', 'ticketpanel'],
  events: [],
  jobs: [],
};
