// ===================================
// Ultra Suite — emojiCreate / emojiDelete / emojiUpdate
// + webhookUpdate events
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
    name: 'emojiCreate',
    async execute(emoji) {
      try {
        const ch = await getLogChannel(emoji.guild);
        if (!ch) return;

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('😀 Emoji ajouté')
            .setColor(0x2ECC71)
            .setThumbnail(emoji.url)
            .addFields(
              { name: 'Emoji', value: `${emoji} \`:${emoji.name}:\``, inline: true },
              { name: 'Animé', value: emoji.animated ? '✅' : '❌', inline: true },
            )
            .setTimestamp()],
        });
      } catch (e) {}
    },
  },
  {
    name: 'emojiDelete',
    async execute(emoji) {
      try {
        const ch = await getLogChannel(emoji.guild);
        if (!ch) return;

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('🗑️ Emoji supprimé')
            .setColor(0xE74C3C)
            .addFields({ name: 'Emoji', value: `\`:${emoji.name}:\``, inline: true })
            .setTimestamp()],
        });
      } catch (e) {}
    },
  },
  {
    name: 'emojiUpdate',
    async execute(oldEmoji, newEmoji) {
      try {
        const ch = await getLogChannel(newEmoji.guild);
        if (!ch) return;

        if (oldEmoji.name === newEmoji.name) return;

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('✏️ Emoji modifié')
            .setColor(0xF39C12)
            .setThumbnail(newEmoji.url)
            .addFields(
              { name: 'Ancien nom', value: `:${oldEmoji.name}:`, inline: true },
              { name: 'Nouveau nom', value: `:${newEmoji.name}:`, inline: true },
            )
            .setTimestamp()],
        });
      } catch (e) {}
    },
  },
  {
    name: 'webhookUpdate',
    async execute(channel) {
      try {
        if (!channel.guild) return;
        const ch = await getLogChannel(channel.guild);
        if (!ch) return;

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('🔗 Webhook modifié')
            .setColor(0xF39C12)
            .addFields({ name: 'Salon', value: `${channel} (\`${channel.name}\`)` })
            .setTimestamp()],
        });
      } catch (e) {}
    },
  },
];
