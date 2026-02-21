// ===================================
// Ultra Suite — /meme
// Générateur de memes
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

module.exports = {
  module: 'fun',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('meme')
    .setDescription('Obtenir un meme aléatoire')
    .addStringOption((o) => o.setName('subreddit').setDescription('Subreddit (défaut: memes)').addChoices(
      { name: 'memes', value: 'memes' },
      { name: 'dankmemes', value: 'dankmemes' },
      { name: 'me_irl', value: 'me_irl' },
      { name: 'wholesomememes', value: 'wholesomememes' },
      { name: 'programmerhumor', value: 'ProgrammerHumor' },
      { name: 'rance', value: 'rance' },
    )),

  async execute(interaction) {
    await interaction.deferReply();
    const sub = interaction.options.getString('subreddit') || 'memes';

    try {
      const res = await fetch(`https://meme-api.com/gimme/${sub}`);
      if (!res.ok) throw new Error('API indisponible');
      const data = await res.json();

      if (data.nsfw) {
        return interaction.editReply({ content: '❌ Ce meme est NSFW, passage au suivant...', ephemeral: true });
      }

      const embed = new EmbedBuilder()
        .setTitle(data.title?.substring(0, 256) || 'Meme')
        .setColor(0xFF4500)
        .setImage(data.url)
        .setFooter({ text: `👍 ${data.ups || 0} • r/${data.subreddit} • Par ${data.author || 'inconnu'}` })
        .setURL(data.postLink || null);

      return interaction.editReply({ embeds: [embed] });
    } catch (e) {
      return interaction.editReply({ content: `❌ Erreur : ${e.message}` });
    }
  },
};
