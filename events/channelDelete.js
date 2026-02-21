// ===================================
// Ultra Suite — channelDelete event
// ===================================

const { EmbedBuilder, AuditLogEvent } = require('discord.js');
const { getDb } = require('../database');

module.exports = {
  name: 'channelDelete',
  async execute(channel) {
    if (!channel.guild) return;
    const db = getDb();
    try {
      const config = await db('guild_config').where({ guild_id: channel.guild.id }).first();
      if (!config) return;
      const settings = config.settings ? JSON.parse(config.settings) : {};
      const logChannel = settings.log_channel || settings.logChannel;
      if (!logChannel) return;

      const ch = await channel.guild.channels.fetch(logChannel).catch(() => null);
      if (!ch) return;

      let executor = 'Inconnu';
      try {
        const audit = await channel.guild.fetchAuditLogs({ type: AuditLogEvent.ChannelDelete, limit: 1 });
        executor = audit.entries.first()?.executor?.tag || 'Inconnu';
      } catch (e) {}

      // Anti-nuke check
      const guildConfig = await db('guild_config').where({ guild_id: channel.guild.id }).first();
      const modules = guildConfig?.modules ? JSON.parse(guildConfig.modules) : {};
      if (modules.antinuke?.enabled) {
        await db('antinuke_log').insert({
          guild_id: channel.guild.id,
          user_id: 'system',
          action: 'channel_delete',
          details: `Salon supprimé: ${channel.name} par ${executor}`,
        }).catch(() => {});
      }

      await ch.send({
        embeds: [new EmbedBuilder()
          .setTitle('🗑️ Salon supprimé')
          .setColor(0xE74C3C)
          .addFields(
            { name: 'Salon', value: `\`${channel.name}\``, inline: true },
            { name: 'Type', value: String(channel.type), inline: true },
            { name: 'Supprimé par', value: executor, inline: true },
          )
          .setTimestamp()],
      });
    } catch (e) {}
  },
};
