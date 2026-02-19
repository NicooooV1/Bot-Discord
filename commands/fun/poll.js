// ===================================
// Ultra Suite — Fun: /poll
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const { createEmbed } = require('../../utils/embeds');

const EMOJIS = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'];

module.exports = {
  module: 'fun',
  cooldown: 10,
  data: new SlashCommandBuilder()
    .setName('poll')
    .setDescription('Crée un sondage')
    .addStringOption((opt) => opt.setName('question').setDescription('La question').setRequired(true))
    .addStringOption((opt) => opt.setName('choix').setDescription('Choix séparés par | (ex: Oui | Non | Peut-être)'))
    .addBooleanOption((opt) => opt.setName('anonymous').setDescription('Réponse anonyme ?')),

  async execute(interaction) {
    const question = interaction.options.getString('question');
    const choicesRaw = interaction.options.getString('choix');

    if (!choicesRaw) {
      // Sondage oui/non
      const embed = createEmbed('primary')
        .setTitle('📊 Sondage')
        .setDescription(question)
        .setFooter({ text: `Par ${interaction.user.tag}` });

      const msg = await interaction.reply({ embeds: [embed], fetchReply: true });
      await msg.react('👍');
      await msg.react('👎');
      return;
    }

    const choices = choicesRaw
      .split('|')
      .map((c) => c.trim())
      .filter(Boolean)
      .slice(0, 10);

    if (choices.length < 2) {
      return interaction.reply({ content: '❌ Il faut au moins 2 choix.', ephemeral: true });
    }

    const description = choices.map((c, i) => `${EMOJIS[i]} ${c}`).join('\n');

    const embed = createEmbed('primary')
      .setTitle(`📊 ${question}`)
      .setDescription(description)
      .setFooter({ text: `Par ${interaction.user.tag} · ${choices.length} choix` });

    const msg = await interaction.reply({ embeds: [embed], fetchReply: true });
    for (let i = 0; i < choices.length; i++) {
      await msg.react(EMOJIS[i]);
    }
  },
};
