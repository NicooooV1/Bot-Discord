// ===================================
// Ultra Suite — Moderation: /unban
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const sanctionQueries = require('../../database/sanctionQueries');
const logQueries = require('../../database/logQueries');
const { successEmbed, errorEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'moderation',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('unban')
    .setDescription('Débannit un utilisateur')
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .addStringOption((opt) => opt.setName('id').setDescription('ID de l\'utilisateur').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison du débannissement')),

  async execute(interaction) {
    const userId = interaction.options.getString('id');
    const reason = interaction.options.getString('raison') || 'Aucune raison';

    try {
      await interaction.guild.bans.remove(userId, `${reason} — par ${interaction.user.tag}`);
    } catch {
      return interaction.reply({ embeds: [errorEmbed('❌ Utilisateur non banni ou ID invalide.')], ephemeral: true });
    }

    // Enregistrer
    const { caseNumber } = await sanctionQueries.create({
      guildId: interaction.guild.id,
      type: 'UNBAN',
      targetId: userId,
      moderatorId: interaction.user.id,
      reason,
    });

    await logQueries.create({
      guildId: interaction.guild.id,
      type: 'MOD_ACTION',
      actorId: interaction.user.id,
      targetId: userId,
      targetType: 'user',
      details: { action: 'UNBAN', reason, caseNumber },
    });

    await interaction.reply({ embeds: [successEmbed(t('mod.unban.success', undefined, { user: userId }))] });
  },
};
