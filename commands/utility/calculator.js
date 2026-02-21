// ===================================
// Ultra Suite — /calculator
// Calculatrice mathématique
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

module.exports = {
  module: 'utility',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('calculator')
    .setDescription('Calculatrice mathématique')
    .addStringOption((o) => o.setName('expression').setDescription('Expression à calculer (ex: 2+2, sqrt(16), sin(pi))').setRequired(true)),

  async execute(interaction) {
    const expr = interaction.options.getString('expression');

    try {
      let mathjs;
      try { mathjs = require('mathjs'); } catch (e) { mathjs = null; }

      let result;
      if (mathjs) {
        result = mathjs.evaluate(expr);
      } else {
        // Fallback — safe eval math only
        const sanitized = expr.replace(/[^0-9+\-*/().%^ ]/g, '');
        if (!sanitized) throw new Error('Expression invalide');
        result = Function(`"use strict"; return (${sanitized})`)();
      }

      const embed = new EmbedBuilder()
        .setTitle('🔢 Calculatrice')
        .setColor(0x3498DB)
        .addFields(
          { name: 'Expression', value: `\`\`\`${expr}\`\`\`` },
          { name: 'Résultat', value: `\`\`\`${String(result)}\`\`\`` },
        );

      return interaction.reply({ embeds: [embed] });
    } catch (e) {
      return interaction.reply({ content: `❌ Expression invalide : ${e.message}`, ephemeral: true });
    }
  },
};
