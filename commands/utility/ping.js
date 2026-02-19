// ===================================
// Ultra Suite — Utility: /ping
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const { createEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'utility',
  cooldown: 3,
  data: new SlashCommandBuilder().setName('ping').setDescription('Affiche la latence du bot'),

  async execute(interaction) {
    const sent = await interaction.reply({ content: '🏓 Ping...', fetchReply: true });
    const roundtrip = sent.createdTimestamp - interaction.createdTimestamp;
    const ws = interaction.client.ws.ping;

    const embed = createEmbed('primary')
      .setTitle('🏓 Pong !')
      .addFields(
        { name: 'Latence API', value: `${roundtrip}ms`, inline: true },
        { name: 'WebSocket', value: `${ws}ms`, inline: true },
        { name: 'Uptime', value: `<t:${Math.floor((Date.now() - interaction.client.uptime) / 1000)}:R>`, inline: true }
      );

    return interaction.editReply({ content: null, embeds: [embed] });
  },
};
