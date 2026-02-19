// ===================================
// Ultra Suite — Moderation: /modlogs
// Historique de sanctions d'un membre
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const sanctionQueries = require('../../database/sanctionQueries');
const { createEmbed, paginateEmbeds, errorEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');
const { relativeTime, formatDuration } = require('../../utils/formatters');

module.exports = {
  module: 'moderation',
  data: new SlashCommandBuilder()
    .setName('modlogs')
    .setDescription('Affiche l\'historique de modération d\'un membre')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre').setRequired(true))
    .addStringOption((opt) =>
      opt
        .setName('type')
        .setDescription('Filtrer par type')
        .addChoices(
          { name: 'Warn', value: 'WARN' },
          { name: 'Timeout', value: 'TIMEOUT' },
          { name: 'Kick', value: 'KICK' },
          { name: 'Ban', value: 'BAN' },
          { name: 'Tempban', value: 'TEMPBAN' }
        )
    ),

  async execute(interaction) {
    const user = interaction.options.getUser('membre');
    const type = interaction.options.getString('type');

    const sanctions = await sanctionQueries.listForUser(interaction.guild.id, user.id, {
      type,
      limit: 50,
    });

    if (sanctions.length === 0) {
      return interaction.reply({
        embeds: [createEmbed('info').setTitle(t('mod.modlogs.title', undefined, { user: user.tag })).setDescription(t('mod.modlogs.empty'))],
        ephemeral: true,
      });
    }

    const formatter = (s, idx) => {
      const status = s.active ? '🔴' : '⚪';
      const dur = s.duration ? ` (${formatDuration(s.duration)})` : '';
      return `${status} **#${s.case_number}** | ${s.type}${dur} — ${s.reason}\n  └ <@${s.moderator_id}> — <t:${Math.floor(new Date(s.created_at).getTime() / 1000)}:R>`;
    };

    const pages = paginateEmbeds(sanctions, 5, formatter, {
      title: t('mod.modlogs.title', undefined, { user: user.tag }),
      color: 'moderation',
    });

    // Ajouter les stats au premier embed
    const counts = await sanctionQueries.countForUser(interaction.guild.id, user.id);
    const statsText = Object.entries(counts)
      .map(([k, v]) => `${k}: **${v}**`)
      .join(' | ');
    pages[0].addFields({ name: '📊 Résumé', value: statsText || 'N/A' });

    await interaction.reply({ embeds: [pages[0]], ephemeral: true });

    // TODO: Pagination interactive avec boutons si > 1 page
  },
};
