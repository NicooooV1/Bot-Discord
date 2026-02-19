const { EmbedBuilder } = require('discord.js');
const { sendLog, COLORS } = require('../utils/logger');

module.exports = {
  name: 'guildMemberUpdate',
  async execute(oldMember, newMember) {
    if (newMember.user.bot) return;

    // ===================================
    // Changement de rôles
    // ===================================
    const oldRoles = oldMember.roles.cache;
    const newRoles = newMember.roles.cache;

    const addedRoles = newRoles.filter(r => !oldRoles.has(r.id) && r.id !== newMember.guild.id);
    const removedRoles = oldRoles.filter(r => !newRoles.has(r.id) && r.id !== newMember.guild.id);

    if (addedRoles.size > 0) {
      const embed = new EmbedBuilder()
        .setTitle('🎭 Rôle(s) ajouté(s)')
        .setColor(COLORS.GREEN)
        .addFields(
          { name: '👤 Membre', value: `${newMember.user} (${newMember.user.tag})`, inline: true },
          { name: '➕ Rôle(s)', value: addedRoles.map(r => r.toString()).join(', '), inline: true },
        )
        .setThumbnail(newMember.user.displayAvatarURL({ dynamic: true }))
        .setTimestamp();

      await sendLog(newMember.guild, embed);
    }

    if (removedRoles.size > 0) {
      const embed = new EmbedBuilder()
        .setTitle('🎭 Rôle(s) retiré(s)')
        .setColor(COLORS.RED)
        .addFields(
          { name: '👤 Membre', value: `${newMember.user} (${newMember.user.tag})`, inline: true },
          { name: '➖ Rôle(s)', value: removedRoles.map(r => r.toString()).join(', '), inline: true },
        )
        .setThumbnail(newMember.user.displayAvatarURL({ dynamic: true }))
        .setTimestamp();

      await sendLog(newMember.guild, embed);
    }

    // ===================================
    // Changement de surnom
    // ===================================
    if (oldMember.nickname !== newMember.nickname) {
      const embed = new EmbedBuilder()
        .setTitle('📝 Surnom modifié')
        .setColor(COLORS.YELLOW)
        .addFields(
          { name: '👤 Membre', value: `${newMember.user} (${newMember.user.tag})`, inline: true },
          { name: '📛 Avant', value: oldMember.nickname || '*Aucun*', inline: true },
          { name: '📛 Après', value: newMember.nickname || '*Aucun*', inline: true },
        )
        .setThumbnail(newMember.user.displayAvatarURL({ dynamic: true }))
        .setTimestamp();

      await sendLog(newMember.guild, embed);
    }

    // ===================================
    // Timeout ajouté/retiré
    // ===================================
    const wasMuted = oldMember.communicationDisabledUntilTimestamp;
    const isMuted = newMember.communicationDisabledUntilTimestamp;

    if (!wasMuted && isMuted) {
      const embed = new EmbedBuilder()
        .setTitle('🔇 Timeout appliqué')
        .setColor(COLORS.ORANGE)
        .addFields(
          { name: '👤 Membre', value: `${newMember.user} (${newMember.user.tag})`, inline: true },
          { name: '⏱️ Expire', value: `<t:${Math.floor(isMuted / 1000)}:R>`, inline: true },
        )
        .setThumbnail(newMember.user.displayAvatarURL({ dynamic: true }))
        .setTimestamp();

      await sendLog(newMember.guild, embed);
    } else if (wasMuted && !isMuted) {
      const embed = new EmbedBuilder()
        .setTitle('🔊 Timeout retiré')
        .setColor(COLORS.GREEN)
        .addFields(
          { name: '👤 Membre', value: `${newMember.user} (${newMember.user.tag})`, inline: true },
        )
        .setThumbnail(newMember.user.displayAvatarURL({ dynamic: true }))
        .setTimestamp();

      await sendLog(newMember.guild, embed);
    }
  },
};
