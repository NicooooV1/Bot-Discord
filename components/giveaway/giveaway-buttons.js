// ===================================
// Ultra Suite — Giveaway Buttons Handler
// ===================================

const { EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  customIds: ['giveaway_enter_'],
  type: 'button',

  async execute(interaction) {
    if (!interaction.customId.startsWith('giveaway_enter_')) return;
    const giveawayId = interaction.customId.replace('giveaway_enter_', '');
    const db = getDb();
    const guildId = interaction.guildId;

    const giveaway = await db('giveaways').where({ guild_id: guildId, id: giveawayId, status: 'active' }).first();
    if (!giveaway) return interaction.reply({ content: '❌ Ce giveaway n\'est plus actif.', ephemeral: true });

    const participants = JSON.parse(giveaway.participants || '[]');

    if (participants.includes(interaction.user.id)) {
      // Remove participation
      const newParticipants = participants.filter((p) => p !== interaction.user.id);
      await db('giveaways').where({ id: giveawayId }).update({ participants: JSON.stringify(newParticipants) });
      return interaction.reply({ content: '❌ Vous avez retiré votre participation.', ephemeral: true });
    }

    // Check requirements
    if (giveaway.required_role) {
      const member = await interaction.guild.members.fetch(interaction.user.id).catch(() => null);
      if (member && !member.roles.cache.has(giveaway.required_role)) {
        return interaction.reply({ content: `❌ Vous avez besoin du rôle <@&${giveaway.required_role}> pour participer.`, ephemeral: true });
      }
    }

    participants.push(interaction.user.id);
    await db('giveaways').where({ id: giveawayId }).update({ participants: JSON.stringify(participants) });

    // Update embed
    try {
      const channel = await interaction.guild.channels.fetch(giveaway.channel_id);
      const msg = await channel.messages.fetch(giveaway.message_id);
      const embed = EmbedBuilder.from(msg.embeds[0]);
      const fields = embed.data.fields || [];
      const partField = fields.find((f) => f.name.includes('Participants'));
      if (partField) partField.value = `${participants.length} participant(s)`;
      await msg.edit({ embeds: [embed] });
    } catch (e) {}

    return interaction.reply({ content: `✅ Vous participez au giveaway ! (${participants.length} participant(s))`, ephemeral: true });
  },
};
