// ===================================
// Ultra Suite — /trivia
// Quiz interactif
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, ComponentType } = require('discord.js');

module.exports = {
  module: 'fun',
  cooldown: 10,

  data: new SlashCommandBuilder()
    .setName('trivia')
    .setDescription('Jouer à un quiz')
    .addStringOption((o) => o.setName('categorie').setDescription('Catégorie').addChoices(
      { name: 'Culture générale', value: '9' },
      { name: 'Sciences', value: '17' },
      { name: 'Informatique', value: '18' },
      { name: 'Jeux vidéo', value: '15' },
      { name: 'Films', value: '11' },
      { name: 'Musique', value: '12' },
      { name: 'Géographie', value: '22' },
      { name: 'Histoire', value: '23' },
    ))
    .addStringOption((o) => o.setName('difficulte').setDescription('Difficulté').addChoices(
      { name: 'Facile', value: 'easy' },
      { name: 'Moyen', value: 'medium' },
      { name: 'Difficile', value: 'hard' },
    )),

  async execute(interaction) {
    await interaction.deferReply();

    const category = interaction.options.getString('categorie') || '';
    const difficulty = interaction.options.getString('difficulte') || '';

    try {
      let url = 'https://opentdb.com/api.php?amount=1&type=multiple';
      if (category) url += `&category=${category}`;
      if (difficulty) url += `&difficulty=${difficulty}`;

      const res = await fetch(url);
      const data = await res.json();

      if (!data.results?.length) throw new Error('Aucune question trouvée');

      const q = data.results[0];
      const decode = (str) => str.replace(/&quot;/g, '"').replace(/&#039;/g, "'").replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');

      const question = decode(q.question);
      const correct = decode(q.correct_answer);
      const choices = [...q.incorrect_answers.map(decode), correct].sort(() => Math.random() - 0.5);
      const correctIdx = choices.indexOf(correct);

      const letters = ['🇦', '🇧', '🇨', '🇩'];
      const diffColors = { easy: 0x2ECC71, medium: 0xF39C12, hard: 0xE74C3C };
      const diffLabels = { easy: '🟢 Facile', medium: '🟡 Moyen', hard: '🔴 Difficile' };

      const embed = new EmbedBuilder()
        .setTitle('🧠 Trivia')
        .setColor(diffColors[q.difficulty] || 0x3498DB)
        .setDescription(`**${question}**\n\n${choices.map((c, i) => `${letters[i]} ${c}`).join('\n')}`)
        .addFields(
          { name: 'Catégorie', value: decode(q.category), inline: true },
          { name: 'Difficulté', value: diffLabels[q.difficulty] || q.difficulty, inline: true },
        )
        .setFooter({ text: '⏱️ 30 secondes pour répondre !' });

      const row = new ActionRowBuilder().addComponents(
        choices.map((c, i) => new ButtonBuilder().setCustomId(`trivia_${i}`).setLabel(letters[i]).setStyle(ButtonStyle.Secondary)),
      );

      const msg = await interaction.editReply({ embeds: [embed], components: [row] });
      const answered = new Set();

      const collector = msg.createMessageComponentCollector({
        componentType: ComponentType.Button,
        time: 30000,
      });

      const scores = {};

      collector.on('collect', async (btn) => {
        if (answered.has(btn.user.id)) return btn.reply({ content: '❌ Vous avez déjà répondu.', ephemeral: true });
        answered.add(btn.user.id);

        const chosen = parseInt(btn.customId.split('_')[1]);
        if (chosen === correctIdx) {
          scores[btn.user.id] = { correct: true, tag: btn.user.tag };
          await btn.reply({ content: '✅ Bonne réponse !', ephemeral: true });
        } else {
          scores[btn.user.id] = { correct: false, tag: btn.user.tag };
          await btn.reply({ content: `❌ Mauvaise réponse ! La bonne réponse était : **${correct}**`, ephemeral: true });
        }
      });

      collector.on('end', async () => {
        const winners = Object.values(scores).filter((s) => s.correct);
        const losers = Object.values(scores).filter((s) => !s.correct);

        const resultEmbed = new EmbedBuilder()
          .setTitle('🧠 Trivia — Résultat')
          .setColor(winners.length ? 0x2ECC71 : 0xE74C3C)
          .setDescription(`**${question}**\n\n✅ Réponse : **${correct}**`)
          .addFields(
            { name: `✅ Correct (${winners.length})`, value: winners.length ? winners.map((w) => w.tag).join(', ') : 'Personne', inline: true },
            { name: `❌ Incorrect (${losers.length})`, value: losers.length ? losers.map((l) => l.tag).join(', ') : 'Personne', inline: true },
          );

        await msg.edit({ embeds: [resultEmbed], components: [] }).catch(() => {});
      });
    } catch (e) {
      return interaction.editReply({ content: `❌ Erreur : ${e.message}` });
    }
  },
};
