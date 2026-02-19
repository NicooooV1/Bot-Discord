// ===================================
// Ultra Suite — Moderation: /mute
// Alias de /timeout pour compatibilité
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const sanctionQueries = require('../../database/sanctionQueries');
const logQueries = require('../../database/logQueries');
const configService = require('../../core/configService');
const { canModerate } = require('../../utils/permissions');
const { modEmbed, errorEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');
const { parseDuration, formatDuration } = require('../../utils/formatters');

module.exports = {
  module: 'moderation',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('mute')
    .setDescription('🔇 Rendre muet un utilisateur (timeout)')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à mute').setRequired(true))
    .addStringOption((opt) => opt.setName('duree').setDescription('Durée (ex: 10m, 1h, 1d, 1w)').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison du mute')),

  async execute(interaction) {
    const target = interaction.options.getMember('membre');
    const durationStr = interaction.options.getString('duree');
    const reason = interaction.options.getString('raison') || 'Aucune raison';

    if (!target) {
      return interaction.reply({ embeds: [errorEmbed(t('common.invalid_user'))], ephemeral: true });
    }

    const check = canModerate(interaction.member, target);
    if (!check.allowed) {
      return interaction.reply({ embeds: [errorEmbed(t(`common.${check.reason}`))], ephemeral: true });
    }

    const duration = parseDuration(durationStr);
    if (!duration || duration > 28 * 86400) {
      return interaction.reply({ embeds: [errorEmbed('❌ Durée invalide (max 28 jours).')], ephemeral: true });
    }

    // Discord timeout attend des ms, parseDuration retourne des secondes
    await target.timeout(duration * 1000, `${reason} — par ${interaction.user.tag}`);

    // DM
    try {
      await target.user.send(`🔇 Vous avez été réduit au silence sur **${interaction.guild.name}** pour **${formatDuration(duration)}**.\nRaison : ${reason}`);
    } catch {}

    // DB
    const expiresAt = new Date(Date.now() + duration * 1000).toISOString();
    const { caseNumber } = await sanctionQueries.create({
      guildId: interaction.guild.id,
      type: 'TIMEOUT',
      targetId: target.id,
      moderatorId: interaction.user.id,
      reason,
      duration,
      expiresAt,
    });

    await logQueries.create({
      guildId: interaction.guild.id,
      type: 'MOD_ACTION',
      actorId: interaction.user.id,
      targetId: target.id,
      targetType: 'user',
      details: { action: 'MUTE', reason, caseNumber, duration },
    });

    const embed = modEmbed({
      type: '🔇 Mute',
      target: target.user.tag,
      moderator: interaction.user.tag,
      reason,
      caseNumber,
      duration: formatDuration(duration),
    });

    await interaction.reply({ embeds: [embed] });

    // Modlog channel
    const config = await configService.get(interaction.guild.id);
    if (config.modLogChannel) {
      const logChannel = interaction.guild.channels.cache.get(config.modLogChannel);
      if (logChannel) logChannel.send({ embeds: [embed] }).catch(() => {});
    }
  },
};
