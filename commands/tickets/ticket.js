// ===================================
// Ultra Suite — Tickets: /ticket
// Système de tickets complet
// ===================================

const {
  SlashCommandBuilder,
  PermissionFlagsBits,
  ChannelType,
  ButtonBuilder,
  ButtonStyle,
  ActionRowBuilder,
} = require('discord.js');
const configService = require('../../core/configService');
const ticketQueries = require('../../database/ticketQueries');
const { createEmbed, successEmbed, errorEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'tickets',
  data: new SlashCommandBuilder()
    .setName('ticket')
    .setDescription('Système de tickets')
    .addSubcommand((sub) =>
      sub
        .setName('panel')
        .setDescription('Envoie le panel de tickets dans ce salon')
        .addStringOption((opt) => opt.setName('titre').setDescription('Titre du panel'))
        .addStringOption((opt) => opt.setName('description').setDescription('Description du panel'))
    )
    .addSubcommand((sub) =>
      sub
        .setName('open')
        .setDescription('Ouvre un ticket manuellement')
        .addStringOption((opt) => opt.setName('sujet').setDescription('Sujet du ticket'))
    )
    .addSubcommand((sub) =>
      sub.setName('close').setDescription('Ferme le ticket actuel')
    )
    .addSubcommand((sub) =>
      sub
        .setName('add')
        .setDescription('Ajoute un membre au ticket')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre à ajouter').setRequired(true))
    )
    .addSubcommand((sub) =>
      sub
        .setName('remove')
        .setDescription('Retire un membre du ticket')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre à retirer').setRequired(true))
    )
    .addSubcommand((sub) =>
      sub
        .setName('assign')
        .setDescription('Assigner un staff au ticket')
        .addUserOption((opt) => opt.setName('staff').setDescription('Staff à assigner').setRequired(true))
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const config = await configService.get(interaction.guild.id);

    switch (sub) {
      // === PANEL ===
      case 'panel': {
        if (!interaction.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
          return interaction.reply({ embeds: [errorEmbed(t('common.no_permission'))], ephemeral: true });
        }

        const title = interaction.options.getString('titre') || t('tickets.panel_title');
        const description = interaction.options.getString('description') || t('tickets.panel_description');

        const embed = createEmbed('primary').setTitle(title).setDescription(description);

        const button = new ButtonBuilder()
          .setCustomId('ticket_open')
          .setLabel(t('tickets.panel_button'))
          .setStyle(ButtonStyle.Primary)
          .setEmoji('📩');

        const row = new ActionRowBuilder().addComponents(button);
        await interaction.channel.send({ embeds: [embed], components: [row] });
        return interaction.reply({ embeds: [successEmbed('✅ Panel envoyé.')], ephemeral: true });
      }

      // === OPEN ===
      case 'open': {
        await openTicket(interaction, config);
        break;
      }

      // === CLOSE ===
      case 'close': {
        const ticket = await ticketQueries.getByChannel(interaction.channel.id);
        if (!ticket) {
          return interaction.reply({ embeds: [errorEmbed(t('tickets.not_ticket'))], ephemeral: true });
        }

        await ticketQueries.updateStatus(ticket.id, 'closed', interaction.user.id);

        // Générer transcript simple
        const messages = await interaction.channel.messages.fetch({ limit: 100 });
        const transcript = messages
          .reverse()
          .map((m) => `[${m.createdAt.toISOString()}] ${m.author.tag}: ${m.content}`)
          .join('\n');
        await ticketQueries.setTranscript(ticket.id, transcript);

        await interaction.reply({ embeds: [successEmbed(t('tickets.close', undefined, { user: interaction.user.tag }))] });

        // Log
        if (config.ticketLogChannel) {
          const logChannel = interaction.guild.channels.cache.get(config.ticketLogChannel);
          if (logChannel) {
            const logEmbed = createEmbed('logs')
              .setTitle(`🎫 Ticket fermé — #${ticket.id}`)
              .addFields(
                { name: 'Ouvert par', value: `<@${ticket.opener_id}>`, inline: true },
                { name: 'Fermé par', value: interaction.user.tag, inline: true },
                { name: 'Sujet', value: ticket.subject || 'N/A', inline: true }
              );
            logChannel.send({ embeds: [logEmbed] }).catch(() => {});
          }
        }

        // Supprimer le salon après 5 secondes
        setTimeout(() => interaction.channel.delete('Ticket fermé').catch(() => {}), 5000);
        break;
      }

      // === ADD ===
      case 'add': {
        const ticket = await ticketQueries.getByChannel(interaction.channel.id);
        if (!ticket) {
          return interaction.reply({ embeds: [errorEmbed(t('tickets.not_ticket'))], ephemeral: true });
        }
        const member = interaction.options.getMember('membre');
        await interaction.channel.permissionOverwrites.edit(member, {
          ViewChannel: true,
          SendMessages: true,
          ReadMessageHistory: true,
        });
        return interaction.reply({ embeds: [successEmbed(`✅ ${member} ajouté au ticket.`)] });
      }

      // === REMOVE ===
      case 'remove': {
        const ticket2 = await ticketQueries.getByChannel(interaction.channel.id);
        if (!ticket2) {
          return interaction.reply({ embeds: [errorEmbed(t('tickets.not_ticket'))], ephemeral: true });
        }
        const member2 = interaction.options.getMember('membre');
        await interaction.channel.permissionOverwrites.delete(member2);
        return interaction.reply({ embeds: [successEmbed(`✅ ${member2} retiré du ticket.`)] });
      }

      // === ASSIGN ===
      case 'assign': {
        const ticket3 = await ticketQueries.getByChannel(interaction.channel.id);
        if (!ticket3) {
          return interaction.reply({ embeds: [errorEmbed(t('tickets.not_ticket'))], ephemeral: true });
        }
        const staff = interaction.options.getMember('staff');
        await ticketQueries.setAssignee(ticket3.id, staff.id);
        await interaction.channel.permissionOverwrites.edit(staff, {
          ViewChannel: true,
          SendMessages: true,
          ReadMessageHistory: true,
        });
        return interaction.reply({ embeds: [successEmbed(`✅ ${staff} assigné au ticket.`)] });
      }
    }
  },
};

/**
 * Ouvre un ticket
 */
async function openTicket(interaction, config) {
  if (!config.ticketCategory) {
    return interaction.reply({ embeds: [errorEmbed('❌ Le système de tickets n\'est pas configuré. Utilisez `/setup tickets`.')], ephemeral: true });
  }

  // Vérifier le max par user
  const count = await ticketQueries.countByUser(interaction.guild.id, interaction.user.id);
  if (count >= (config.maxTicketsPerUser || 3)) {
    return interaction.reply({
      embeds: [errorEmbed(t('tickets.max_tickets', undefined, { max: config.maxTicketsPerUser || 3 }))],
      ephemeral: true,
    });
  }

  const subject = interaction.options?.getString('sujet') || null;

  // Créer le salon
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

  // DB
  const ticketId = await ticketQueries.create({
    guildId: interaction.guild.id,
    channelId: channel.id,
    openerId: interaction.user.id,
    subject,
  });

  // Message d'accueil dans le ticket
  const embed = createEmbed('primary')
    .setTitle(`🎫 Ticket #${ticketId}`)
    .setDescription(`Bienvenue ${interaction.user} !\n\nDécrivez votre problème et un membre du staff vous répondra.\n${subject ? `**Sujet :** ${subject}` : ''}`)
    .setFooter({ text: 'Utilisez /ticket close pour fermer ce ticket.' });

  const closeBtn = new ButtonBuilder()
    .setCustomId('ticket_close')
    .setLabel('🔒 Fermer le ticket')
    .setStyle(ButtonStyle.Danger);

  const row = new ActionRowBuilder().addComponents(closeBtn);
  await channel.send({ embeds: [embed], components: [row] });

  await interaction.reply({
    embeds: [successEmbed(`✅ Ticket créé : ${channel}`)],
    ephemeral: true,
  });
}
