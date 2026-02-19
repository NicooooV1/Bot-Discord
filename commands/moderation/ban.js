// ===================================
// Ultra Suite — Moderation: /ban
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
    .setName('ban')
    .setDescription('Bannit un membre du serveur')
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à bannir').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison du ban'))
    .addStringOption((opt) => opt.setName('duree').setDescription('Durée (ex: 7d, 24h) — laissez vide pour permanent'))
    .addIntegerOption((opt) =>
      opt
        .setName('purge')
        .setDescription('Supprimer les messages des X derniers jours')
        .setMinValue(0)
        .setMaxValue(7)
    ),

  async execute(interaction) {
    const target = interaction.options.getMember('membre');
    const user = interaction.options.getUser('membre');
    const reason = interaction.options.getString('raison') || 'Aucune raison';
    const durationStr = interaction.options.getString('duree');
    const purge = interaction.options.getInteger('purge') || 0;

    // Vérifications de hiérarchie
    if (target) {
      const check = canModerate(interaction.member, target);
      if (!check.allowed) {
        return interaction.reply({ embeds: [errorEmbed(t(`common.${check.reason}`))], ephemeral: true });
      }
    }

    // Durée (tempban)
    const duration = durationStr ? parseDuration(durationStr) : null;
    const type = duration ? 'TEMPBAN' : 'BAN';
    const expiresAt = duration ? new Date(Date.now() + duration * 1000).toISOString() : null;

    // DM avant ban
    try {
      await user.send(t('mod.ban.dm', undefined, { guild: interaction.guild.name, reason }));
    } catch {}

    // Ban
    await interaction.guild.members.ban(user.id, {
      reason: `${reason} — par ${interaction.user.tag}`,
      deleteMessageSeconds: purge * 86400,
    });

    // Enregistrer en DB
    const { caseNumber } = await sanctionQueries.create({
      guildId: interaction.guild.id,
      type,
      targetId: user.id,
      moderatorId: interaction.user.id,
      reason,
      duration,
      expiresAt,
    });

    // Log en DB
    await logQueries.create({
      guildId: interaction.guild.id,
      type: 'MOD_ACTION',
      actorId: interaction.user.id,
      targetId: user.id,
      targetType: 'user',
      details: { action: type, reason, caseNumber, duration },
    });

    // Embed de confirmation
    const embed = modEmbed({
      type: type === 'TEMPBAN' ? '🔨 Tempban' : '🔨 Ban',
      target: user.tag,
      moderator: interaction.user.tag,
      reason,
      caseNumber,
      duration: duration ? formatDuration(duration) : null,
    });

    await interaction.reply({ embeds: [embed] });

    // Envoyer dans le salon modlogs
    const config = await configService.get(interaction.guild.id);
    if (config.modLogChannel) {
      const logChannel = interaction.guild.channels.cache.get(config.modLogChannel);
      if (logChannel) logChannel.send({ embeds: [embed] }).catch(() => {});
    }
  },
};
