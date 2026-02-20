// ===================================
// Ultra Suite — /fun
// Commandes fun groupées
// /fun 8ball | coinflip | dice | rps | rate | hug
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

const EIGHT_BALL_RESPONSES = [
  // Positif
  'Oui, absolument !', 'C\'est certain.', 'Sans aucun doute.', 'Oui, définitivement.',
  'Tu peux compter dessus.', 'Les étoiles disent oui.', 'Bien sûr !', 'C\'est probable.',
  // Neutre
  'Peut-être...', 'Difficile à dire.', 'Concentre-toi et repose la question.',
  'Mieux vaut ne pas te le dire maintenant.', 'Redemande plus tard.',
  // Négatif
  'N\'y compte pas.', 'Ma réponse est non.', 'Les signaux sont négatifs.',
  'C\'est très peu probable.', 'Certainement pas.',
];

const RPS_EMOJIS = { rock: '🪨', paper: '📄', scissors: '✂️' };
const RPS_NAMES = { rock: 'Pierre', paper: 'Papier', scissors: 'Ciseaux' };

module.exports = {
  module: 'fun',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('fun')
    .setDescription('Commandes fun et mini-jeux')
    .addSubcommand((sub) =>
      sub.setName('8ball').setDescription('Poser une question à la boule magique')
        .addStringOption((opt) => opt.setName('question').setDescription('Votre question').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('coinflip').setDescription('Lancer une pièce'))
    .addSubcommand((sub) =>
      sub.setName('dice').setDescription('Lancer des dés')
        .addIntegerOption((opt) => opt.setName('faces').setDescription('Nombre de faces (défaut: 6)').setMinValue(2).setMaxValue(100))
        .addIntegerOption((opt) => opt.setName('nombre').setDescription('Nombre de dés (défaut: 1)').setMinValue(1).setMaxValue(10)))
    .addSubcommand((sub) =>
      sub.setName('rps').setDescription('Pierre-papier-ciseaux')
        .addStringOption((opt) => opt.setName('choix').setDescription('Votre choix').setRequired(true)
          .addChoices(
            { name: '🪨 Pierre', value: 'rock' },
            { name: '📄 Papier', value: 'paper' },
            { name: '✂️ Ciseaux', value: 'scissors' },
          )))
    .addSubcommand((sub) =>
      sub.setName('rate').setDescription('Noter quelque chose')
        .addStringOption((opt) => opt.setName('sujet').setDescription('Que voulez-vous noter ?').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('hug').setDescription('Envoyer un câlin')
        .addUserOption((opt) => opt.setName('membre').setDescription('À qui faire un câlin ?').setRequired(true))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();

    // === 8BALL ===
    if (sub === '8ball') {
      const question = interaction.options.getString('question');
      const answer = EIGHT_BALL_RESPONSES[Math.floor(Math.random() * EIGHT_BALL_RESPONSES.length)];

      const embed = new EmbedBuilder()
        .setTitle('🎱 Boule Magique')
        .addFields(
          { name: 'Question', value: question, inline: false },
          { name: 'Réponse', value: `**${answer}**`, inline: false },
        )
        .setColor(0x2F3136);

      return interaction.reply({ embeds: [embed] });
    }

    // === COINFLIP ===
    if (sub === 'coinflip') {
      const result = Math.random() < 0.5 ? 'Pile' : 'Face';
      const emoji = result === 'Pile' ? '🪙' : '💫';

      return interaction.reply({ content: `${emoji} La pièce tombe sur... **${result}** !` });
    }

    // === DICE ===
    if (sub === 'dice') {
      const faces = interaction.options.getInteger('faces') || 6;
      const count = interaction.options.getInteger('nombre') || 1;

      const rolls = [];
      for (let i = 0; i < count; i++) {
        rolls.push(Math.floor(Math.random() * faces) + 1);
      }

      const total = rolls.reduce((a, b) => a + b, 0);
      const diceStr = rolls.map((r) => `\`${r}\``).join(' + ');

      let content = `🎲 ${diceStr}`;
      if (count > 1) content += ` = **${total}**`;

      return interaction.reply({ content });
    }

    // === RPS ===
    if (sub === 'rps') {
      const userChoice = interaction.options.getString('choix');
      const choices = ['rock', 'paper', 'scissors'];
      const botChoice = choices[Math.floor(Math.random() * 3)];

      let result;
      let color;
      if (userChoice === botChoice) {
        result = '🤝 Égalité !';
        color = 0xFEE75C;
      } else if (
        (userChoice === 'rock' && botChoice === 'scissors') ||
        (userChoice === 'paper' && botChoice === 'rock') ||
        (userChoice === 'scissors' && botChoice === 'paper')
      ) {
        result = '🎉 Vous avez gagné !';
        color = 0x57F287;
      } else {
        result = '😔 Vous avez perdu !';
        color = 0xED4245;
      }

      const embed = new EmbedBuilder()
        .setTitle('Pierre-Papier-Ciseaux')
        .setDescription(
          `${RPS_EMOJIS[userChoice]} **${RPS_NAMES[userChoice]}** vs ${RPS_EMOJIS[botChoice]} **${RPS_NAMES[botChoice]}**\n\n${result}`
        )
        .setColor(color);

      return interaction.reply({ embeds: [embed] });
    }

    // === RATE ===
    if (sub === 'rate') {
      const sujet = interaction.options.getString('sujet');
      // Hash du sujet pour avoir un résultat constant
      let hash = 0;
      for (let i = 0; i < sujet.length; i++) {
        hash = ((hash << 5) - hash) + sujet.charCodeAt(i);
        hash = hash & hash;
      }
      const rating = Math.abs(hash) % 11; // 0-10
      const stars = '⭐'.repeat(Math.ceil(rating / 2)) + '☆'.repeat(5 - Math.ceil(rating / 2));

      return interaction.reply({ content: `Je donne à **${sujet}** un **${rating}/10** ! ${stars}` });
    }

    // === HUG ===
    if (sub === 'hug') {
      const target = interaction.options.getUser('membre');
      if (target.id === interaction.user.id) {
        return interaction.reply({ content: `🤗 **${interaction.user.username}** se fait un auto-câlin. C'est un peu triste...` });
      }
      return interaction.reply({ content: `🤗 **${interaction.user.username}** fait un gros câlin à **${target.username}** !` });
    }
  },
};