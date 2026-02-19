// ===================================
// Ultra Suite — Event: guildMemberRemove
// Logs + Goodbye
// ===================================

const configService = require('../../core/configService');
const logQueries = require('../../database/logQueries');
const { logEmbed, createEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  name: 'guildMemberRemove',

  async execute(member) {
    if (member.user.bot) return;

    let config;
    try {
      config = await configService.get(member.guild.id);
    } catch {
      return;
    }

    // === LOGS ===
    const logsEnabled = await configService.isModuleEnabled(member.guild.id, 'logs');
    if (logsEnabled && config.logChannel) {
      const logChannel = member.guild.channels.cache.get(config.logChannel);
      if (logChannel) {
        const roles = member.roles.cache
          .filter((r) => r.id !== member.guild.id)
          .map((r) => r.toString())
          .join(', ');

        const embed = logEmbed({
          title: '📤 Membre parti',
          color: 'error',
          fields: [
            { name: 'Membre', value: `${member.user.tag} (${member.id})`, inline: true },
            { name: 'Membres', value: `${member.guild.memberCount}`, inline: true },
            { name: 'Rôles', value: roles || 'Aucun', inline: false },
          ],
        });

        logChannel.send({ embeds: [embed] }).catch(() => {});
      }

      await logQueries.create({
        guildId: member.guild.id,
        type: 'MEMBER_LEAVE',
        targetId: member.id,
        targetType: 'user',
        details: { tag: member.user.tag, roles: member.roles.cache.map((r) => r.id) },
      });
    }

    // === GOODBYE ===
    const onboardingEnabled = await configService.isModuleEnabled(member.guild.id, 'onboarding');
    if (onboardingEnabled && config.goodbyeChannel) {
      const goodbyeChannel = member.guild.channels.cache.get(config.goodbyeChannel);
      if (goodbyeChannel) {
        const msg = (config.goodbyeMessage || t('onboarding.goodbye'))
          .replace(/\{\{user\}\}/g, member.user.tag)
          .replace(/\{\{guild\}\}/g, member.guild.name)
          .replace(/\{\{count\}\}/g, member.guild.memberCount);

        const embed = createEmbed('logs').setDescription(msg);
        goodbyeChannel.send({ embeds: [embed] }).catch(() => {});
      }
    }
  },
};
