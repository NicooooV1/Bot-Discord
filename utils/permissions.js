// ===================================
// Ultra Suite — Permission Helpers
// ===================================

const { PermissionFlagsBits } = require('discord.js');

/**
 * Vérifie si un membre peut modérer une cible
 * @param {import('discord.js').GuildMember} mod
 * @param {import('discord.js').GuildMember} target
 * @returns {{ allowed: boolean, reason?: string }}
 */
function canModerate(mod, target) {
  // Self-action
  if (mod.id === target.id) {
    return { allowed: false, reason: 'self_action' };
  }

  // Action sur un bot
  if (target.user.bot) {
    return { allowed: false, reason: 'bot_action' };
  }

  // Owner du serveur
  if (target.id === target.guild.ownerId) {
    return { allowed: false, reason: 'target_is_owner' };
  }

  // Hiérarchie de rôles
  if (mod.roles.highest.position <= target.roles.highest.position && mod.id !== mod.guild.ownerId) {
    return { allowed: false, reason: 'higher_role' };
  }

  // Bot peut agir ?
  const botMember = target.guild.members.me;
  if (botMember && botMember.roles.highest.position <= target.roles.highest.position) {
    return { allowed: false, reason: 'bot_higher_role' };
  }

  return { allowed: true };
}

/**
 * Vérifie si le bot a les permissions requises dans un salon
 * @param {import('discord.js').GuildChannel} channel
 * @param {bigint[]} perms
 * @returns {{ allowed: boolean, missing: string[] }}
 */
function botHasPermissions(channel, perms) {
  const me = channel.guild.members.me;
  if (!me) return { allowed: false, missing: ['Not in guild'] };

  const channelPerms = channel.permissionsFor(me);
  const missing = [];

  for (const perm of perms) {
    if (!channelPerms.has(perm)) {
      const name = Object.entries(PermissionFlagsBits).find(([, v]) => v === perm)?.[0] || String(perm);
      missing.push(name);
    }
  }

  return { allowed: missing.length === 0, missing };
}

/**
 * Map des permissions Discord FR
 */
const PERM_NAMES_FR = {
  Administrator: 'Administrateur',
  ManageGuild: 'Gérer le serveur',
  ManageChannels: 'Gérer les salons',
  ManageRoles: 'Gérer les rôles',
  ManageMessages: 'Gérer les messages',
  BanMembers: 'Bannir des membres',
  KickMembers: 'Expulser des membres',
  ModerateMembers: 'Modérer les membres',
  MuteMembers: 'Rendre muet',
  DeafenMembers: 'Mettre en sourdine',
  MoveMembers: 'Déplacer les membres',
  ManageNicknames: 'Gérer les pseudos',
  ViewAuditLog: 'Voir le journal d\'audit',
  SendMessages: 'Envoyer des messages',
  EmbedLinks: 'Intégrer des liens',
  AttachFiles: 'Joindre des fichiers',
  ReadMessageHistory: 'Lire l\'historique',
  UseExternalEmojis: 'Utiliser des emojis externes',
  Connect: 'Se connecter',
  Speak: 'Parler',
};

module.exports = { canModerate, botHasPermissions, PERM_NAMES_FR };
