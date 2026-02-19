// ===================================
// Ultra Suite — Moderation: /lock + /unlock
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const { successEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'moderation',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('lock')
    .setDescription('Verrouille ou déverrouille un salon')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageChannels)
    .addSubcommand((sub) =>
      sub
        .setName('on')
        .setDescription('Verrouille le salon')
        .addChannelOption((opt) => opt.setName('salon').setDescription('Salon cible'))
    )
    .addSubcommand((sub) =>
      sub
        .setName('off')
        .setDescription('Déverrouille le salon')
        .addChannelOption((opt) => opt.setName('salon').setDescription('Salon cible'))
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const channel = interaction.options.getChannel('salon') || interaction.channel;
    const lock = sub === 'on';

    await channel.permissionOverwrites.edit(interaction.guild.roles.everyone, {
      SendMessages: lock ? false : null,
    });

    const key = lock ? 'mod.lock.locked' : 'mod.lock.unlocked';
    await interaction.reply({
      embeds: [successEmbed(t(key, undefined, { channel: channel.toString() }))],
    });
  },
};
