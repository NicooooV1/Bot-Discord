// ============================================================
// Module Manifest : Sécurité
// AutoMod et Anti-Raid
// ============================================================

module.exports = {
  id: 'security',
  name: 'Sécurité',
  emoji: '🛡️',
  description: 'AutoMod (anti-spam, anti-link, anti-mention) et anti-raid.',
  category: 'moderation',

  dependencies: [],
  requiredPermissions: [
    'ManageMessages',
    'KickMembers',
    'BanMembers',
    'ModerateMembers',
  ],

  configSchema: {
    // == AutoMod ==
    antiSpam: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'Anti-spam',
      description: 'Supprimer automatiquement les messages de spam.',
    },
    antiLink: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'Anti-lien',
      description: 'Supprimer automatiquement les messages contenant des liens.',
    },
    antiMention: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'Anti-mention de masse',
      description: 'Supprimer les messages avec trop de mentions.',
    },
    mentionLimit: {
      type: 'integer',
      min: 2,
      max: 50,
      required: false,
      default: 5,
      label: 'Limite mentions',
      description: 'Nombre maximum de mentions par message.',
    },
    whitelistedLinks: {
      type: 'json',
      required: false,
      default: [],
      label: 'Liens autorisés',
      description: 'Liste de domaines exemptés de l\'anti-lien.',
    },
    exemptRoles: {
      type: 'roles',
      required: false,
      default: [],
      label: 'Rôles exemptés',
      description: 'Rôles ignorés par l\'automod.',
    },
    exemptChannels: {
      type: 'channels',
      required: false,
      default: [],
      label: 'Salons exemptés',
      description: 'Salons ignorés par l\'automod.',
    },

    // == Anti-Raid ==
    antiRaidEnabled: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'Anti-raid',
      description: 'Active la protection anti-raid.',
    },
    joinThreshold: {
      type: 'integer',
      min: 2,
      max: 100,
      required: false,
      default: 10,
      label: 'Seuil joins/minute',
      description: 'Nombre de joins dans la fenêtre pour déclencher l\'anti-raid.',
    },
    joinWindow: {
      type: 'integer',
      min: 5,
      max: 120,
      required: false,
      default: 10,
      label: 'Fenêtre (secondes)',
      description: 'Durée de la fenêtre de détection.',
    },
    raidAction: {
      type: 'enum',
      values: ['kick', 'ban', 'timeout'],
      required: false,
      default: 'kick',
      label: 'Action anti-raid',
      description: 'Action appliquée aux comptes détectés comme raiders.',
    },
  },

  commands: ['automod'],
  events: ['messageCreate', 'guildMemberAdd'],
  jobs: [],
};
