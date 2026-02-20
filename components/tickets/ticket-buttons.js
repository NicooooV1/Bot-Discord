// ===================================
// Ultra Suite — Composants Tickets
// Gère les boutons : ticket-open, ticket-close, ticket-claim
// ===================================

const { EmbedBuilder, PermissionFlagsBits, ChannelType, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const configService = require('../../core/configService');
const { t } = require('../../core/i18n');
const { getDb } = require('../../database');

module.exports = {
  prefix: 'ticket-',
  type: 'button',
  module: 'tickets',

  async execute(interaction) {
    const customId = interaction.customId;
    const guildId = interaction.guildId;

    if (customId === 'ticket-open') return openTicketFromPanel(interaction, guildId);
    if (customId === 'ticket-close') return closeTicketButton(interaction, guildId);
    if (customId === 'ticket-claim') return claimTicketButton(interaction, guildId);
  },
};

async function openTicketFromPanel(interaction, guildId) {
  const config = await configService.get(guildId);
  const db = getDb();

  const maxTickets = config.maxTicketsPerUser || 3;
  const openTickets = await db('tickets')
    .where('guild_id', guildId).where('user_id', interaction.user.id)
    .where('status', 'OPEN').count('id as count').first();

  if ((openTickets?.count || 0) >= maxTickets) {
    const msg = await t(guildId, 'tickets.max_reached', { max: String(maxTickets) });
    return interaction.reply({ content: `❌ ${msg}`, ephemeral: true });
  }

  await interaction.deferReply({ ephemeral: true });

  const last = await db('tickets').where('guild_id', guildId).max('ticket_number as max').first();
  const ticketNumber = (last?.max || 0) + 1;
  const staffRoleId = config.ticketStaffRole || null;

  const permOverwrites = [
    { id: interaction.guild.id, deny: [PermissionFlagsBits.ViewChannel] },
    { id: interaction.user.id, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.AttachFiles] },
  ];
  if (staffRoleId) permOverwrites.push({ id: staffRoleId, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.ManageMessages] });
  if (interaction.guild.members.me) permOverwrites.push({ id: interaction.guild.members.me.id, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.ManageChannels] });

  const channel = await interaction.guild.channels.create({
    name: `ticket-${ticketNumber}`, type: ChannelType.GuildText,
    parent: config.ticketCategory || null,
    topic: `Ticket #${ticketNumber} — par ${interaction.user.tag}`,
    permissionOverwrites: permOverwrites,
  });

  await db('tickets').insert({ guild_id: guildId, ticket_number: ticketNumber, channel_id: channel.id, user_id: interaction.user.id, subject: 'Via panel', status: 'OPEN' });

  const row = new ActionRowBuilder().addComponents(
    new ButtonBuilder().setCustomId('ticket-close').setLabel('Fermer').setStyle(ButtonStyle.Danger).setEmoji('🔒'),
    new ButtonBuilder().setCustomId('ticket-claim').setLabel('Claim').setStyle(ButtonStyle.Secondary).setEmoji('🙋'),
  );

  const embed = new EmbedBuilder()
    .setTitle(`🎫 Ticket #${ticketNumber}`)
    .setDescription(`**Créé par :** ${interaction.user}\n\nDécrivez votre problème, un membre du staff vous répondra.`)
    .setColor(0x5865F2).setTimestamp();

  await channel.send({ content: `${interaction.user} ${staffRoleId ? `<@&${staffRoleId}>` : ''}`, embeds: [embed], components: [row] });

  const msg = await t(guildId, 'tickets.created', { id: String(ticketNumber) });
  await interaction.editReply({ content: `✅ ${msg}\n→ ${channel}` });
}

async function closeTicketButton(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).where('status', 'OPEN').first();
  if (!ticket) return interaction.reply({ content: '❌ Pas un ticket ouvert.', ephemeral: true });

  await interaction.deferReply();
  await db('tickets').where('id', ticket.id).update({ status: 'CLOSED', closed_by: interaction.user.id, closed_at: new Date() });

  const embed = new EmbedBuilder().setTitle(`🔒 Ticket #${ticket.ticket_number} fermé`).setDescription(`Fermé par ${interaction.user}`).setColor(0xED4245).setTimestamp();
  await interaction.editReply({ embeds: [embed] });

  const config = await configService.get(guildId);
  if (config.ticketLogChannel) {
    const logCh = interaction.guild.channels.cache.get(config.ticketLogChannel);
    if (logCh) {
      embed.addFields({ name: 'Créé par', value: `<@${ticket.user_id}>`, inline: true }, { name: 'Fermé par', value: interaction.user.toString(), inline: true });
      await logCh.send({ embeds: [embed] }).catch(() => {});
    }
  }

  setTimeout(async () => { try { await interaction.channel.delete('Ticket fermé'); } catch {} }, 5000);
}

async function claimTicketButton(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).where('status', 'OPEN').first();
  if (!ticket) return interaction.reply({ content: '❌ Pas un ticket ouvert.', ephemeral: true });
  if (ticket.claimed_by) return interaction.reply({ content: `ℹ️ Déjà claim par <@${ticket.claimed_by}>.`, ephemeral: true });

  await db('tickets').where('id', ticket.id).update({ claimed_by: interaction.user.id });
  return interaction.reply({ content: `🙋 ${interaction.user} prend en charge ce ticket.` });
}