// ===================================
// Ultra Suite — Music: /music
// Préparé pour intégration future
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const { infoEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'music',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('music')
    .setDescription('🎵 Module musique (bientôt disponible)')
    .addSubcommand((sub) =>
      sub.setName('play').setDescription('Joue une musique').addStringOption((opt) => opt.setName('query').setDescription('URL ou recherche').setRequired(true))
    )
    .addSubcommand((sub) => sub.setName('stop').setDescription('Arrête la musique'))
    .addSubcommand((sub) => sub.setName('skip').setDescription('Passe au suivant'))
    .addSubcommand((sub) => sub.setName('queue').setDescription('File d\'attente'))
    .addSubcommand((sub) => sub.setName('pause').setDescription('Met en pause'))
    .addSubcommand((sub) => sub.setName('volume').setDescription('Volume').addIntegerOption((opt) => opt.setName('level').setDescription('Niveau (0-100)').setMinValue(0).setMaxValue(100).setRequired(true))),

  async execute(interaction) {
    return interaction.reply({
      embeds: [
        infoEmbed(
          '🎵 **Module Musique — Bientôt disponible**\n\n' +
            'Ce module est préparé pour une intégration future avec `@discordjs/voice` et `play-dl`.\n\n' +
            'Commandes prévues : play, stop, skip, queue, pause, volume, loop, nowplaying, lyrics.'
        ),
      ],
      ephemeral: true,
    });
  },
};
