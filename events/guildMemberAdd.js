const { EmbedBuilder } = require('discord.js');
const { getGuildConfig } = require('../utils/database');
const { memberLog, COLORS } = require('../utils/logger');

module.exports = {
  name: 'guildMemberAdd',
  async execute(member) {
    const config = getGuildConfig(member.guild.id);

    // ===================================
    // Log d'arrivée
    // ===================================
    await memberLog(member.guild, {
      action: 'Membre rejoint',
      member,
      color: COLORS.GREEN,
      extra: { name: '👥 Membres', value: `${member.guild.memberCount}`, inline: true },
    });

    // ===================================
    // Message de bienvenue
    // ===================================
    if (!config?.welcome_channel_id) return;

    const channel = member.guild.channels.cache.get(config.welcome_channel_id);
    if (!channel) return;

    const welcomeText = (config.welcome_message || 'Bienvenue {user} sur **{server}** !')
      .replace(/{user}/g, member)
      .replace(/{username}/g, member.user.username)
      .replace(/{tag}/g, member.user.tag)
      .replace(/{server}/g, member.guild.name)
      .replace(/{memberCount}/g, member.guild.memberCount);

    const embed = new EmbedBuilder()
      .setTitle('👋 Bienvenue !')
      .setDescription(welcomeText)
      .setColor(COLORS.GREEN)
      .setThumbnail(member.user.displayAvatarURL({ dynamic: true, size: 256 }))
      .setFooter({ text: `Membre #${member.guild.memberCount}` })
      .setTimestamp();

    try {
      await channel.send({ embeds: [embed] });
    } catch (error) {
      console.error('[WELCOME]', error.message);
    }
  },
};
