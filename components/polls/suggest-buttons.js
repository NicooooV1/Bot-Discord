// ===================================
// Ultra Suite — Suggestion vote handler
// ===================================

const { EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  customIds: ['suggest_up_', 'suggest_down_'],
  type: 'button',

  async execute(interaction) {
    const db = getDb();
    const guildId = interaction.guildId;
    const [, direction, id] = interaction.customId.match(/suggest_(up|down)_(\d+)/) || [];
    if (!id) return;

    const suggestion = await db('suggestions').where({ guild_id: guildId, id }).first();
    if (!suggestion) return interaction.reply({ content: '❌ Suggestion introuvable.', ephemeral: true });

    if (direction === 'up') {
      await db('suggestions').where({ id }).increment('upvotes', 1);
    } else {
      await db('suggestions').where({ id }).increment('downvotes', 1);
    }

    const updated = await db('suggestions').where({ id }).first();

    const embed = EmbedBuilder.from(interaction.message.embeds[0]);
    const votesField = embed.data.fields?.find((f) => f.name === 'Votes');
    if (votesField) {
      votesField.value = `👍 ${updated.upvotes} | 👎 ${updated.downvotes}`;
    }

    await interaction.update({ embeds: [embed] });
  },
};
