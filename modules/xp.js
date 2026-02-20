// ============================================================
// Module Manifest : XP & Niveaux
// Système d'expérience, niveaux, et récompenses
// ============================================================

module.exports = {
  id: 'xp',
  name: 'XP & Niveaux',
  emoji: '⭐',
  description: 'Système d\'expérience par message, niveaux, récompenses par rôles.',
  category: 'engagement',

  dependencies: [],
  requiredPermissions: [
    'ManageRoles',
    'SendMessages',
    'EmbedLinks',
  ],

  configSchema: {
    min: {
      type: 'integer',
      min: 1,
      max: 100,
      required: false,
      default: 15,
      label: 'XP minimum',
      description: 'XP minimum gagné par message.',
    },
    max: {
      type: 'integer',
      min: 1,
      max: 200,
      required: false,
      default: 25,
      label: 'XP maximum',
      description: 'XP maximum gagné par message.',
    },
    cooldown: {
      type: 'integer',
      min: 10,
      max: 600,
      required: false,
      default: 60,
      label: 'Cooldown (s)',
      description: 'Délai en secondes entre deux gains d\'XP.',
    },
    levelUpChannel: {
      type: 'channel',
      channelTypes: ['GUILD_TEXT'],
      required: false,
      label: 'Salon level up',
      description: 'Salon pour les annonces de niveau. Vide = dans le salon du message.',
    },
    levelUpMessage: {
      type: 'string',
      maxLength: 2000,
      required: false,
      default: '🎉 {user.mention} est passé au **niveau {level}** !',
      label: 'Message level up',
      description: 'Message envoyé lors d\'un passage de niveau. Variables: {user.mention}, {level}, {xp}',
    },
    roleRewards: {
      type: 'json',
      required: false,
      default: {},
      label: 'Récompenses de rôles',
      description: 'Objet { niveau: "roleId" } — rôles attribués à chaque palier.',
    },
    noXpRoles: {
      type: 'roles',
      required: false,
      default: [],
      label: 'Rôles sans XP',
      description: 'Membres avec ces rôles ne gagnent pas d\'XP.',
    },
    noXpChannels: {
      type: 'channels',
      required: false,
      default: [],
      label: 'Salons sans XP',
      description: 'Messages dans ces salons ne donnent pas d\'XP.',
    },
    stackRewards: {
      type: 'boolean',
      required: false,
      default: false,
      label: 'Cumuler les récompenses',
      description: 'Garder les rôles des niveaux précédents (true) ou retirer l\'ancien (false).',
    },
  },

  commands: ['rank', 'leaderboard', 'xpadmin'],
  events: ['messageCreate'],
  jobs: [],
};
