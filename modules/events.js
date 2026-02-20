// ============================================================
// Module Manifest : Événements
// Création et gestion d'événements communautaires
// ============================================================

module.exports = {
  id: 'events',
  name: 'Événements',
  emoji: '📅',
  description: 'Création d\'événements avec inscriptions, rappels, et récurrence.',
  category: 'community',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
    'ManageEvents',
  ],

  configSchema: {
    eventChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: false,
      label: 'Salon événements',
      description: 'Salon par défaut pour publier les événements.',
    },
    reminderEnabled: {
      type: 'boolean',
      required: false,
      default: true,
      label: 'Rappels',
      description: 'Envoyer des rappels automatiques avant les événements.',
    },
    reminderDelays: {
      type: 'json',
      required: false,
      default: [3600, 900],
      label: 'Délais rappels (s)',
      description: 'Tableau de délais en secondes avant l\'événement (ex: [3600, 900] = 1h et 15min).',
    },
    maxParticipants: {
      type: 'integer',
      min: 0,
      max: 10000,
      required: false,
      default: 0,
      label: 'Max participants',
      description: 'Limite de participants par défaut (0 = illimité).',
    },
    managerRole: {
      type: 'role',
      required: false,
      label: 'Rôle organisateur',
      description: 'Rôle pouvant créer et gérer des événements sans être admin.',
    },
  },

  commands: ['event'],
  events: [],
  jobs: ['eventCleanup', 'eventReminder'],
};
