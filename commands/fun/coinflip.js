// ===================================
// Ultra Suite — Fun: /coinflip
// ===================================

const { SlashCommandBuilder } = require('discord.js');

module.exports = {
  module: 'fun',
  data: new SlashCommandBuilder().setName('coinflip').setDescription('Lance une pièce 🪙'),

  async execute(interaction) {
    const result = Math.random() < 0.5 ? '🪙 **Pile !**' : '🪙 **Face !**';
    return interaction.reply({ content: result });
  },
};
