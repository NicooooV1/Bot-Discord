// ===================================
// Ultra Suite — guildUpdate event
// ===================================

const { EmbedBuilder, AuditLogEvent } = require('discord.js');
const { getDb } = require('../database');

module.exports = {
  name: 'guildUpdate',
  async execute(oldGuild, newGuild) {
    try {
      const db = getDb();
      const config = await db('guild_config').where({ guild_id: newGuild.id }).first();
      if (!config) return;
      const settings = config.settings ? JSON.parse(config.settings) : {};
      const logChannelId = settings.log_channel || settings.logChannel;
      if (!logChannelId) return;

      const ch = await newGuild.channels.fetch(logChannelId).catch(() => null);
      if (!ch) return;

      const changes = [];
      if (oldGuild.name !== newGuild.name) changes.push(`📝 Nom: \`${oldGuild.name}\` → \`${newGuild.name}\``);
      if (oldGuild.icon !== newGuild.icon) changes.push(`🖼️ Icône modifiée`);
      if (oldGuild.banner !== newGuild.banner) changes.push(`🖼️ Bannière modifiée`);
      if (oldGuild.verificationLevel !== newGuild.verificationLevel) changes.push(`🔒 Niveau de vérification: ${oldGuild.verificationLevel} → ${newGuild.verificationLevel}`);
      if (oldGuild.vanityURLCode !== newGuild.vanityURLCode) changes.push(`🔗 URL vanity: ${oldGuild.vanityURLCode || 'aucune'} → ${newGuild.vanityURLCode || 'aucune'}`);
      if (oldGuild.description !== newGuild.description) changes.push(`📋 Description modifiée`);
      if (oldGuild.premiumTier !== newGuild.premiumTier) changes.push(`💎 Tier boost: ${oldGuild.premiumTier} → ${newGuild.premiumTier}`);

      if (!changes.length) return;

      // Anti-nuke alert for critical changes
      const modules = config.modules ? JSON.parse(config.modules) : {};
      if (modules.antinuke?.enabled) {
        await db('antinuke_log').insert({
          guild_id: newGuild.id,
          user_id: 'system',
          action: 'guild_update',
          details: changes.join('; '),
        }).catch(() => {});
      }

      await ch.send({
        embeds: [new EmbedBuilder()
          .setTitle('🏠 Serveur modifié')
          .setColor(0xF39C12)
          .setThumbnail(newGuild.iconURL())
          .addFields({ name: 'Changements', value: changes.join('\n') })
          .setTimestamp()],
      });
    } catch (e) {}
  },
};
