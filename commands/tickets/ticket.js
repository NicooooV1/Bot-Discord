// ===================================
// Ultra Suite — /ticket
// Gestion des tickets de support
// /ticket create | close | add | remove | claim
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ChannelType, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const configService = require('../../core/configService');
const { t } = require('../../core/i18n');
const { getDb } = require('../../database');
const { createModuleLogger } = require('../../core/logger');

const log = createModuleLogger('Ticket');

module.exports = {
  module: 'tickets',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('ticket')
    .setDescription('Gestion des tickets de support')
    .addSubcommand((sub) =>
      sub.setName('create').setDescription('Créer un nouveau ticket')
        .addStringOption((opt) => opt.setName('sujet').setDescription('Sujet du ticket')))
    .addSubcommand((sub) =>
      sub.setName('close').setDescription('Fermer ce ticket')
        .addStringOption((opt) => opt.setName('raison').setDescription('Raison de la fermeture')))
    .addSubcommand((sub) =>
      sub.setName('add').setDescription('Ajouter un membre au ticket')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre à ajouter').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('remove').setDescription('Retirer un membre du ticket')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre à retirer').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('claim').setDescription('S\'attribuer ce ticket en tant que staff')),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;

    if (sub === 'create') return createTicket(interaction, guildId);
    if (sub === 'close') return closeTicket(interaction, guildId);
    if (sub === 'add') return addToTicket(interaction, guildId);
    if (sub === 'remove') return removeFromTicket(interaction, guildId);
    if (sub === 'claim') return claimTicket(interaction, guildId);
  },
};

async function createTicket(interaction, guildId) {
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
  const sujet = interaction.options.getString('sujet') || 'Support';
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
    topic: `Ticket #${ticketNumber} — ${sujet} — par ${interaction.user.tag}`,
    permissionOverwrites: permOverwrites,
  });

  await db('tickets').insert({ guild_id: guildId, ticket_number: ticketNumber, channel_id: channel.id, user_id: interaction.user.id, subject: sujet, status: 'OPEN' });

  const row = new ActionRowBuilder().addComponents(
    new ButtonBuilder().setCustomId('ticket-close').setLabel('Fermer le ticket').setStyle(ButtonStyle.Danger).setEmoji('🔒'),
    new ButtonBuilder().setCustomId('ticket-claim').setLabel('Claim').setStyle(ButtonStyle.Secondary).setEmoji('🙋'),
  );

  const embed = new EmbedBuilder()
    .setTitle(`🎫 Ticket #${ticketNumber}`)
    .setDescription(`**Sujet :** ${sujet}\n**Créé par :** ${interaction.user}\n\nUn membre du staff vous répondra bientôt.`)
    .setColor(0x5865F2).setTimestamp();

  await channel.send({ content: `${interaction.user} ${staffRoleId ? `<@&${staffRoleId}>` : ''}`, embeds: [embed], components: [row] });

  const msg = await t(guildId, 'tickets.created', { id: String(ticketNumber) });
  await interaction.editReply({ content: `✅ ${msg}\n→ ${channel}` });
}

async function closeTicket(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).where('status', 'OPEN').first();
  if (!ticket) return interaction.reply({ content: '❌ Ce channel n\'est pas un ticket ouvert.', ephemeral: true });

  await interaction.deferReply();
  const reason = interaction.options.getString('raison') || 'Aucune raison';

  await db('tickets').where('id', ticket.id).update({ status: 'CLOSED', closed_by: interaction.user.id, closed_at: new Date() });

  const closedMsg = await t(guildId, 'tickets.closed', { id: String(ticket.ticket_number), user: interaction.user.tag });
  const embed = new EmbedBuilder().setTitle(`🔒 Ticket #${ticket.ticket_number} fermé`).setDescription(`${closedMsg}\n**Raison :** ${reason}`).setColor(0xED4245).setTimestamp();
  await interaction.editReply({ embeds: [embed] });

  // Log
  const config = await configService.get(guildId);
  if (config.ticketLogChannel) {
    const logCh = interaction.guild.channels.cache.get(config.ticketLogChannel);
    if (logCh) {
      embed.addFields(
        { name: 'Créé par', value: `<@${ticket.user_id}>`, inline: true },
        { name: 'Fermé par', value: interaction.user.toString(), inline: true },
        { name: 'Sujet', value: ticket.subject || 'N/A', inline: true },
      );
      await logCh.send({ embeds: [embed] }).catch(() => {});
    }
  }

  setTimeout(async () => { try { await interaction.channel.delete('Ticket fermé'); } catch {} }, 5000);
}

async function addToTicket(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).where('status', 'OPEN').first();
  if (!ticket) return interaction.reply({ content: '❌ Pas un ticket ouvert.', ephemeral: true });

  const member = interaction.options.getUser('membre');
  await interaction.channel.permissionOverwrites.edit(member.id, { ViewChannel: true, SendMessages: true, AttachFiles: true });
  return interaction.reply({ content: `✅ ${member} ajouté au ticket.` });
}

async function removeFromTicket(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).where('status', 'OPEN').first();
  if (!ticket) return interaction.reply({ content: '❌ Pas un ticket ouvert.', ephemeral: true });

  const member = interaction.options.getUser('membre');
  if (member.id === ticket.user_id) return interaction.reply({ content: '❌ Impossible de retirer le créateur.', ephemeral: true });

  await interaction.channel.permissionOverwrites.delete(member.id);
  return interaction.reply({ content: `✅ ${member} retiré du ticket.` });
}

async function claimTicket(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).where('status', 'OPEN').first();
  if (!ticket) return interaction.reply({ content: '❌ Pas un ticket ouvert.', ephemeral: true });
  if (ticket.claimed_by) return interaction.reply({ content: `ℹ️ Déjà claim par <@${ticket.claimed_by}>.`, ephemeral: true });

  await db('tickets').where('id', ticket.id).update({ claimed_by: interaction.user.id });
  return interaction.reply({ content: `🙋 ${interaction.user} a pris en charge ce ticket.` });
}