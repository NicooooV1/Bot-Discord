// ===================================
// Ultra Suite — Composants Tickets
// Gère les boutons : ticket-open, ticket-close, ticket-claim, ticket-transcript
// ===================================

const { EmbedBuilder, PermissionFlagsBits, ChannelType, ActionRowBuilder, ButtonBuilder, ButtonStyle, AttachmentBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { t } = require('../../core/i18n');
const { getDb } = require('../../database');
const { createModuleLogger } = require('../../core/logger');

const log = createModuleLogger('TicketBtn');

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
    if (customId === 'ticket-transcript') return transcriptButton(interaction, guildId);
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

// ─── Transcript HTML generator ───────────────────────
function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

async function generateTranscript(channel) {
  const messages = [];
  let lastId;
  while (true) {
    const opts = { limit: 100 };
    if (lastId) opts.before = lastId;
    const fetched = await channel.messages.fetch(opts);
    if (fetched.size === 0) break;
    fetched.forEach((m) => messages.push(m));
    lastId = fetched.last().id;
    if (fetched.size < 100) break;
  }
  messages.sort((a, b) => a.createdTimestamp - b.createdTimestamp);

  let html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Transcript — ${channel.name}</title>
<style>body{font-family:'Segoe UI',sans-serif;background:#36393f;color:#dcddde;max-width:900px;margin:0 auto;padding:20px}
.msg{display:flex;gap:12px;padding:4px 0;border-bottom:1px solid #40444b}.avatar{width:40px;height:40px;border-radius:50%}
.author{font-weight:600;color:#fff}.time{font-size:0.75rem;color:#72767d;margin-left:8px}.content{margin-top:2px}
.embed{border-left:4px solid #5865f2;background:#2f3136;padding:8px;margin:4px 0;border-radius:4px}
.attachment{color:#00aff4;text-decoration:underline}h1{color:#fff;border-bottom:2px solid #5865f2;padding-bottom:8px}
.info{color:#72767d;font-size:0.85rem;margin-bottom:16px}</style></head><body>`;

  html += `<h1>📋 Transcript — #${channel.name}</h1>`;
  html += `<div class="info">Généré le ${new Date().toLocaleString('fr-FR')} — ${messages.length} messages</div>`;

  for (const msg of messages) {
    const avatar = msg.author.displayAvatarURL({ size: 64, extension: 'png' });
    const time = msg.createdAt.toLocaleString('fr-FR');
    const isBot = msg.author.bot ? ' <span style="background:#5865f2;color:#fff;font-size:0.65rem;padding:1px 4px;border-radius:3px">BOT</span>' : '';
    html += `<div class="msg"><img class="avatar" src="${avatar}" alt=""><div>`;
    html += `<span class="author">${escapeHtml(msg.author.displayName || msg.author.username)}${isBot}</span><span class="time">${time}</span>`;
    if (msg.content) html += `<div class="content">${escapeHtml(msg.content)}</div>`;
    for (const embed of msg.embeds) {
      html += `<div class="embed">`;
      if (embed.title) html += `<strong>${escapeHtml(embed.title)}</strong><br>`;
      if (embed.description) html += `${escapeHtml(embed.description)}`;
      html += `</div>`;
    }
    for (const att of msg.attachments.values()) {
      html += `<div><a class="attachment" href="${att.url}">${escapeHtml(att.name)}</a> (${formatBytes(att.size)})</div>`;
    }
    html += `</div></div>`;
  }
  html += `</body></html>`;
  return { html, messageCount: messages.length };
}

async function transcriptButton(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).first();
  if (!ticket) return interaction.reply({ content: '❌ Pas un ticket.', ephemeral: true });

  await interaction.deferReply({ ephemeral: true });
  try {
    const { html, messageCount } = await generateTranscript(interaction.channel);
    const buffer = Buffer.from(html, 'utf-8');
    const attachment = new AttachmentBuilder(buffer, { name: `transcript-${ticket.ticket_number}.html` });
    await interaction.editReply({ content: `📋 Transcript — ${messageCount} messages`, files: [attachment] });
  } catch (e) {
    log.error('Erreur transcript', e);
    await interaction.editReply({ content: '❌ Erreur lors de la génération du transcript.' });
  }
}