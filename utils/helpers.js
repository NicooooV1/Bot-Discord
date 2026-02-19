const { PermissionFlagsBits } = require('discord.js');

/**
 * Parse une durée textuelle en millisecondes
 * Exemples: "10m", "2h", "1d", "30s", "1d12h"
 */
function parseDuration(str) {
  const regex = /(\d+)\s*(s|sec|m|min|h|hr|hour|d|day|j|jour|w|week|semaine)/gi;
  let total = 0;
  let match;

  const units = {
    s: 1000, sec: 1000,
    m: 60_000, min: 60_000,
    h: 3_600_000, hr: 3_600_000, hour: 3_600_000,
    d: 86_400_000, day: 86_400_000, j: 86_400_000, jour: 86_400_000,
    w: 604_800_000, week: 604_800_000, semaine: 604_800_000,
  };

  while ((match = regex.exec(str)) !== null) {
    const value = parseInt(match[1]);
    const unit = match[2].toLowerCase();
    if (units[unit]) {
      total += value * units[unit];
    }
  }

  return total || null;
}

/**
 * Formate une durée en millisecondes en texte lisible
 */
function formatDuration(ms) {
  const parts = [];
  const days = Math.floor(ms / 86_400_000);
  const hours = Math.floor((ms % 86_400_000) / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);

  if (days) parts.push(`${days}j`);
  if (hours) parts.push(`${hours}h`);
  if (minutes) parts.push(`${minutes}m`);
  if (seconds) parts.push(`${seconds}s`);

  return parts.join(' ') || '0s';
}

/**
 * Vérifie si le bot peut agir sur un membre
 */
function canModerate(interaction, target) {
  const botMember = interaction.guild.members.me;
  const targetMember = interaction.guild.members.cache.get(target.id);

  if (!targetMember) return { ok: true }; // L'utilisateur n'est pas sur le serveur

  // Vérifier si c'est soi-même
  if (target.id === interaction.user.id) {
    return { ok: false, reason: '❌ Vous ne pouvez pas effectuer cette action sur vous-même.' };
  }

  // Vérifier si c'est le bot
  if (target.id === interaction.client.user.id) {
    return { ok: false, reason: '❌ Je ne peux pas effectuer cette action sur moi-même.' };
  }

  // Vérifier la hiérarchie des rôles (bot)
  if (targetMember.roles.highest.position >= botMember.roles.highest.position) {
    return { ok: false, reason: '❌ Je ne peux pas agir sur cet utilisateur (son rôle est supérieur ou égal au mien).' };
  }

  // Vérifier la hiérarchie des rôles (modérateur)
  if (targetMember.roles.highest.position >= interaction.member.roles.highest.position) {
    return { ok: false, reason: '❌ Vous ne pouvez pas agir sur cet utilisateur (son rôle est supérieur ou égal au vôtre).' };
  }

  // Vérifier si c'est le propriétaire du serveur
  if (target.id === interaction.guild.ownerId) {
    return { ok: false, reason: '❌ Impossible d\'agir sur le propriétaire du serveur.' };
  }

  return { ok: true };
}

/**
 * Crée une réponse d'erreur embed standardisée
 */
function errorReply(message) {
  return {
    content: message,
    ephemeral: true,
  };
}

module.exports = {
  parseDuration,
  formatDuration,
  canModerate,
  errorReply,
};
