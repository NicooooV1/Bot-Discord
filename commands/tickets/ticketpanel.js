// ===================================
// Ultra Suite — /ticketpanel
// Envoyer un panel de création de ticket (embed + bouton)
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const configService = require('../../core/configService');

module.exports = {
  module: 'tickets',
  adminOnly: true,
  cooldown: 10,

  data: new SlashCommandBuilder()
    .setName('ticketpanel')
    .setDescription('Envoyer un panel de création de ticket dans ce channel')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
    .addStringOption((opt) => opt.setName('titre').setDescription('Titre du panel').setRequired(false))
    .addStringOption((opt) => opt.setName('description').setDescription('Description du panel').setRequired(false))
    .addStringOption((opt) => opt.setName('couleur').setDescription('Couleur hex (ex: #5865F2)').setRequired(false)),

  async execute(interaction) {
    const titre = interaction.options.getString('titre') || '🎫 Support — Créer un ticket';
    const description = interaction.options.getString('description') ||
      'Cliquez sur le bouton ci-dessous pour ouvrir un ticket de support.\n\n' +
      'Un membre du staff vous répondra dans les plus brefs délais.\n' +
      '⚠️ Merci de ne pas ouvrir de tickets inutiles.';

    let color = 0x5865F2;
    const colorStr = interaction.options.getString('couleur');
    if (colorStr) {
      const parsed = parseInt(colorStr.replace('#', ''), 16);
      if (!isNaN(parsed)) color = parsed;
    }

    const embed = new EmbedBuilder()
      .setTitle(titre)
      .setDescription(description)
      .setColor(color)
      .setFooter({ text: interaction.guild.name, iconURL: interaction.guild.iconURL() })
      .setTimestamp();

    const row = new ActionRowBuilder().addComponents(
      new ButtonBuilder()
        .setCustomId('ticket-open')
        .setLabel('Créer un ticket')
        .setStyle(ButtonStyle.Primary)
        .setEmoji('🎫'),
    );

    await interaction.channel.send({ embeds: [embed], components: [row] });
    return interaction.reply({ content: '✅ Panel de tickets envoyé.', ephemeral: true });
  },
};