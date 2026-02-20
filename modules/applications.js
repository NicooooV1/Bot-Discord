// ============================================================
// Module Manifest : Candidatures
// Système de candidatures et formulaires
// ============================================================

module.exports = {
  id: 'applications',
  name: 'Candidatures',
  emoji: '📝',
  description: 'Formulaires de candidature avec review par le staff.',
  category: 'management',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    reviewChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: true,
      label: 'Salon review',
      description: 'Salon où les candidatures sont envoyées pour examen.',
    },
    reviewerRole: {
      type: 'role',
      required: false,
      label: 'Rôle revieweur',
      description: 'Rôle mentionné quand une nouvelle candidature arrive.',
    },
    resultChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: false,
      label: 'Salon résultats',
      description: 'Salon où les résultats sont annoncés.',
    },
    acceptedRole: {
      type: 'role',
      required: false,
      label: 'Rôle accepté',
      description: 'Rôle attribué automatiquement quand une candidature est acceptée.',
    },
    dmOnResult: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'DM au résultat',
      description: 'Envoyer un DM au candidat avec le résultat.',
    },
    cooldown: {
      type: 'integer',
      min: 0,
      max: 2592000,
      required: false,
      default: 86400,
      label: 'Cooldown (s)',
      description: 'Délai en secondes entre deux candidatures du même utilisateur.',
    },
  },

  commands: ['apply'],
  events: [],
  jobs: [],
};
