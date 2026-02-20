// ============================================================
// Module Manifest : Utilitaire
// Commandes utilitaires générales
// ============================================================

module.exports = {
  id: 'utility',
  name: 'Utilitaire',
  emoji: '🔧',
  description: 'Commandes outils : ping, serverinfo, userinfo, embed builder, rappels.',
  category: 'utility',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    embedDefaultColor: {
      type: 'string',
      required: false,
      default: '#5865F2',
      regex: '^#[0-9A-Fa-f]{6}$',
      label: 'Couleur embeds',
      description: 'Couleur par défaut des embeds du bot.',
    },
  },

  commands: ['ping', 'serverinfo', 'userinfo', 'embed', 'reminder', 'help', 'avatar', 'announce'],
  events: [],
  jobs: ['reminderCheck'],
};
