// ===================================
// Ultra Suite — threadCreate / threadDelete events
// ===================================

const { EmbedBuilder } = require('discord.js');
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
    name: 'threadCreate',
    async execute(thread, newlyCreated) {
      if (!newlyCreated) return;
      try {
        if (!thread.guild) return;
        const ch = await getLogChannel(thread.guild);
        if (!ch) return;

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('🧵 Thread créé')
            .setColor(0x2ECC71)
            .addFields(
              { name: 'Thread', value: `${thread} (\`${thread.name}\`)`, inline: true },
              { name: 'Salon parent', value: thread.parent ? `${thread.parent}` : 'N/A', inline: true },
              { name: 'Créé par', value: thread.ownerId ? `<@${thread.ownerId}>` : 'Inconnu', inline: true },
            )
            .setTimestamp()],
        });

        // Forum auto-react
        const db = getDb();
        const forumConfig = await db('forum_config').where({ guild_id: thread.guild.id, channel_id: thread.parentId }).first();
        if (forumConfig?.auto_react) {
          const starterMessage = await thread.fetchStarterMessage().catch(() => null);
          if (starterMessage) {
            await starterMessage.react('👍').catch(() => {});
            await starterMessage.react('👎').catch(() => {});
          }
        }
      } catch (e) {}
    },
  },
  {
    name: 'threadDelete',
    async execute(thread) {
      try {
        if (!thread.guild) return;
        const ch = await getLogChannel(thread.guild);
        if (!ch) return;

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('🗑️ Thread supprimé')
            .setColor(0xE74C3C)
            .addFields(
              { name: 'Thread', value: `\`${thread.name}\``, inline: true },
              { name: 'Salon parent', value: thread.parent ? `${thread.parent}` : 'N/A', inline: true },
            )
            .setTimestamp()],
        });
      } catch (e) {}
    },
  },
];
