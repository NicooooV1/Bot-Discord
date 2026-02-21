// ===================================
// Ultra Suite — /say
// Faire parler le bot
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');

module.exports = {
  module: 'fun',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('say')
    .setDescription('Faire dire quelque chose au bot')
    .addStringOption((o) => o.setName('message').setDescription('Le message').setRequired(true))
    .addChannelOption((o) => o.setName('salon').setDescription('Le salon cible'))
    .addBooleanOption((o) => o.setName('embed').setDescription('Envoyer en embed'))
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageMessages),

  async execute(interaction) {
    const message = interaction.options.getString('message');
    const channel = interaction.options.getChannel('salon') || interaction.channel;
    const asEmbed = interaction.options.getBoolean('embed') || false;

    if (asEmbed) {
      const { EmbedBuilder } = require('discord.js');
      const embed = new EmbedBuilder().setColor(0x3498DB).setDescription(message);
      await channel.send({ embeds: [embed] });
    } else {
      await channel.send({ content: message });
    }

    return interaction.reply({ content: `✅ Message envoyé dans ${channel} !`, ephemeral: true });
  },
};
