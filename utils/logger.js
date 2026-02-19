const { EmbedBuilder } = require('discord.js');
const { getGuildConfig } = require('./database');

const COLORS = {
  RED: 0xE74C3C,
  ORANGE: 0xE67E22,
  YELLOW: 0xF1C40F,
  GREEN: 0x2ECC71,
  BLUE: 0x3498DB,
  PURPLE: 0x9B59B6,
  GREY: 0x95A5A6,
};

/**
 * Envoie un embed de log dans le salon de logs configuré
 */
async function sendLog(guild, embed) {
  try {
    const config = getGuildConfig(guild.id);
    if (!config?.log_channel_id) return;

    const channel = guild.channels.cache.get(config.log_channel_id);
    if (!channel) return;

    await channel.send({ embeds: [embed] });
  } catch (error) {
    console.error('[LOGS] Erreur lors de l\'envoi du log:', error.message);
  }
}

/**
 * Log de modération (ban, kick, mute, warn)
 */
function modLog(guild, { action, moderator, target, reason, duration, color }) {
  const embed = new EmbedBuilder()
    .setTitle(`🔨 ${action}`)
    .setColor(color || COLORS.RED)
    .addFields(
      { name: '👤 Utilisateur', value: `${target} (${target.id})`, inline: true },
      { name: '🛡️ Modérateur', value: `${moderator} (${moderator.id})`, inline: true },
      { name: '📝 Raison', value: reason || 'Aucune raison spécifiée', inline: false },
    )
    .setThumbnail(target.displayAvatarURL?.({ dynamic: true }) || null)
    .setTimestamp();

  if (duration) {
    embed.addFields({ name: '⏱️ Durée', value: duration, inline: true });
  }

  return sendLog(guild, embed);
}

/**
 * Log de message (edit, delete)
 */
function messageLog(guild, { action, author, channel, oldContent, newContent, color }) {
  const embed = new EmbedBuilder()
    .setTitle(`📝 ${action}`)
    .setColor(color || COLORS.YELLOW)
    .addFields(
      { name: '👤 Auteur', value: `${author} (${author.id})`, inline: true },
      { name: '📌 Salon', value: `${channel}`, inline: true },
    )
    .setTimestamp();

  if (oldContent) {
    embed.addFields({ name: '📄 Ancien contenu', value: oldContent.substring(0, 1024) || '*Vide*' });
  }
  if (newContent) {
    embed.addFields({ name: '📄 Nouveau contenu', value: newContent.substring(0, 1024) || '*Vide*' });
  }

  return sendLog(guild, embed);
}

/**
 * Log de membre (join, leave)
 */
function memberLog(guild, { action, member, color, extra }) {
  const user = member.user || member;
  const embed = new EmbedBuilder()
    .setTitle(`👥 ${action}`)
    .setColor(color || COLORS.BLUE)
    .addFields(
      { name: '👤 Membre', value: `${user} (${user.id})`, inline: true },
      { name: '📅 Compte créé le', value: `<t:${Math.floor(user.createdTimestamp / 1000)}:R>`, inline: true },
    )
    .setThumbnail(user.displayAvatarURL({ dynamic: true }))
    .setTimestamp();

  if (extra) {
    embed.addFields(extra);
  }

  return sendLog(guild, embed);
}

module.exports = {
  COLORS,
  sendLog,
  modLog,
  messageLog,
  memberLog,
};
