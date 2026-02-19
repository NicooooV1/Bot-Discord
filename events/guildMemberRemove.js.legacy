const { EmbedBuilder } = require('discord.js');
const { getGuildConfig } = require('../utils/database');
const { memberLog, COLORS } = require('../utils/logger');

module.exports = {
  name: 'guildMemberRemove',
  async execute(member) {
    const config = getGuildConfig(member.guild.id);

    // ===================================
    // Log de départ
    // ===================================
    const roles = member.roles.cache
      .filter(r => r.id !== member.guild.id)
      .map(r => r.toString())
      .join(', ') || 'Aucun rôle';

    await memberLog(member.guild, {
      action: 'Membre parti',
      member,
      color: COLORS.RED,
      extra: [
        { name: '👥 Membres', value: `${member.guild.memberCount}`, inline: true },
        { name: '🎭 Rôles', value: roles.substring(0, 1024) },
      ],
    });

    // ===================================
    // Message de départ
    // ===================================
    if (!config?.welcome_channel_id) return;

    const channel = member.guild.channels.cache.get(config.welcome_channel_id);
    if (!channel) return;

    const leaveText = (config.leave_message || '**{user}** a quitté le serveur.')
      .replace(/{user}/g, member.user.tag)
      .replace(/{username}/g, member.user.username)
      .replace(/{tag}/g, member.user.tag)
      .replace(/{server}/g, member.guild.name)
      .replace(/{memberCount}/g, member.guild.memberCount);

    const embed = new EmbedBuilder()
      .setTitle('👋 Au revoir...')
      .setDescription(leaveText)
      .setColor(COLORS.GREY)
      .setThumbnail(member.user.displayAvatarURL({ dynamic: true }))
      .setTimestamp();

    try {
      await channel.send({ embeds: [embed] });
    } catch (error) {
      console.error('[LEAVE]', error.message);
    }
  },
};
