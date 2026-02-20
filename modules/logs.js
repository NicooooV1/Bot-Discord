// ============================================================
// Module Manifest : Logs
// Journalisation des événements serveur
// ============================================================

module.exports = {
  id: 'logs',
  name: 'Logs',
  emoji: '📋',
  description: 'Journalisation des messages supprimés, édités, joins, leaves, etc.',
  category: 'management',

  dependencies: [],
  requiredPermissions: [
    'ViewAuditLog',
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    logChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: true,
      label: 'Salon de logs',
      description: 'Salon principal où les événements du serveur sont journalisés.',
    },
    logMessageDelete: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'Log suppression messages',
      description: 'Journaliser les messages supprimés.',
    },
    logMessageEdit: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'Log édition messages',
      description: 'Journaliser les messages édités.',
    },
    logMemberJoin: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'Log arrivées',
      description: 'Journaliser les nouveaux membres.',
    },
    logMemberLeave: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'Log départs',
      description: 'Journaliser les départs de membres.',
    },
    logVoice: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'Log vocaux',
      description: 'Journaliser les mouvements dans les salons vocaux.',
    },
    logRoles: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'Log changements rôles',
      description: 'Journaliser les changements de rôles des membres.',
    },
    ignoredChannels: {
      type: 'channels',
      required: false,
      default: [],
      label: 'Salons ignorés',
      description: 'Salons exclus de la journalisation.',
    },
  },

  commands: [],
  events: ['messageDelete', 'messageUpdate', 'guildMemberAdd', 'guildMemberRemove', 'guildMemberUpdate', 'voiceStateUpdate'],
  jobs: [],
};
