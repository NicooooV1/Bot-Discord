// ============================================================
// Module Manifest : Roleplay
// Profils RP et inventaire
// ============================================================

module.exports = {
  id: 'rp',
  name: 'Roleplay',
  emoji: '🎭',
  description: 'Profils de personnages RP, inventaire, et gestion de campagnes.',
  category: 'creative',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    rpChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: false,
      label: 'Salon RP principal',
      description: 'Salon dédié aux messages RP.',
    },
    maxProfiles: {
      type: 'integer',
      min: 1,
      max: 20,
      required: false,
      default: 5,
      label: 'Max profils / membre',
      description: 'Nombre maximum de personnages RP par membre.',
    },
    approvalRequired: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'Approbation requise',
      description: 'Les profils RP doivent être approuvés par le staff.',
    },
    approvalChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: false,
      label: 'Salon approbation',
      description: 'Salon où les profils en attente sont envoyés pour review.',
    },
  },

  commands: ['rpprofile', 'rpinventory'],
  events: [],
  jobs: [],
};
