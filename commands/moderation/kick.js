// ===================================
// Ultra Suite — Moderation: /kick
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const sanctionQueries = require('../../database/sanctionQueries');
const logQueries = require('../../database/logQueries');
const configService = require('../../core/configService');
const { canModerate } = require('../../utils/permissions');
const { modEmbed, errorEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'moderation',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('kick')
    .setDescription('Expulse un membre du serveur')
    .setDefaultMemberPermissions(PermissionFlagsBits.KickMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à expulser').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison de l\'expulsion')),

  async execute(interaction) {
    const target = interaction.options.getMember('membre');
    const reason = interaction.options.getString('raison') || 'Aucune raison';

    if (!target) {
      return interaction.reply({ embeds: [errorEmbed(t('common.invalid_user'))], ephemeral: true });
    }

    const check = canModerate(interaction.member, target);
    if (!check.allowed) {
      return interaction.reply({ embeds: [errorEmbed(t(`common.${check.reason}`))], ephemeral: true });
    }

    await interaction.deferReply();

    // DM
    try {
      await target.user.send(t('mod.kick.dm', undefined, { guild: interaction.guild.name, reason }));
    } catch {}

    // Kick
    await target.kick(`${reason} — par ${interaction.user.tag}`);

    // DB
    const { caseNumber } = await sanctionQueries.create({
      guildId: interaction.guild.id,
      type: 'KICK',
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
      details: { action: 'KICK', reason, caseNumber },
    });

    const embed = modEmbed({
      type: '👢 Kick',
      target: target.user.tag,
      moderator: interaction.user.tag,
      reason,
      caseNumber,
    });

    await interaction.editReply({ embeds: [embed] });

    const config = await configService.get(interaction.guild.id);
    if (config.modLogChannel) {
      const logChannel = interaction.guild.channels.cache.get(config.modLogChannel);
      if (logChannel) logChannel.send({ embeds: [embed] }).catch(() => {});
    }
  },
};
