// ===================================
// Ultra Suite — guildBanAdd / guildBanRemove events
// ===================================

const { EmbedBuilder, AuditLogEvent } = require('discord.js');
const { getDb } = require('../database');

async function getLogChannel(guild) {
  const db = getDb();
  const config = await db('guild_config').where({ guild_id: guild.id }).first();
  if (!config) return null;
  const settings = config.settings ? JSON.parse(config.settings) : {};
  const logChannelId = settings.log_channel || settings.logChannel;
  if (!logChannelId) return null;
  return guild.channels.fetch(logChannelId).catch(() => null);
}

module.exports = [
  {
    name: 'guildBanAdd',
    async execute(ban) {
      try {
        const ch = await getLogChannel(ban.guild);
        if (!ch) return;

        let executor = 'Inconnu', reason = ban.reason || 'Aucune raison';
        try {
          const audit = await ban.guild.fetchAuditLogs({ type: AuditLogEvent.MemberBanAdd, limit: 1 });
          const entry = audit.entries.first();
          if (entry) {
            executor = entry.executor?.tag || 'Inconnu';
            reason = entry.reason || reason;
          }
        } catch (e) {}

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('🔨 Membre banni')
            .setColor(0xE74C3C)
            .setThumbnail(ban.user.displayAvatarURL())
            .addFields(
              { name: 'Utilisateur', value: `${ban.user.tag} (${ban.user.id})`, inline: true },
              { name: 'Banni par', value: executor, inline: true },
              { name: 'Raison', value: reason },
            )
            .setTimestamp()],
        });
      } catch (e) {}
    },
  },
  {
    name: 'guildBanRemove',
    async execute(ban) {
      try {
        const ch = await getLogChannel(ban.guild);
        if (!ch) return;

        let executor = 'Inconnu';
        try {
          const audit = await ban.guild.fetchAuditLogs({ type: AuditLogEvent.MemberBanRemove, limit: 1 });
          executor = audit.entries.first()?.executor?.tag || 'Inconnu';
        } catch (e) {}

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('🔓 Membre débanni')
            .setColor(0x2ECC71)
            .setThumbnail(ban.user.displayAvatarURL())
            .addFields(
              { name: 'Utilisateur', value: `${ban.user.tag} (${ban.user.id})`, inline: true },
              { name: 'Débanni par', value: executor, inline: true },
            )
            .setTimestamp()],
        });
      } catch (e) {}
    },
  },
];
