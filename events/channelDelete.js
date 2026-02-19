const { EmbedBuilder, AuditLogEvent } = require('discord.js');
const { sendLog, COLORS } = require('../utils/logger');

module.exports = {
  name: 'channelDelete',
  async execute(channel) {
    if (!channel.guild) return;

    let executor = null;
    try {
      const auditLogs = await channel.guild.fetchAuditLogs({
        type: AuditLogEvent.ChannelDelete,
        limit: 1,
      });
      const log = auditLogs.entries.first();
      if (log && log.target.id === channel.id && Date.now() - log.createdTimestamp < 5000) {
        executor = log.executor;
      }
    } catch { /* Pas de permission */ }

    const embed = new EmbedBuilder()
      .setTitle('🗑️ Salon supprimé')
      .setColor(COLORS.RED)
      .addFields(
        { name: '💬 Salon', value: `\`#${channel.name}\``, inline: true },
        { name: '📂 Type', value: `\`${channel.type}\``, inline: true },
      )
      .setTimestamp();

    if (channel.parent) {
      embed.addFields({ name: '📁 Catégorie', value: channel.parent.name, inline: true });
    }
    if (executor) {
      embed.addFields({ name: '👤 Supprimé par', value: `${executor} (${executor.tag})` });
    }

    await sendLog(channel.guild, embed);
  },
};
