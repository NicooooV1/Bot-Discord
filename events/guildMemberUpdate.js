// ===================================
// Ultra Suite — Event: guildMemberUpdate
// Log les changements de rôles, pseudo, timeout
// ===================================

const { EmbedBuilder } = require('discord.js');
const configService = require('../core/configService');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('MemberUpdate');

module.exports = {
  name: 'guildMemberUpdate',
  once: false,

  async execute(oldMember, newMember) {
    if (newMember.user.bot) return;
    const guildId = newMember.guild.id;
    if (!(await configService.isModuleEnabled(guildId, 'logs'))) return;

    const config = await configService.get(guildId);
    const logChannelId = config.logChannel;
    if (!logChannelId) return;
    const logChannel = newMember.guild.channels.cache.get(logChannelId);
    if (!logChannel) return;

    // --- Changement de rôles ---
    const oldRoles = oldMember.roles.cache;
    const newRoles = newMember.roles.cache;
    const addedRoles = newRoles.filter((r) => !oldRoles.has(r.id));
    const removedRoles = oldRoles.filter((r) => !newRoles.has(r.id));

    if (addedRoles.size > 0 || removedRoles.size > 0) {
      try {
        const embed = new EmbedBuilder()
          .setAuthor({ name: `Rôles modifiés — ${newMember.user.tag}`, iconURL: newMember.user.displayAvatarURL() })
          .setColor(0x5865F2)
          .setFooter({ text: `ID: ${newMember.id}` })
          .setTimestamp();
        if (addedRoles.size > 0) embed.addFields({ name: '➕ Ajouté(s)', value: addedRoles.map((r) => r.toString()).join(', '), inline: false });
        if (removedRoles.size > 0) embed.addFields({ name: '➖ Retiré(s)', value: removedRoles.map((r) => r.toString()).join(', '), inline: false });
        await logChannel.send({ embeds: [embed] });
      } catch (err) { log.error(`Erreur log rôles: ${err.message}`); }
    }

    // --- Changement de pseudo ---
    if (oldMember.nickname !== newMember.nickname) {
      try {
        const embed = new EmbedBuilder()
          .setAuthor({ name: `Pseudo modifié — ${newMember.user.tag}`, iconURL: newMember.user.displayAvatarURL() })
          .addFields(
            { name: 'Avant', value: oldMember.nickname || '*Aucun*', inline: true },
            { name: 'Après', value: newMember.nickname || '*Aucun*', inline: true },
          )
          .setColor(0xFEE75C).setFooter({ text: `ID: ${newMember.id}` }).setTimestamp();
        await logChannel.send({ embeds: [embed] });
      } catch (err) { log.error(`Erreur log nickname: ${err.message}`); }
    }

    // --- Timeout ---
    const oldTimeout = oldMember.communicationDisabledUntilTimestamp;
    const newTimeout = newMember.communicationDisabledUntilTimestamp;
    if (oldTimeout !== newTimeout) {
      try {
        const isTimedOut = newTimeout && newTimeout > Date.now();
        const embed = new EmbedBuilder()
          .setAuthor({
            name: isTimedOut ? `Timeout appliqué — ${newMember.user.tag}` : `Timeout retiré — ${newMember.user.tag}`,
            iconURL: newMember.user.displayAvatarURL(),
          })
          .setColor(isTimedOut ? 0xED4245 : 0x57F287)
          .setFooter({ text: `ID: ${newMember.id}` }).setTimestamp();
        if (isTimedOut) embed.addFields({ name: 'Expire', value: `<t:${Math.floor(newTimeout / 1000)}:R>`, inline: true });
        await logChannel.send({ embeds: [embed] });
      } catch (err) { log.error(`Erreur log timeout: ${err.message}`); }
    }
  },
};