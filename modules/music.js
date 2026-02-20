// ============================================================
// Module Manifest : Musique
// Lecture musicale via salons vocaux
// ============================================================

module.exports = {
  id: 'music',
  name: 'Musique',
  emoji: '🎵',
  description: 'Lecture de musique dans les salons vocaux (YouTube, Spotify, etc.).',
  category: 'community',

  dependencies: [],
  requiredPermissions: [
    'Connect',
    'Speak',
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    djRole: {
      type: 'role',
      required: false,
      label: 'Rôle DJ',
      description: 'Rôle pour contrôler la musique (skip, stop, volume). Vide = tout le monde.',
    },
    defaultVolume: {
      type: 'integer',
      min: 1,
      max: 100,
      required: false,
      default: 50,
      label: 'Volume par défaut',
      description: 'Volume par défaut en pourcentage.',
    },
    maxQueueLength: {
      type: 'integer',
      min: 1,
      max: 500,
      required: false,
      default: 100,
      label: 'Max queue',
      description: 'Nombre maximum de pistes dans la file d\'attente.',
    },
    leaveOnEmpty: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'Quitter si vide',
      description: 'Quitter le vocal si le salon est vide.',
    },
    leaveDelay: {
      type: 'integer',
      min: 0,
      max: 600,
      required: false,
      default: 30,
      label: 'Délai départ (s)',
      description: 'Secondes avant de quitter un salon vide.',
    },
  },

  commands: ['music'],
  events: ['voiceStateUpdate'],
  jobs: [],
};
