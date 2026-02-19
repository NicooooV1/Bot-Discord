// ===================================
// Ultra Suite — Moderation: /softban
// Ban + unban pour purger les messages
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
    .setName('softban')
    .setDescription('🧹 Softban — Bannir puis débannir pour supprimer les messages')
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à softban').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison du softban'))
    .addIntegerOption((opt) =>
      opt.setName('jours').setDescription('Messages à supprimer (jours, défaut: 7)').setMinValue(1).setMaxValue(7)
    ),

  async execute(interaction) {
    const target = interaction.options.getMember('membre');
    const user = interaction.options.getUser('membre');
    const reason = interaction.options.getString('raison') || 'Aucune raison';
    const days = interaction.options.getInteger('jours') || 7;

    if (target) {
      const check = canModerate(interaction.member, target);
      if (!check.allowed) {
        return interaction.reply({ embeds: [errorEmbed(t(`common.${check.reason}`))], ephemeral: true });
      }
    }

    // DM
    try {
      await user.send(`🧹 Vous avez été softban de **${interaction.guild.name}**.\nRaison : ${reason}`);
    } catch {}

    // Ban + unban
    await interaction.guild.members.ban(user.id, {
      reason: `[SOFTBAN] ${reason} — par ${interaction.user.tag}`,
      deleteMessageSeconds: days * 86400,
    });
    await interaction.guild.members.unban(user.id, `[SOFTBAN] ${reason} — par ${interaction.user.tag}`);

    const { caseNumber } = await sanctionQueries.create({
      guildId: interaction.guild.id,
      type: 'SOFTBAN',
      targetId: user.id,
      moderatorId: interaction.user.id,
      reason,
    });

    await logQueries.create({
      guildId: interaction.guild.id,
      type: 'MOD_ACTION',
      actorId: interaction.user.id,
      targetId: user.id,
      targetType: 'user',
      details: { action: 'SOFTBAN', reason, caseNumber, days },
    });

    const embed = modEmbed({
      type: '🧹 Softban',
      target: user.tag,
      moderator: interaction.user.tag,
      reason,
      caseNumber,
      duration: `${days}j de messages supprimés`,
    });

    await interaction.reply({ embeds: [embed] });

    const config = await configService.get(interaction.guild.id);
    if (config.modLogChannel) {
      const logChannel = interaction.guild.channels.cache.get(config.modLogChannel);
      if (logChannel) logChannel.send({ embeds: [embed] }).catch(() => {});
    }
  },
};
