// ===================================
// Ultra Suite — /translate
// Traduction
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

const LANGUAGES = [
  { name: 'Français', value: 'fr' }, { name: 'Anglais', value: 'en' },
  { name: 'Espagnol', value: 'es' }, { name: 'Allemand', value: 'de' },
  { name: 'Italien', value: 'it' }, { name: 'Portugais', value: 'pt' },
  { name: 'Russe', value: 'ru' }, { name: 'Japonais', value: 'ja' },
  { name: 'Chinois', value: 'zh' }, { name: 'Arabe', value: 'ar' },
  { name: 'Coréen', value: 'ko' }, { name: 'Néerlandais', value: 'nl' },
];

module.exports = {
  module: 'utility',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('translate')
    .setDescription('Traduire du texte')
    .addStringOption((o) => o.setName('texte').setDescription('Texte à traduire').setRequired(true))
    .addStringOption((o) => o.setName('cible').setDescription('Langue cible').setRequired(true).addChoices(...LANGUAGES))
    .addStringOption((o) => o.setName('source').setDescription('Langue source (auto si omis)').addChoices(...LANGUAGES)),

  async execute(interaction) {
    await interaction.deferReply();
    const text = interaction.options.getString('texte');
    const to = interaction.options.getString('cible');
    const from = interaction.options.getString('source') || 'auto';

    try {
      let translate;
      try { translate = require('translate-google'); } catch (e) { translate = null; }

      let result;
      if (translate) {
        result = await translate(text, { from, to });
      } else {
        result = '[translate-google non installé — texte original] ' + text;
      }

      const langName = (code) => LANGUAGES.find((l) => l.value === code)?.name ?? code;

      const embed = new EmbedBuilder()
        .setTitle('🌐 Traduction')
        .setColor(0x3498DB)
        .addFields(
          { name: `📥 ${from === 'auto' ? 'Auto-détecté' : langName(from)}`, value: text.substring(0, 1024) },
          { name: `📤 ${langName(to)}`, value: String(result).substring(0, 1024) },
        )
        .setTimestamp();

      return interaction.editReply({ embeds: [embed] });
    } catch (e) {
      return interaction.editReply({ content: `❌ Erreur de traduction : ${e.message}` });
    }
  },
};
