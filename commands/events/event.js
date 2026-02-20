// ===================================
// Ultra Suite — /event
// Gestion d'événements serveur avec RSVP
// /event create | list | cancel | info
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'events',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('event')
    .setDescription('Gérer les événements du serveur')
    .addSubcommand((sub) =>
      sub.setName('create').setDescription('Créer un événement')
        .addStringOption((opt) => opt.setName('titre').setDescription('Titre de l\'événement').setRequired(true))
        .addStringOption((opt) => opt.setName('description').setDescription('Description').setRequired(true))
        .addStringOption((opt) => opt.setName('date').setDescription('Date et heure (ex: 2025-12-25 20:00)').setRequired(true))
        .addChannelOption((opt) => opt.setName('channel').setDescription('Channel d\'annonce'))
        .addRoleOption((opt) => opt.setName('ping').setDescription('Rôle à mentionner'))
        .addIntegerOption((opt) => opt.setName('max_participants').setDescription('Nombre max de participants').setMinValue(1)))
    .addSubcommand((sub) =>
      sub.setName('list').setDescription('Voir les événements à venir'))
    .addSubcommand((sub) =>
      sub.setName('cancel').setDescription('Annuler un événement')
        .addIntegerOption((opt) => opt.setName('id').setDescription('ID de l\'événement').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('info').setDescription('Détails d\'un événement')
        .addIntegerOption((opt) => opt.setName('id').setDescription('ID de l\'événement').setRequired(true))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();

    // === CREATE ===
    if (sub === 'create') {
      if (!interaction.member.permissions.has(PermissionFlagsBits.ManageEvents)) {
        return interaction.reply({ content: '❌ Permission `Gérer les événements` requise.', ephemeral: true });
      }

      const titre = interaction.options.getString('titre');
      const description = interaction.options.getString('description');
      const dateStr = interaction.options.getString('date');
      const channel = interaction.options.getChannel('channel') || interaction.channel;
      const pingRole = interaction.options.getRole('ping');
      const maxParticipants = interaction.options.getInteger('max_participants') || null;

      // Parser la date
      const eventDate = new Date(dateStr.replace(' ', 'T'));
      if (isNaN(eventDate.getTime())) {
        return interaction.reply({ content: '❌ Date invalide. Format : `2025-12-25 20:00`', ephemeral: true });
      }
      if (eventDate < new Date()) {
        return interaction.reply({ content: '❌ La date doit être dans le futur.', ephemeral: true });
      }

      await interaction.deferReply({ ephemeral: true });

      const [id] = await db('server_events').insert({
        guild_id: guildId,
        title: titre,
        description,
        event_date: eventDate,
        created_by: interaction.user.id,
        channel_id: channel.id,
        max_participants: maxParticipants,
        status: 'ACTIVE',
        participants: JSON.stringify([]),
      });

      // Envoyer l'annonce
      const ts = Math.floor(eventDate.getTime() / 1000);
      const embed = new EmbedBuilder()
        .setTitle(`🎉 ${titre}`)
        .setDescription(description)
        .addFields(
          { name: '📅 Date', value: `<t:${ts}:F> (<t:${ts}:R>)`, inline: false },
          { name: '👥 Participants', value: `0${maxParticipants ? `/${maxParticipants}` : ''}`, inline: true },
          { name: '🆔', value: `#${id}`, inline: true },
        )
        .setColor(0x5865F2)
        .setFooter({ text: `Organisé par ${interaction.user.tag}`, iconURL: interaction.user.displayAvatarURL() })
        .setTimestamp();

      const row = new ActionRowBuilder().addComponents(
        new ButtonBuilder().setCustomId(`event-join-${id}`).setLabel('Participer').setStyle(ButtonStyle.Success).setEmoji('✅'),
        new ButtonBuilder().setCustomId(`event-leave-${id}`).setLabel('Se désinscrire').setStyle(ButtonStyle.Secondary).setEmoji('❌'),
      );

      const ping = pingRole ? `${pingRole}` : '';
      const msg = await channel.send({ content: ping || undefined, embeds: [embed], components: [row] });

      await db('server_events').where('id', id).update({ message_id: msg.id });
      await interaction.editReply({ content: `✅ Événement **#${id}** créé dans ${channel}.` });
    }

    // === LIST ===
    if (sub === 'list') {
      const events = await db('server_events')
        .where('guild_id', guildId)
        .where('status', 'ACTIVE')
        .where('event_date', '>=', new Date())
        .orderBy('event_date', 'asc')
        .limit(10);

      if (events.length === 0) {
        return interaction.reply({ content: '📅 Aucun événement à venir.', ephemeral: true });
      }

      const lines = events.map((e) => {
        const ts = Math.floor(new Date(e.event_date).getTime() / 1000);
        const participants = JSON.parse(e.participants || '[]').length;
        return `**#${e.id}** — ${e.title}\n📅 <t:${ts}:F> (<t:${ts}:R>) • 👥 ${participants}${e.max_participants ? `/${e.max_participants}` : ''}`;
      });

      const embed = new EmbedBuilder()
        .setTitle('📅 Événements à venir')
        .setDescription(lines.join('\n\n'))
        .setColor(0x5865F2).setTimestamp();

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    // === CANCEL ===
    if (sub === 'cancel') {
      if (!interaction.member.permissions.has(PermissionFlagsBits.ManageEvents)) {
        return interaction.reply({ content: '❌ Permission requise.', ephemeral: true });
      }

      const id = interaction.options.getInteger('id');
      const event = await db('server_events').where('id', id).where('guild_id', guildId).first();
      if (!event) return interaction.reply({ content: '❌ Événement introuvable.', ephemeral: true });

      await db('server_events').where('id', id).update({ status: 'CANCELLED' });
      return interaction.reply({ content: `✅ Événement **#${id}** annulé.`, ephemeral: true });
    }

    // === INFO ===
    if (sub === 'info') {
      const id = interaction.options.getInteger('id');
      const event = await db('server_events').where('id', id).where('guild_id', guildId).first();
      if (!event) return interaction.reply({ content: '❌ Introuvable.', ephemeral: true });

      const ts = Math.floor(new Date(event.event_date).getTime() / 1000);
      const participants = JSON.parse(event.participants || '[]');

      const embed = new EmbedBuilder()
        .setTitle(`🎉 ${event.title}`)
        .setDescription(event.description)
        .addFields(
          { name: '📅 Date', value: `<t:${ts}:F> (<t:${ts}:R>)`, inline: false },
          { name: '📊 Statut', value: event.status, inline: true },
          { name: '👥 Participants', value: `${participants.length}${event.max_participants ? `/${event.max_participants}` : ''}`, inline: true },
          { name: '🎯 Organisateur', value: `<@${event.created_by}>`, inline: true },
        )
        .setColor(event.status === 'ACTIVE' ? 0x5865F2 : 0x99AAB5).setTimestamp();

      if (participants.length > 0) {
        embed.addFields({
          name: 'Liste des participants',
          value: participants.slice(0, 20).map((p) => `<@${p}>`).join(', ') +
            (participants.length > 20 ? ` +${participants.length - 20}` : ''),
          inline: false,
        });
      }

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }
  },
};