// ===================================
// Ultra Suite — Poll vote handler
// ===================================

const { EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  customIds: ['poll_vote_'],
  type: 'button',

  async execute(interaction) {
    if (!interaction.customId.startsWith('poll_vote_')) return;
    const optionIdx = parseInt(interaction.customId.replace('poll_vote_', ''));
    const db = getDb();
    const guildId = interaction.guildId;

    const poll = await db('polls').where({ guild_id: guildId, message_id: interaction.message.id, status: 'active' }).first();
    if (!poll) return interaction.reply({ content: '❌ Sondage terminé.', ephemeral: true });

    const options = JSON.parse(poll.options);
    const userId = interaction.user.id;

    // Check if user already voted
    const existingVote = options.findIndex((o) => o.votes?.includes(userId));

    if (existingVote >= 0 && !poll.multiple_choice) {
      // Remove old vote
      options[existingVote].votes = options[existingVote].votes.filter((v) => v !== userId);
    }

    if (existingVote === optionIdx && !poll.multiple_choice) {
      // Toggle off
      await db('polls').where({ id: poll.id }).update({ options: JSON.stringify(options) });
      return interaction.reply({ content: '❌ Vote retiré.', ephemeral: true });
    }

    // Add vote
    if (!options[optionIdx].votes) options[optionIdx].votes = [];
    if (options[optionIdx].votes.includes(userId)) {
      // Toggle off in multi-choice
      options[optionIdx].votes = options[optionIdx].votes.filter((v) => v !== userId);
      await db('polls').where({ id: poll.id }).update({ options: JSON.stringify(options) });
      return interaction.reply({ content: '❌ Vote retiré.', ephemeral: true });
    }

    options[optionIdx].votes.push(userId);
    await db('polls').where({ id: poll.id }).update({ options: JSON.stringify(options) });

    // Update embed
    const totalVotes = options.reduce((sum, o) => sum + (o.votes?.length || 0), 0);
    const embed = EmbedBuilder.from(interaction.message.embeds[0])
      .setDescription(options.map((o) => {
        const count = o.votes?.length || 0;
        const pct = totalVotes > 0 ? Math.round(count / totalVotes * 100) : 0;
        return `${o.emoji} **${o.label}** — ${count} vote(s) (${pct}%)`;
      }).join('\n'));

    await interaction.update({ embeds: [embed] });
  },
};
