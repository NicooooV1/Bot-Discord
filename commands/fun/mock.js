// ===================================
// Ultra Suite — /mock
// Texte mOcKiNg
// ===================================

const { SlashCommandBuilder } = require('discord.js');

module.exports = {
  module: 'fun',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('mock')
    .setDescription('Transformer du texte en MoCkInG tExT')
    .addStringOption((o) => o.setName('texte').setDescription('Texte à mocker').setRequired(true)),

  async execute(interaction) {
    const text = interaction.options.getString('texte');
    const mocked = text.split('').map((c, i) => i % 2 === 0 ? c.toLowerCase() : c.toUpperCase()).join('');
    return interaction.reply({ content: `🐔 ${mocked}` });
  },
};
