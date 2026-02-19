// ===================================
// Ultra Suite — Fun: /8ball
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const { createEmbed } = require('../../utils/embeds');

const ANSWERS = [
  '🟢 Oui, absolument.',
  '🟢 C\'est certain.',
  '🟢 Sans aucun doute.',
  '🟢 Oui.',
  '🟢 Tu peux compter dessus.',
  '🟡 Très probable.',
  '🟡 Les signes disent oui.',
  '🟡 Probablement.',
  '🟡 Bonne chance.',
  '🟠 Demande plus tard.',
  '🟠 Je ne peux pas répondre maintenant.',
  '🟠 Concentre-toi et redemande.',
  '🟠 Ne compte pas dessus.',
  '🔴 Ma réponse est non.',
  '🔴 Mes sources disent non.',
  '🔴 Les perspectives ne sont pas bonnes.',
  '🔴 Très douteux.',
  '🔴 Non.',
];

module.exports = {
  module: 'fun',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('8ball')
    .setDescription('Pose une question à la boule magique 🎱')
    .addStringOption((opt) => opt.setName('question').setDescription('Ta question').setRequired(true)),

  async execute(interaction) {
    const question = interaction.options.getString('question');
    const answer = ANSWERS[Math.floor(Math.random() * ANSWERS.length)];

    const embed = createEmbed('primary')
      .setTitle('🎱 Boule Magique')
      .addFields(
        { name: '❓ Question', value: question },
        { name: '🔮 Réponse', value: answer }
      )
      .setFooter({ text: interaction.user.tag });

    return interaction.reply({ embeds: [embed] });
  },
};
