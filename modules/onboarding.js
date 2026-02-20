// ============================================================
// Module Manifest : Onboarding
// Messages de bienvenue et d'au revoir
// ============================================================

module.exports = {
  id: 'onboarding',
  name: 'Onboarding',
  emoji: '👋',
  description: 'Messages de bienvenue, au revoir, et rôles automatiques.',
  category: 'community',

  dependencies: [],
  requiredPermissions: [
    'SendMessages',
    'EmbedLinks',
    'ManageRoles',
  ],

  configSchema: {
    welcomeChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: true,
      label: 'Salon bienvenue',
      description: 'Salon où les messages de bienvenue sont envoyés.',
    },
    welcomeMessage: {
      type: 'string',
      maxLength: 2000,
      required: false,
      default: 'Bienvenue {user.mention} sur **{guild.name}** ! 🎉',
      label: 'Message de bienvenue',
      description: 'Message envoyé à l\'arrivée. Variables: {user.mention}, {user.tag}, {guild.name}, {guild.memberCount}',
    },
    welcomeRole: {
      type: 'role',
      required: false,
      label: 'Rôle automatique',
      description: 'Rôle attribué automatiquement aux nouveaux membres.',
    },
    goodbyeChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: false,
      label: 'Salon au revoir',
      description: 'Salon pour les messages d\'au revoir. Laisser vide pour désactiver.',
    },
    goodbyeMessage: {
      type: 'string',
      maxLength: 2000,
      required: false,
      default: '**{user.tag}** a quitté le serveur. 👋',
      label: 'Message d\'au revoir',
      description: 'Message envoyé au départ. Variables: {user.tag}, {guild.name}, {guild.memberCount}',
    },
    welcomeEmbed: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'Utiliser un embed',
      description: 'Envoyer le message de bienvenue dans un embed plutôt qu\'en texte brut.',
    },
    welcomeEmbedColor: {
      type: 'string',
      required: false,
      default: '#5865F2',
      regex: '^#[0-9A-Fa-f]{6}$',
      label: 'Couleur embed',
      description: 'Couleur de l\'embed de bienvenue (format hex).',
    },
  },

  commands: [],
  events: ['guildMemberAdd', 'guildMemberRemove'],
  jobs: [],
};
