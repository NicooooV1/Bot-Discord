// ===================================
// Ultra Suite — /ticket
// Gestion avancée des tickets de support
// /ticket create | close | add | remove | claim | transcript | rename | priority | transfer | blacklist | stats
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ChannelType, ActionRowBuilder, ButtonBuilder, ButtonStyle, StringSelectMenuBuilder, AttachmentBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { t } = require('../../core/i18n');
const { getDb } = require('../../database');
const { createModuleLogger } = require('../../core/logger');

const log = createModuleLogger('Ticket');
const PRIORITIES = { low: { emoji: '🟢', label: 'Basse', color: 0x57F287 }, medium: { emoji: '🟡', label: 'Moyenne', color: 0xFEE75C }, high: { emoji: '🟠', label: 'Haute', color: 0xE67E22 }, urgent: { emoji: '🔴', label: 'Urgente', color: 0xED4245 } };

module.exports = {
  module: 'tickets',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('ticket')
    .setDescription('Gestion avancée des tickets de support')
    .addSubcommand((sub) =>
      sub.setName('create').setDescription('Créer un nouveau ticket')
        .addStringOption((opt) => opt.setName('sujet').setDescription('Sujet du ticket'))
        .addStringOption((opt) => opt.setName('categorie').setDescription('Catégorie du ticket'))
        .addStringOption((opt) => opt.setName('priorite').setDescription('Priorité').addChoices(
          { name: '🟢 Basse', value: 'low' }, { name: '🟡 Moyenne', value: 'medium' },
          { name: '🟠 Haute', value: 'high' }, { name: '🔴 Urgente', value: 'urgent' })))
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
      sub.setName('claim').setDescription('S\'attribuer ce ticket en tant que staff'))
    .addSubcommand((sub) =>
      sub.setName('transcript').setDescription('Générer un transcript du ticket'))
    .addSubcommand((sub) =>
      sub.setName('rename').setDescription('Renommer ce ticket')
        .addStringOption((opt) => opt.setName('nom').setDescription('Nouveau nom').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('priority').setDescription('Définir la priorité du ticket')
        .addStringOption((opt) => opt.setName('niveau').setDescription('Niveau de priorité').setRequired(true)
          .addChoices({ name: '🟢 Basse', value: 'low' }, { name: '🟡 Moyenne', value: 'medium' }, { name: '🟠 Haute', value: 'high' }, { name: '🔴 Urgente', value: 'urgent' })))
    .addSubcommand((sub) =>
      sub.setName('transfer').setDescription('Transférer le ticket à un autre staff')
        .addUserOption((opt) => opt.setName('staff').setDescription('Staff destinataire').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('blacklist').setDescription('Blacklist un utilisateur des tickets')
        .addUserOption((opt) => opt.setName('utilisateur').setDescription('Utilisateur à blacklist').setRequired(true))
        .addStringOption((opt) => opt.setName('raison').setDescription('Raison du blacklist')))
    .addSubcommand((sub) =>
      sub.setName('unblacklist').setDescription('Retirer un utilisateur du blacklist')
        .addUserOption((opt) => opt.setName('utilisateur').setDescription('Utilisateur').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('stats').setDescription('Statistiques des tickets')),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;

    const handlers = {
      create: createTicket, close: closeTicket, add: addToTicket, remove: removeFromTicket,
      claim: claimTicket, transcript: transcriptTicket, rename: renameTicket,
      priority: setPriority, transfer: transferTicket, blacklist: blacklistUser,
      unblacklist: unblacklistUser, stats: ticketStats,
    };

    if (handlers[sub]) return handlers[sub](interaction, guildId);
  },
};

// ─── Transcript generator ────────────────────────────
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

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

// ─── Create ──────────────────────────────────────────
async function createTicket(interaction, guildId) {
  const config = await configService.get(guildId);
  const db = getDb();

  // Blacklist check
  const blacklisted = await db('ticket_blacklist').where('guild_id', guildId).where('user_id', interaction.user.id).first();
  if (blacklisted) return interaction.reply({ content: `❌ Vous êtes blacklist des tickets. Raison : ${blacklisted.reason || 'Non spécifiée'}`, ephemeral: true });

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
  const categorie = interaction.options.getString('categorie') || null;
  const priorite = interaction.options.getString('priorite') || 'medium';
  const staffRoleId = config.ticketStaffRole || null;
  const prioInfo = PRIORITIES[priorite] || PRIORITIES.medium;

  const permOverwrites = [
    { id: interaction.guild.id, deny: [PermissionFlagsBits.ViewChannel] },
    { id: interaction.user.id, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.AttachFiles, PermissionFlagsBits.EmbedLinks] },
  ];
  if (staffRoleId) permOverwrites.push({ id: staffRoleId, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.ManageMessages] });
  if (interaction.guild.members.me) permOverwrites.push({ id: interaction.guild.members.me.id, allow: [PermissionFlagsBits.ViewChannel, PermissionFlagsBits.SendMessages, PermissionFlagsBits.ManageChannels] });

  const channel = await interaction.guild.channels.create({
    name: `${prioInfo.emoji.replace(/[^a-z0-9-]/gi, '')}ticket-${ticketNumber}`,
    type: ChannelType.GuildText,
    parent: config.ticketCategory || null,
    topic: `${prioInfo.emoji} Ticket #${ticketNumber} — ${sujet} — Priorité: ${prioInfo.label} — par ${interaction.user.tag}`,
    permissionOverwrites: permOverwrites,
  });

  await db('tickets').insert({
    guild_id: guildId, ticket_number: ticketNumber, channel_id: channel.id,
    user_id: interaction.user.id, subject: sujet, status: 'OPEN',
    tags: categorie ? JSON.stringify([categorie]) : null,
  });

  const row = new ActionRowBuilder().addComponents(
    new ButtonBuilder().setCustomId('ticket-close').setLabel('Fermer').setStyle(ButtonStyle.Danger).setEmoji('🔒'),
    new ButtonBuilder().setCustomId('ticket-claim').setLabel('Claim').setStyle(ButtonStyle.Secondary).setEmoji('🙋'),
    new ButtonBuilder().setCustomId('ticket-transcript').setLabel('Transcript').setStyle(ButtonStyle.Secondary).setEmoji('📋'),
  );

  const embed = new EmbedBuilder()
    .setTitle(`🎫 Ticket #${ticketNumber}`)
    .setDescription(`**Sujet :** ${sujet}\n**Créé par :** ${interaction.user}\n**Priorité :** ${prioInfo.emoji} ${prioInfo.label}${categorie ? `\n**Catégorie :** ${categorie}` : ''}\n\nUn membre du staff vous répondra bientôt.`)
    .setColor(prioInfo.color).setTimestamp()
    .setFooter({ text: `ID: ${ticketNumber} • Priorité ${prioInfo.label}` });

  await channel.send({ content: `${interaction.user} ${staffRoleId ? `<@&${staffRoleId}>` : ''}`, embeds: [embed], components: [row] });

  const msg = await t(guildId, 'tickets.created', { id: String(ticketNumber) });
  await interaction.editReply({ content: `✅ ${msg}\n→ ${channel}` });
}

// ─── Close ───────────────────────────────────────────
async function closeTicket(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).where('status', 'OPEN').first();
  if (!ticket) return interaction.reply({ content: '❌ Ce channel n\'est pas un ticket ouvert.', ephemeral: true });

  await interaction.deferReply();
  const reason = interaction.options.getString('raison') || 'Aucune raison';

  // Auto-generate transcript before closing
  let transcriptAttachment = null;
  try {
    const { html, messageCount } = await generateTranscript(interaction.channel);
    const buffer = Buffer.from(html, 'utf-8');
    transcriptAttachment = new AttachmentBuilder(buffer, { name: `transcript-${ticket.ticket_number}.html` });
  } catch (e) {
    log.warn('Impossible de générer le transcript', e);
  }

  await db('tickets').where('id', ticket.id).update({ status: 'CLOSED', closed_by: interaction.user.id, closed_at: new Date(), close_reason: reason });

  const closedMsg = await t(guildId, 'tickets.closed', { id: String(ticket.ticket_number), user: interaction.user.tag });
  const embed = new EmbedBuilder()
    .setTitle(`🔒 Ticket #${ticket.ticket_number} fermé`)
    .setDescription(`${closedMsg}\n**Raison :** ${reason}`)
    .setColor(0xED4245).setTimestamp();

  await interaction.editReply({ embeds: [embed] });

  // Log + transcript
  const config = await configService.get(guildId);
  if (config.ticketLogChannel) {
    const logCh = interaction.guild.channels.cache.get(config.ticketLogChannel);
    if (logCh) {
      const logEmbed = new EmbedBuilder()
        .setTitle(`🔒 Ticket #${ticket.ticket_number} fermé`)
        .setColor(0xED4245).setTimestamp()
        .addFields(
          { name: 'Créé par', value: `<@${ticket.user_id}>`, inline: true },
          { name: 'Fermé par', value: interaction.user.toString(), inline: true },
          { name: 'Sujet', value: ticket.subject || 'N/A', inline: true },
          { name: 'Raison', value: reason, inline: false },
        );
      const payload = { embeds: [logEmbed] };
      if (transcriptAttachment) payload.files = [transcriptAttachment];
      await logCh.send(payload).catch(() => {});
    }
  }

  // DM transcript to creator
  try {
    const creator = await interaction.client.users.fetch(ticket.user_id);
    const dmEmbed = new EmbedBuilder()
      .setTitle(`🎫 Ticket #${ticket.ticket_number} — Fermé`)
      .setDescription(`Votre ticket sur **${interaction.guild.name}** a été fermé.\n**Raison :** ${reason}`)
      .setColor(0xED4245).setTimestamp();
    const dmPayload = { embeds: [dmEmbed] };
    if (transcriptAttachment) {
      const { html } = await generateTranscript(interaction.channel).catch(() => ({ html: null }));
      if (html) dmPayload.files = [new AttachmentBuilder(Buffer.from(html, 'utf-8'), { name: `transcript-${ticket.ticket_number}.html` })];
    }
    await creator.send(dmPayload).catch(() => {});
  } catch {}

  setTimeout(async () => { try { await interaction.channel.delete('Ticket fermé'); } catch {} }, 5000);
}

// ─── Transcript ──────────────────────────────────────
async function transcriptTicket(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).first();
  if (!ticket) return interaction.reply({ content: '❌ Ce channel n\'est pas un ticket.', ephemeral: true });

  await interaction.deferReply({ ephemeral: true });

  try {
    const { html, messageCount } = await generateTranscript(interaction.channel);
    const buffer = Buffer.from(html, 'utf-8');
    const attachment = new AttachmentBuilder(buffer, { name: `transcript-${ticket.ticket_number}.html` });

    await interaction.editReply({
      content: `📋 Transcript du ticket #${ticket.ticket_number} — ${messageCount} messages`,
      files: [attachment],
    });
  } catch (e) {
    log.error('Erreur transcript', e);
    await interaction.editReply({ content: '❌ Erreur lors de la génération du transcript.' });
  }
}

// ─── Rename ──────────────────────────────────────────
async function renameTicket(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).where('status', 'OPEN').first();
  if (!ticket) return interaction.reply({ content: '❌ Pas un ticket ouvert.', ephemeral: true });

  const newName = interaction.options.getString('nom');
  await interaction.channel.setName(newName);
  await interaction.reply({ content: `✅ Ticket renommé en **${newName}**.` });
}

// ─── Priority ────────────────────────────────────────
async function setPriority(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).where('status', 'OPEN').first();
  if (!ticket) return interaction.reply({ content: '❌ Pas un ticket ouvert.', ephemeral: true });

  const level = interaction.options.getString('niveau');
  const prioInfo = PRIORITIES[level];

  // Update topic
  await interaction.channel.setTopic(`${prioInfo.emoji} Ticket #${ticket.ticket_number} — ${ticket.subject || 'Support'} — Priorité: ${prioInfo.label}`);

  await interaction.reply({
    embeds: [new EmbedBuilder().setDescription(`${prioInfo.emoji} Priorité mise à jour : **${prioInfo.label}**`).setColor(prioInfo.color)],
  });
}

// ─── Transfer ────────────────────────────────────────
async function transferTicket(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).where('status', 'OPEN').first();
  if (!ticket) return interaction.reply({ content: '❌ Pas un ticket ouvert.', ephemeral: true });

  const staff = interaction.options.getUser('staff');
  await db('tickets').where('id', ticket.id).update({ claimed_by: staff.id });
  await interaction.channel.permissionOverwrites.edit(staff.id, { ViewChannel: true, SendMessages: true, ManageMessages: true });

  await interaction.reply({
    embeds: [new EmbedBuilder()
      .setDescription(`🔄 Ticket transféré à ${staff}\n(anciennement ${ticket.claimed_by ? `<@${ticket.claimed_by}>` : 'non claim'})`)
      .setColor(0x5865F2)],
  });
}

// ─── Add / Remove ────────────────────────────────────
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

// ─── Claim ───────────────────────────────────────────
async function claimTicket(interaction, guildId) {
  const db = getDb();
  const ticket = await db('tickets').where('guild_id', guildId).where('channel_id', interaction.channel.id).where('status', 'OPEN').first();
  if (!ticket) return interaction.reply({ content: '❌ Pas un ticket ouvert.', ephemeral: true });
  if (ticket.claimed_by) return interaction.reply({ content: `ℹ️ Déjà claim par <@${ticket.claimed_by}>.`, ephemeral: true });

  await db('tickets').where('id', ticket.id).update({ claimed_by: interaction.user.id });
  return interaction.reply({ content: `🙋 ${interaction.user} a pris en charge ce ticket.` });
}

// ─── Blacklist ───────────────────────────────────────
async function blacklistUser(interaction, guildId) {
  if (!interaction.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
    return interaction.reply({ content: '❌ Permission ManageGuild requise.', ephemeral: true });
  }
  const db = getDb();
  const user = interaction.options.getUser('utilisateur');
  const reason = interaction.options.getString('raison') || 'Non spécifiée';

  await db('ticket_blacklist').insert({ guild_id: guildId, user_id: user.id, reason, added_by: interaction.user.id }).onConflict(['guild_id', 'user_id']).merge();

  return interaction.reply({
    embeds: [new EmbedBuilder().setDescription(`🚫 ${user} a été blacklist des tickets.\n**Raison :** ${reason}`).setColor(0xED4245)],
  });
}

async function unblacklistUser(interaction, guildId) {
  if (!interaction.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
    return interaction.reply({ content: '❌ Permission ManageGuild requise.', ephemeral: true });
  }
  const db = getDb();
  const user = interaction.options.getUser('utilisateur');

  const deleted = await db('ticket_blacklist').where('guild_id', guildId).where('user_id', user.id).del();
  if (!deleted) return interaction.reply({ content: `ℹ️ ${user} n'est pas blacklist.`, ephemeral: true });

  return interaction.reply({
    embeds: [new EmbedBuilder().setDescription(`✅ ${user} retiré du blacklist des tickets.`).setColor(0x57F287)],
  });
}

// ─── Stats ───────────────────────────────────────────
async function ticketStats(interaction, guildId) {
  const db = getDb();
  const [total] = await db('tickets').where('guild_id', guildId).count('id as c');
  const [open] = await db('tickets').where('guild_id', guildId).where('status', 'OPEN').count('id as c');
  const [closed] = await db('tickets').where('guild_id', guildId).where('status', 'CLOSED').count('id as c');
  const [blacklisted] = await db('ticket_blacklist').where('guild_id', guildId).count('id as c');

  // Average resolution time
  const avgTime = await db('tickets').where('guild_id', guildId).where('status', 'CLOSED')
    .whereNotNull('closed_at').avg(db.raw('TIMESTAMPDIFF(MINUTE, created_at, closed_at) as avg_min')).first().catch(() => null);

  const avgMinutes = Math.round(avgTime?.avg_min || 0);
  const avgStr = avgMinutes > 60 ? `${Math.floor(avgMinutes / 60)}h ${avgMinutes % 60}min` : `${avgMinutes} min`;

  // Top staff
  const topStaff = await db('tickets').select('claimed_by').where('guild_id', guildId).whereNotNull('claimed_by')
    .groupBy('claimed_by').count('id as c').orderBy('c', 'desc').limit(5);

  const topStaffStr = topStaff.length > 0 ? topStaff.map((s, i) => `${i + 1}. <@${s.claimed_by}> — ${s.c} tickets`).join('\n') : 'Aucun';

  const embed = new EmbedBuilder()
    .setTitle('📊 Statistiques des tickets')
    .setColor(0x5865F2)
    .addFields(
      { name: 'Total', value: String(total?.c || 0), inline: true },
      { name: 'Ouverts', value: String(open?.c || 0), inline: true },
      { name: 'Fermés', value: String(closed?.c || 0), inline: true },
      { name: 'Temps moyen de résolution', value: avgStr, inline: true },
      { name: 'Utilisateurs blacklist', value: String(blacklisted?.c || 0), inline: true },
      { name: '\u200b', value: '\u200b', inline: true },
      { name: 'Top Staff', value: topStaffStr, inline: false },
    )
    .setTimestamp();

  return interaction.reply({ embeds: [embed] });
}