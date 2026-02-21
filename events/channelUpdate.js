// ===================================
// Ultra Suite — channelUpdate event
// ===================================

const { EmbedBuilder, AuditLogEvent } = require('discord.js');
const { getDb } = require('../database');

module.exports = {
  name: 'channelUpdate',
  async execute(oldChannel, newChannel) {
    if (!newChannel.guild) return;
    const db = getDb();
    try {
      const config = await db('guild_config').where({ guild_id: newChannel.guild.id }).first();
      if (!config) return;
      const settings = config.settings ? JSON.parse(config.settings) : {};
      const logChannel = settings.log_channel || settings.logChannel;
      if (!logChannel) return;

      const ch = await newChannel.guild.channels.fetch(logChannel).catch(() => null);
      if (!ch) return;

      const changes = [];
      if (oldChannel.name !== newChannel.name) changes.push(`📝 Nom: \`${oldChannel.name}\` → \`${newChannel.name}\``);
      if (oldChannel.topic !== newChannel.topic) changes.push(`📌 Sujet: \`${oldChannel.topic || 'aucun'}\` → \`${newChannel.topic || 'aucun'}\``);
      if (oldChannel.nsfw !== newChannel.nsfw) changes.push(`🔞 NSFW: ${oldChannel.nsfw} → ${newChannel.nsfw}`);
      if (oldChannel.rateLimitPerUser !== newChannel.rateLimitPerUser) changes.push(`🐌 Slowmode: ${oldChannel.rateLimitPerUser}s → ${newChannel.rateLimitPerUser}s`);
      if (oldChannel.parentId !== newChannel.parentId) changes.push(`📁 Catégorie changée`);

      if (!changes.length) return;

      await ch.send({
        embeds: [new EmbedBuilder()
          .setTitle('✏️ Salon modifié')
          .setColor(0xF39C12)
          .addFields(
            { name: 'Salon', value: `${newChannel} (\`${newChannel.name}\`)` },
            { name: 'Changements', value: changes.join('\n') },
          )
          .setTimestamp()],
      });
    } catch (e) {}
  },
};
