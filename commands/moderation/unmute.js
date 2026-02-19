// ===================================
// Ultra Suite — Moderation: /unmute
// Retire le timeout d'un membre
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const sanctionQueries = require('../../database/sanctionQueries');
const logQueries = require('../../database/logQueries');
const configService = require('../../core/configService');
const { modEmbed, errorEmbed, successEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'moderation',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('unmute')
    .setDescription('🔊 Retirer le mute d\'un utilisateur')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à unmute').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison du unmute')),

  async execute(interaction) {
    const target = interaction.options.getMember('membre');
    const reason = interaction.options.getString('raison') || 'Aucune raison';

    if (!target) {
      return interaction.reply({ embeds: [errorEmbed(t('common.invalid_user'))], ephemeral: true });
    }

    if (!target.isCommunicationDisabled()) {
      return interaction.reply({ embeds: [errorEmbed('❌ Cet utilisateur n\'est pas muté.')], ephemeral: true });
    }

    await target.timeout(null, `${reason} — par ${interaction.user.tag}`);

    const { caseNumber } = await sanctionQueries.create({
      guildId: interaction.guild.id,
      type: 'UNMUTE',
      targetId: target.id,
      moderatorId: interaction.user.id,
      reason,
    });

    await logQueries.create({
      guildId: interaction.guild.id,
      type: 'MOD_ACTION',
      actorId: interaction.user.id,
      targetId: target.id,
      targetType: 'user',
      details: { action: 'UNMUTE', reason, caseNumber },
    });

    const embed = modEmbed({
      type: '🔊 Unmute',
      target: target.user.tag,
      moderator: interaction.user.tag,
      reason,
      caseNumber,
    });

    await interaction.reply({ embeds: [embed] });

    const config = await configService.get(interaction.guild.id);
    if (config.modLogChannel) {
      const logChannel = interaction.guild.channels.cache.get(config.modLogChannel);
      if (logChannel) logChannel.send({ embeds: [embed] }).catch(() => {});
    }
  },
};
