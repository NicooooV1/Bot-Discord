// ===================================
// Ultra Suite — /lock + /unlock
// Verrouiller / déverrouiller un channel
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('lock')
    .setDescription('Verrouiller ou déverrouiller un channel')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageChannels)
    .addSubcommand((sub) =>
      sub.setName('on').setDescription('Verrouiller ce channel')
        .addStringOption((opt) => opt.setName('raison').setDescription('Raison du verrouillage'))
        .addChannelOption((opt) => opt.setName('channel').setDescription('Channel cible')))
    .addSubcommand((sub) =>
      sub.setName('off').setDescription('Déverrouiller ce channel')
        .addChannelOption((opt) => opt.setName('channel').setDescription('Channel cible'))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const channel = interaction.options.getChannel('channel') || interaction.channel;
    const everyoneRole = interaction.guild.roles.everyone;

    try {
      if (sub === 'on') {
        const reason = interaction.options.getString('raison') || 'Aucune raison';

        await channel.permissionOverwrites.edit(everyoneRole, {
          SendMessages: false,
          AddReactions: false,
          CreatePublicThreads: false,
        });

        const embed = new EmbedBuilder()
          .setDescription(`🔒 Ce channel a été verrouillé par ${interaction.user}.\n**Raison :** ${reason}`)
          .setColor(0xED4245)
          .setTimestamp();

        await channel.send({ embeds: [embed] });
        return interaction.reply({ content: `🔒 ${channel} verrouillé.`, ephemeral: true });
      }

      if (sub === 'off') {
        await channel.permissionOverwrites.edit(everyoneRole, {
          SendMessages: null,
          AddReactions: null,
          CreatePublicThreads: null,
        });

        const embed = new EmbedBuilder()
          .setDescription(`🔓 Ce channel a été déverrouillé par ${interaction.user}.`)
          .setColor(0x57F287)
          .setTimestamp();

        await channel.send({ embeds: [embed] });
        return interaction.reply({ content: `🔓 ${channel} déverrouillé.`, ephemeral: true });
      }
    } catch {
      return interaction.reply({ content: '❌ Impossible de modifier les permissions de ce channel.', ephemeral: true });
    }
  },
};