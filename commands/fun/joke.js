// ===================================
// Ultra Suite — /joke
// Blagues aléatoires
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

const JOKES_FR = [
  { q: 'Qu\'est-ce qu\'un canif ?', a: 'Un petit fien.' },
  { q: 'C\'est quoi un crocodile qui surveille un parking ?', a: 'Un lot-qui-garde.' },
  { q: 'Qu\'est-ce qu\'un chat tombé dans un pot de peinture le jour de Noël ?', a: 'Un chat-peint de Noël.' },
  { q: 'Pourquoi les plongeurs plongent-ils toujours en arrière et jamais en avant ?', a: 'Parce que sinon ils tomberaient dans le bateau.' },
  { q: 'Que dit un informaticien quand il s\'ennuie ?', a: 'Je suis en mode veille.' },
  { q: 'C\'est l\'histoire d\'un tétard qui croyait qu\'il était tôt...', a: 'Mais en fait, il était tard (têtard).' },
  { q: 'Pourquoi les éoliennes ne tournent pas rond ?', a: 'Parce qu\'elles ont un vent de folie.' },
  { q: 'Qu\'est-ce qu\'un Pokémon qui cuisine ?', a: 'Un chef Pikachu.' },
  { q: 'Pourquoi le champignon est invité à toutes les fêtes ?', a: 'Parce que c\'est un champignon.' },
  { q: 'Qu\'est-ce qu\'un thon sans tête ?', a: 'Un on.' },
];

module.exports = {
  module: 'fun',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('joke')
    .setDescription('Entendre une blague')
    .addStringOption((o) => o.setName('type').setDescription('Type de blague').addChoices(
      { name: 'Aléatoire', value: 'random' },
      { name: 'Dark', value: 'dark' },
      { name: 'Dev', value: 'dev' },
    )),

  async execute(interaction) {
    const type = interaction.options.getString('type') || 'random';

    // Try external API first
    try {
      let url = 'https://v2.jokeapi.dev/joke/Any?lang=fr&type=twopart';
      if (type === 'dark') url = 'https://v2.jokeapi.dev/joke/Dark?lang=fr&type=twopart';
      else if (type === 'dev') url = 'https://v2.jokeapi.dev/joke/Programming?type=twopart';

      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        if (data.setup && data.delivery) {
          const embed = new EmbedBuilder()
            .setTitle('😂 Blague')
            .setColor(0xF1C40F)
            .setDescription(`**${data.setup}**\n\n||${data.delivery}||`)
            .setFooter({ text: `Catégorie: ${data.category}` });
          return interaction.reply({ embeds: [embed] });
        }
      }
    } catch (e) { /* fallback */ }

    // Fallback to local jokes
    const joke = JOKES_FR[Math.floor(Math.random() * JOKES_FR.length)];
    const embed = new EmbedBuilder()
      .setTitle('😂 Blague')
      .setColor(0xF1C40F)
      .setDescription(`**${joke.q}**\n\n||${joke.a}||`);

    return interaction.reply({ embeds: [embed] });
  },
};
