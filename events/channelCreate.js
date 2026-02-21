// ===================================
// Ultra Suite — channelCreate event
// Log channel creation
// ===================================

const { EmbedBuilder, AuditLogEvent } = require('discord.js');
const { getDb } = require('../database');

module.exports = {
  name: 'channelCreate',
  async execute(channel) {
    if (!channel.guild) return;
    const db = getDb();
    const guildId = channel.guild.id;

    try {
      const config = await db('guild_config').where({ guild_id: guildId }).first();
      if (!config) return;
      const settings = config.settings ? JSON.parse(config.settings) : {};
      const logChannel = settings.log_channel || settings.logChannel;
      if (!logChannel) return;

      const ch = await channel.guild.channels.fetch(logChannel).catch(() => null);
      if (!ch) return;

      let executor = 'Inconnu';
      try {
        const audit = await channel.guild.fetchAuditLogs({ type: AuditLogEvent.ChannelCreate, limit: 1 });
        executor = audit.entries.first()?.executor?.tag || 'Inconnu';
      } catch (e) {}

      const embed = new EmbedBuilder()
        .setTitle('📝 Salon créé')
        .setColor(0x2ECC71)
        .addFields(
          { name: 'Salon', value: `${channel} (\`${channel.name}\`)`, inline: true },
          { name: 'Type', value: String(channel.type), inline: true },
          { name: 'Créé par', value: executor, inline: true },
        )
        .setTimestamp();

      if (channel.parent) embed.addFields({ name: 'Catégorie', value: channel.parent.name, inline: true });

      await ch.send({ embeds: [embed] });
    } catch (e) {}
  },
};
