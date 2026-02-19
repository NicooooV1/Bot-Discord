// ===================================
// Ultra Suite — Moderation: /warn
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const sanctionQueries = require('../../database/sanctionQueries');
const logQueries = require('../../database/logQueries');
const configService = require('../../core/configService');
const { canModerate } = require('../../utils/permissions');
const { modEmbed, errorEmbed, successEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'moderation',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('warn')
    .setDescription('Avertit un membre')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à avertir').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison de l\'avertissement')),

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

    // Créer le warn
    const { caseNumber } = await sanctionQueries.create({
      guildId: interaction.guild.id,
      type: 'WARN',
      targetId: target.id,
      moderatorId: interaction.user.id,
      reason,
    });

    const total = await sanctionQueries.activeWarns(interaction.guild.id, target.id);

    // DM
    try {
      await target.user.send(t('mod.warn.dm', undefined, { guild: interaction.guild.name, reason }));
    } catch {}

    await logQueries.create({
      guildId: interaction.guild.id,
      type: 'MOD_ACTION',
      actorId: interaction.user.id,
      targetId: target.id,
      targetType: 'user',
      details: { action: 'WARN', reason, caseNumber, total },
    });

    const embed = modEmbed({
      type: '⚠️ Warn',
      target: target.user.tag,
      moderator: interaction.user.tag,
      reason,
      caseNumber,
    }).addFields({ name: 'Warns actifs', value: `${total}`, inline: true });

    await interaction.reply({ embeds: [embed] });

    // Auto-action si seuil atteint
    const config = await configService.get(interaction.guild.id);
    if (config.automod?.maxWarns && total >= config.automod.maxWarns) {
      const action = config.automod.warnAction || 'TIMEOUT';
      const duration = config.automod.warnActionDuration || 3600;

      if (action === 'TIMEOUT') {
        await target.timeout(duration * 1000, `Auto-action : ${total} warns`).catch(() => {});
        await interaction.followUp({
          embeds: [successEmbed(`⚠️ **${target.user.tag}** a atteint ${total} warns — timeout automatique appliqué.`)],
        });
      } else if (action === 'KICK') {
        await target.kick(`Auto-action : ${total} warns`).catch(() => {});
        await interaction.followUp({
          embeds: [successEmbed(`⚠️ **${target.user.tag}** a atteint ${total} warns — kick automatique.`)],
        });
      } else if (action === 'BAN') {
        await interaction.guild.members.ban(target.id, { reason: `Auto-action : ${total} warns` }).catch(() => {});
        await interaction.followUp({
          embeds: [successEmbed(`⚠️ **${target.user.tag}** a atteint ${total} warns — ban automatique.`)],
        });
      }
    }

    if (config.modLogChannel) {
      const logChannel = interaction.guild.channels.cache.get(config.modLogChannel);
      if (logChannel) logChannel.send({ embeds: [embed] }).catch(() => {});
    }
  },
};
