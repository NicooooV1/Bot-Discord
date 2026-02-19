// ===================================
// Ultra Suite — Button: ticket_open
// Ouvre un ticket via le panel
// ===================================

const { ChannelType, PermissionFlagsBits, ButtonBuilder, ButtonStyle, ActionRowBuilder } = require('discord.js');
const configService = require('../../core/configService');
const ticketQueries = require('../../database/ticketQueries');
const { createEmbed, errorEmbed, successEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  id: 'ticket_open',

  async execute(interaction) {
    const config = await configService.get(interaction.guild.id);

    if (!config.ticketCategory) {
      return interaction.reply({ embeds: [errorEmbed('❌ Tickets non configurés.')], ephemeral: true });
    }

    const count = await ticketQueries.countByUser(interaction.guild.id, interaction.user.id);
    if (count >= (config.maxTicketsPerUser || 3)) {
      return interaction.reply({
        embeds: [errorEmbed(t('tickets.max_tickets', undefined, { max: config.maxTicketsPerUser || 3 }))],
        ephemeral: true,
      });
    }

    const permissionOverwrites = [
      { id: interaction.guild.roles.everyone.id, deny: [PermissionFlagsBits.ViewChannel] },
      { id: interaction.user.id, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.ReadMessageHistory] },
      { id: interaction.client.user.id, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.ManageChannels] },
    ];

    if (config.ticketStaffRole) {
      permissionOverwrites.push({
        id: config.ticketStaffRole,
        allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.ReadMessageHistory],
      });
    }

    const channel = await interaction.guild.channels.create({
      name: `ticket-${interaction.user.username}`,
      type: ChannelType.GuildText,
      parent: config.ticketCategory,
      permissionOverwrites,
    });

    const ticketId = await ticketQueries.create({
      guildId: interaction.guild.id,
      channelId: channel.id,
      openerId: interaction.user.id,
    });

    const embed = createEmbed('primary')
      .setTitle(`🎫 Ticket #${ticketId}`)
      .setDescription(`Bienvenue ${interaction.user} !\n\nDécrivez votre problème et un membre du staff vous répondra.`)
      .setFooter({ text: 'Utilisez /ticket close pour fermer ce ticket.' });

    const closeBtn = new ButtonBuilder()
      .setCustomId('ticket_close')
      .setLabel('🔒 Fermer')
      .setStyle(ButtonStyle.Danger);

    await channel.send({ embeds: [embed], components: [new ActionRowBuilder().addComponents(closeBtn)] });
    await interaction.reply({ embeds: [successEmbed(`✅ Ticket créé : ${channel}`)], ephemeral: true });
  },
};
