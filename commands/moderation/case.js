// ===================================
// Ultra Suite — Moderation: /case
// Voir ou révoquer un case
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const sanctionQueries = require('../../database/sanctionQueries');
const { createEmbed, successEmbed, errorEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');
const { formatDuration } = require('../../utils/formatters');

module.exports = {
  module: 'moderation',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('case')
    .setDescription('Voir ou révoquer un dossier de modération')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addSubcommand((sub) =>
      sub
        .setName('view')
        .setDescription('Voir les détails d\'un case')
        .addIntegerOption((opt) =>
          opt.setName('numero').setDescription('Numéro du case').setRequired(true)
        )
    )
    .addSubcommand((sub) =>
      sub
        .setName('revoke')
        .setDescription('Révoquer un case')
        .addIntegerOption((opt) =>
          opt.setName('numero').setDescription('Numéro du case').setRequired(true)
        )
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const caseNumber = interaction.options.getInteger('numero');

    const sanction = await sanctionQueries.getByCase(interaction.guild.id, caseNumber);
    if (!sanction) {
      return interaction.reply({ embeds: [errorEmbed(t('mod.case.not_found', undefined, { case: caseNumber }))], ephemeral: true });
    }

    if (sub === 'view') {
      const embed = createEmbed(sanction.active ? 'moderation' : 'logs')
        .setTitle(`Case #${sanction.case_number} — ${sanction.type}`)
        .addFields(
          { name: 'Cible', value: `<@${sanction.target_id}>`, inline: true },
          { name: 'Modérateur', value: `<@${sanction.moderator_id}>`, inline: true },
          { name: 'Statut', value: sanction.active ? '🔴 Actif' : '⚪ Révoqué', inline: true },
          { name: 'Raison', value: sanction.reason || 'N/A' },
          { name: 'Date', value: `<t:${Math.floor(new Date(sanction.created_at).getTime() / 1000)}:F>`, inline: true }
        );

      if (sanction.duration) {
        embed.addFields({ name: 'Durée', value: formatDuration(sanction.duration), inline: true });
      }
      if (sanction.revoked_by) {
        embed.addFields({ name: 'Révoqué par', value: `<@${sanction.revoked_by}>`, inline: true });
      }

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    if (sub === 'revoke') {
      if (!sanction.active) {
        return interaction.reply({ embeds: [errorEmbed('Ce case est déjà révoqué.')], ephemeral: true });
      }

      await sanctionQueries.revoke(interaction.guild.id, caseNumber, interaction.user.id);
      return interaction.reply({ embeds: [successEmbed(t('mod.case.revoked', undefined, { case: caseNumber }))], ephemeral: true });
    }
  },
};
