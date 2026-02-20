// ============================================================
// Module Manifest : Salons Vocaux Temporaires
// Création automatique de salons vocaux temporaires
// ============================================================

module.exports = {
  id: 'tempvoice',
  name: 'Salons Vocaux Temp.',
  emoji: '🔊',
  description: 'Création automatique de salons vocaux temporaires personnalisables.',
  category: 'community',

  dependencies: [],
  requiredPermissions: [
    'ManageChannels',
    'MoveMembers',
    'Connect',
  ],

  configSchema: {
    tempVoiceCategory: {
      type: 'channel',
      channelTypes: ['GUILD_CATEGORY'],
      required: true,
      label: 'Catégorie',
      description: 'Catégorie où les salons temporaires sont créés.',
    },
    tempVoiceLobby: {
      type: 'channel',
      channelTypes: ['GUILD_VOICE'],
      required: true,
      label: 'Lobby vocal',
      description: 'Salon vocal "Créer un salon" — rejoindre ce salon en crée un nouveau.',
    },
    defaultLimit: {
      type: 'integer',
      min: 0,
      max: 99,
      required: false,
      default: 0,
      label: 'Limite par défaut',
      description: 'Limite de membres par défaut (0 = illimité).',
    },
    namingTemplate: {
      type: 'string',
      maxLength: 100,
      required: false,
      default: '🔊 Salon de {user.name}',
      label: 'Template nom',
      description: 'Template pour le nom du salon. Variables: {user.name}, {user.tag}',
    },
    allowRename: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'Renommage',
      description: 'Permettre au créateur de renommer son salon.',
    },
    allowLimit: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'Modifier limite',
      description: 'Permettre au créateur de modifier la limite de membres.',
    },
  },

  commands: ['tempvoice'],
  events: ['voiceStateUpdate'],
  jobs: ['tempvoiceCleanup'],
};
