// ============================================================
// Module Manifest : Intégrations
// Connexions vers services tiers
// ============================================================

module.exports = {
  id: 'integrations',
  name: 'Intégrations',
  emoji: '🔗',
  description: 'Connexions vers Twitch, YouTube, GitHub, RSS et autres services.',
  category: 'utility',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    twitchChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: false,
      label: 'Salon Twitch',
      description: 'Salon pour les notifications Twitch.',
    },
    youtubeChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: false,
      label: 'Salon YouTube',
      description: 'Salon pour les notifications YouTube.',
    },
    twitchUsers: {
      type: 'json',
      required: false,
      default: [],
      label: 'Streamers Twitch',
      description: 'Liste des noms d\'utilisateurs Twitch à suivre.',
    },
    youtubeChannels: {
      type: 'json',
      required: false,
      default: [],
      label: 'Chaînes YouTube',
      description: 'Liste des IDs de chaînes YouTube à suivre.',
    },
  },

  commands: [],
  events: [],
  jobs: ['twitchCheck', 'youtubeCheck'],
};
