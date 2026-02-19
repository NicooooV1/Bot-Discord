// ===================================
// Ultra Suite — Event: guildMemberAdd
// Logs + Onboarding (welcome)
// ===================================

const configService = require('../../core/configService');
const logQueries = require('../../database/logQueries');
const guildQueries = require('../../database/guildQueries');
const { logEmbed, createEmbed } = require('../../utils/embeds');
const { relativeTime } = require('../../utils/formatters');
const { t } = require('../../core/i18n');

module.exports = {
  name: 'guildMemberAdd',

  async execute(member) {
    if (member.user.bot) return;

    try {
      // Initialiser la guild en DB si nécessaire
      await guildQueries.getOrCreate(member.guild.id, member.guild.name, member.guild.ownerId);
    } catch {}

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
        const accountAge = Math.floor((Date.now() - member.user.createdTimestamp) / 86400000);
        const embed = logEmbed({
          title: '📥 Membre rejoint',
          color: 'success',
          fields: [
            { name: 'Membre', value: `${member} (${member.user.tag})`, inline: true },
            { name: 'ID', value: member.id, inline: true },
            { name: 'Compte créé', value: `${relativeTime(member.user.createdAt)} (${accountAge}j)`, inline: true },
            { name: 'Membres', value: `${member.guild.memberCount}`, inline: true },
          ],
        });

        // Alert si compte jeune (< 7 jours)
        if (accountAge < 7) {
          embed.addFields({ name: '⚠️ Compte suspect', value: `Compte créé il y a seulement ${accountAge} jour(s)` });
        }

        logChannel.send({ embeds: [embed] }).catch(() => {});
      }

      await logQueries.create({
        guildId: member.guild.id,
        type: 'MEMBER_JOIN',
        targetId: member.id,
        targetType: 'user',
        details: { tag: member.user.tag, accountAge: Math.floor((Date.now() - member.user.createdTimestamp) / 1000) },
      });
    }

    // === ONBOARDING : Message de bienvenue ===
    const onboardingEnabled = await configService.isModuleEnabled(member.guild.id, 'onboarding');
    if (onboardingEnabled && config.welcomeChannel) {
      const welcomeChannel = member.guild.channels.cache.get(config.welcomeChannel);
      if (welcomeChannel) {
        const msg = (config.welcomeMessage || t('onboarding.welcome'))
          .replace(/\{\{user\}\}/g, member.toString())
          .replace(/\{\{guild\}\}/g, member.guild.name)
          .replace(/\{\{count\}\}/g, member.guild.memberCount);

        const embed = createEmbed('success')
          .setThumbnail(member.user.displayAvatarURL({ size: 256 }))
          .setDescription(msg);

        welcomeChannel.send({ embeds: [embed] }).catch(() => {});
      }

      // Auto-rôle
      if (config.welcomeRole) {
        try {
          await member.roles.add(config.welcomeRole, 'Auto-rôle de bienvenue');
        } catch {}
      }
    }
  },
};
