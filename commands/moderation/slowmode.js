// ===================================
// Ultra Suite — Moderation: /slowmode
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const { successEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'moderation',
  data: new SlashCommandBuilder()
    .setName('slowmode')
    .setDescription('Définit le slowmode d\'un salon')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageChannels)
    .addIntegerOption((opt) =>
      opt
        .setName('secondes')
        .setDescription('Intervalle en secondes (0 = désactiver)')
        .setMinValue(0)
        .setMaxValue(21600)
        .setRequired(true)
    )
    .addChannelOption((opt) => opt.setName('salon').setDescription('Salon cible')),

  async execute(interaction) {
    const seconds = interaction.options.getInteger('secondes');
    const channel = interaction.options.getChannel('salon') || interaction.channel;

    await channel.setRateLimitPerUser(seconds, `Par ${interaction.user.tag}`);

    const key = seconds === 0 ? 'mod.slowmode.removed' : 'mod.slowmode.success';
    await interaction.reply({
      embeds: [successEmbed(t(key, undefined, { seconds, channel: channel.toString() }))],
      ephemeral: true,
    });
  },
};
