const { EmbedBuilder } = require('discord.js');
const { sendLog, COLORS } = require('../utils/logger');

module.exports = {
  name: 'voiceStateUpdate',
  async execute(oldState, newState) {
    const member = newState.member || oldState.member;
    if (!member || member.user.bot) return;

    const guild = newState.guild || oldState.guild;

    let embed = null;

    // Rejoint un salon vocal
    if (!oldState.channelId && newState.channelId) {
      embed = new EmbedBuilder()
        .setTitle('🔊 Rejoint un salon vocal')
        .setColor(COLORS.GREEN)
        .addFields(
          { name: '👤 Membre', value: `${member.user} (${member.user.tag})`, inline: true },
          { name: '🔊 Salon', value: `${newState.channel}`, inline: true },
        )
        .setThumbnail(member.user.displayAvatarURL({ dynamic: true }))
        .setTimestamp();
    }
    // Quitté un salon vocal
    else if (oldState.channelId && !newState.channelId) {
      embed = new EmbedBuilder()
        .setTitle('🔇 Quitté un salon vocal')
        .setColor(COLORS.RED)
        .addFields(
          { name: '👤 Membre', value: `${member.user} (${member.user.tag})`, inline: true },
          { name: '🔊 Salon', value: `${oldState.channel}`, inline: true },
        )
        .setThumbnail(member.user.displayAvatarURL({ dynamic: true }))
        .setTimestamp();
    }
    // Changé de salon vocal
    else if (oldState.channelId && newState.channelId && oldState.channelId !== newState.channelId) {
      embed = new EmbedBuilder()
        .setTitle('🔀 Changement de salon vocal')
        .setColor(COLORS.BLUE)
        .addFields(
          { name: '👤 Membre', value: `${member.user} (${member.user.tag})`, inline: true },
          { name: '⬅️ Ancien', value: `${oldState.channel}`, inline: true },
          { name: '➡️ Nouveau', value: `${newState.channel}`, inline: true },
        )
        .setThumbnail(member.user.displayAvatarURL({ dynamic: true }))
        .setTimestamp();
    }

    if (embed) {
      await sendLog(guild, embed);
    }
  },
};
