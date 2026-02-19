// ===================================
// Ultra Suite — Events: /event
// Événements serveur avec inscription
// ===================================

const {
  SlashCommandBuilder,
  PermissionFlagsBits,
  ButtonBuilder,
  ButtonStyle,
  ActionRowBuilder,
} = require('discord.js');
const { getDb } = require('../../database');
const { successEmbed, errorEmbed, createEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'events',
  cooldown: 5,
  data: new SlashCommandBuilder()
    .setName('event')
    .setDescription('Gère les événements')
    .addSubcommand((sub) =>
      sub
        .setName('create')
        .setDescription('Crée un événement')
        .addStringOption((opt) => opt.setName('titre').setDescription('Titre').setRequired(true))
        .addStringOption((opt) => opt.setName('description').setDescription('Description').setRequired(true))
        .addStringOption((opt) => opt.setName('date').setDescription('Date (ex: 2025-01-20 20:00)').setRequired(true))
        .addIntegerOption((opt) => opt.setName('max').setDescription('Places max (0 = illimité)'))
    )
    .addSubcommand((sub) =>
      sub
        .setName('info')
        .setDescription('Voir un événement')
        .addIntegerOption((opt) => opt.setName('id').setDescription('ID').setRequired(true))
    )
    .addSubcommand((sub) => sub.setName('list').setDescription('Liste les événements'))
    .addSubcommand((sub) =>
      sub
        .setName('cancel')
        .setDescription('Annule un événement')
        .addIntegerOption((opt) => opt.setName('id').setDescription('ID').setRequired(true))
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();

    switch (sub) {
      case 'create': {
        if (!interaction.member.permissions.has(PermissionFlagsBits.ManageEvents)) {
          return interaction.reply({ embeds: [errorEmbed('❌ Permission manquante.')], ephemeral: true });
        }

        const title = interaction.options.getString('titre');
        const description = interaction.options.getString('description');
        const dateStr = interaction.options.getString('date');
        const max = interaction.options.getInteger('max') || 0;

        const date = new Date(dateStr);
        if (isNaN(date.getTime())) {
          return interaction.reply({ embeds: [errorEmbed('❌ Date invalide. Format: YYYY-MM-DD HH:MM')], ephemeral: true });
        }

        const [id] = await db('events').insert({
          guild_id: interaction.guild.id,
          title,
          description,
          creator_id: interaction.user.id,
          scheduled_at: date.toISOString(),
          max_participants: max || 0,
          participants: JSON.stringify([]),
        });

        const embed = createEmbed('primary')
          .setTitle(`🎉 ${title}`)
          .setDescription(description)
          .addFields(
            { name: '📅 Date', value: `<t:${Math.floor(date.getTime() / 1000)}:F>`, inline: true },
            { name: '👥 Places', value: max > 0 ? `0/${max}` : 'Illimité', inline: true },
            { name: '🎯 Organisateur', value: `${interaction.user}`, inline: true }
          );

        const row = new ActionRowBuilder().addComponents(
          new ButtonBuilder()
            .setCustomId(`event_join_${id}`)
            .setLabel('Participer')
            .setStyle(ButtonStyle.Success)
            .setEmoji('✅'),
          new ButtonBuilder()
            .setCustomId(`event_leave_${id}`)
            .setLabel('Se désinscrire')
            .setStyle(ButtonStyle.Danger)
            .setEmoji('❌')
        );

        const msg = await interaction.channel.send({ embeds: [embed], components: [row] });
        await db('events').where('id', id).update({ channel_id: interaction.channel.id });

        return interaction.reply({ embeds: [successEmbed(`✅ Événement #${id} créé !`)], ephemeral: true });
      }

      case 'info': {
        const id = interaction.options.getInteger('id');
        const event = await db('events').where({ id, guild_id: interaction.guild.id }).first();

        if (!event) return interaction.reply({ embeds: [errorEmbed('❌ Événement introuvable.')], ephemeral: true });

        const participants = JSON.parse(event.participants || '[]');

        const embed = createEmbed('primary')
          .setTitle(`🎉 ${event.title}`)
          .setDescription(event.description)
          .addFields(
            { name: '📅 Date', value: `<t:${Math.floor(new Date(event.scheduled_at).getTime() / 1000)}:F>`, inline: true },
            { name: '👥 Participants', value: `${participants.length}`, inline: true },
          );

        if (participants.length > 0) {
          embed.addFields({
            name: 'Liste des participants',
            value: participants.slice(0, 20).map((p) => `<@${p}>`).join(', '),
          });
        }

        return interaction.reply({ embeds: [embed] });
      }

      case 'list': {
        const events = await db('events')
          .where({ guild_id: interaction.guild.id })
          .orderBy('scheduled_at');

        if (events.length === 0) return interaction.reply({ content: '📭 Aucun événement.', ephemeral: true });

        const list = events.map((e) => {
          const p = JSON.parse(e.participants || '[]').length;
          return `**#${e.id}** — ${e.title} · <t:${Math.floor(new Date(e.scheduled_at).getTime() / 1000)}:R> · ${p} participants`;
        });

        const embed = createEmbed('primary').setTitle('🎉 Événements').setDescription(list.join('\n'));

        return interaction.reply({ embeds: [embed] });
      }

      case 'cancel': {
        if (!interaction.member.permissions.has(PermissionFlagsBits.ManageEvents)) {
          return interaction.reply({ embeds: [errorEmbed('❌ Permission manquante.')], ephemeral: true });
        }

        const id = interaction.options.getInteger('id');
        const deleted = await db('events')
          .where({ id, guild_id: interaction.guild.id })
          .delete();

        if (!deleted) return interaction.reply({ embeds: [errorEmbed('❌ Événement introuvable.')], ephemeral: true });

        return interaction.reply({ embeds: [successEmbed(`✅ Événement #${id} annulé.`)] });
      }
    }
  },
};
