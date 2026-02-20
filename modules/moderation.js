// ============================================================
// Module Manifest : Modération
// Gestion des sanctions, mutes, bans, et discipline du serveur
// ============================================================

module.exports = {
  id: 'moderation',
  name: 'Modération',
  emoji: '🔨',
  description: 'Sanctions, bans, mutes, warns — gestion disciplinaire complète.',
  category: 'moderation',

  dependencies: [],
  requiredPermissions: [
    'BanMembers',
    'KickMembers',
    'ModerateMembers',
    'ManageMessages',
    'ManageRoles',
  ],

  configSchema: {
    modLogChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: true,
      label: 'Salon logs modération',
      description: 'Salon où les sanctions sont journalisées automatiquement.',
    },
    muteRole: {
      type: 'role',
      required: false,
      label: 'Rôle mute',
      description: 'Rôle appliqué lors d\'un mute. Laisser vide pour utiliser le timeout Discord.',
    },
    dmOnSanction: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'DM au sanctionné',
      description: 'Envoyer un message privé au membre sanctionné.',
    },
    maxWarns: {
      type: 'integer',
      min: 1,
      max: 50,
      required: false,
      default: 5,
      label: 'Max avertissements',
      description: 'Nombre d\'avertissements avant action automatique.',
    },
    warnAction: {
      type: 'enum',
      values: ['TIMEOUT', 'KICK', 'BAN'],
      required: false,
      default: 'TIMEOUT',
      label: 'Action au seuil',
      description: 'Action exécutée quand le seuil d\'avertissements est atteint.',
    },
    warnActionDuration: {
      type: 'integer',
      min: 60,
      max: 2592000,
      required: false,
      default: 3600,
      label: 'Durée action auto (s)',
      description: 'Durée en secondes pour l\'action automatique (timeout/ban temp).',
    },
  },

  commands: ['ban', 'kick', 'warn', 'timeout', 'sanctions', 'unban', 'purge', 'slowmode', 'lock', 'note', 'modlogs'],
  events: [],
  jobs: ['tempbanCheck'],
};
