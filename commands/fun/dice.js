// ===================================
// Ultra Suite — Fun: /dice
// ===================================

const { SlashCommandBuilder } = require('discord.js');

module.exports = {
  module: 'fun',
  data: new SlashCommandBuilder()
    .setName('dice')
    .setDescription('Lance un dé 🎲')
    .addIntegerOption((opt) =>
      opt.setName('faces').setDescription('Nombre de faces (défaut: 6)').setMinValue(2).setMaxValue(100)
    )
    .addIntegerOption((opt) =>
      opt.setName('nombre').setDescription('Nombre de dés (défaut: 1)').setMinValue(1).setMaxValue(10)
    ),

  async execute(interaction) {
    const faces = interaction.options.getInteger('faces') || 6;
    const count = interaction.options.getInteger('nombre') || 1;

    const results = [];
    for (let i = 0; i < count; i++) {
      results.push(Math.floor(Math.random() * faces) + 1);
    }

    const total = results.reduce((a, b) => a + b, 0);
    const display = results.map((r) => `**${r}**`).join(' + ');

    return interaction.reply({
      content: `🎲 ${display}${count > 1 ? ` = **${total}**` : ''}`,
    });
  },
};
