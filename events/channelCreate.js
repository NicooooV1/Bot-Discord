const { EmbedBuilder, AuditLogEvent } = require('discord.js');
const { sendLog, COLORS } = require('../utils/logger');

module.exports = {
  name: 'channelCreate',
  async execute(channel) {
    if (!channel.guild) return;

    // Essayer de trouver qui a créé le salon via les audit logs
    let executor = null;
    try {
      const auditLogs = await channel.guild.fetchAuditLogs({
        type: AuditLogEvent.ChannelCreate,
        limit: 1,
      });
      const log = auditLogs.entries.first();
      if (log && log.target.id === channel.id && Date.now() - log.createdTimestamp < 5000) {
        executor = log.executor;
      }
    } catch { /* Pas de permission d'accès aux audit logs */ }

    const embed = new EmbedBuilder()
      .setTitle('📌 Salon créé')
      .setColor(COLORS.GREEN)
      .addFields(
        { name: '💬 Salon', value: `${channel} (\`${channel.name}\`)`, inline: true },
        { name: '📂 Type', value: `\`${channel.type}\``, inline: true },
      )
      .setTimestamp();

    if (channel.parent) {
      embed.addFields({ name: '📁 Catégorie', value: channel.parent.name, inline: true });
    }
    if (executor) {
      embed.addFields({ name: '👤 Créé par', value: `${executor} (${executor.tag})` });
    }

    await sendLog(channel.guild, embed);
  },
};
