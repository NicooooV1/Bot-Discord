// ===================================
// Ultra Suite — roleCreate, roleDelete, roleUpdate events
// ===================================

const { EmbedBuilder, AuditLogEvent } = require('discord.js');
const { getDb } = require('../database');

async function getLogChannel(guild) {
  const db = getDb();
  const config = await db('guild_config').where({ guild_id: guild.id }).first();
  if (!config) return null;
  const settings = config.settings ? JSON.parse(config.settings) : {};
  const logChannelId = settings.log_channel || settings.logChannel;
  if (!logChannelId) return null;
  return guild.channels.fetch(logChannelId).catch(() => null);
}

module.exports = [
  {
    name: 'roleCreate',
    async execute(role) {
      try {
        const ch = await getLogChannel(role.guild);
        if (!ch) return;

        let executor = 'Inconnu';
        try {
          const audit = await role.guild.fetchAuditLogs({ type: AuditLogEvent.RoleCreate, limit: 1 });
          executor = audit.entries.first()?.executor?.tag || 'Inconnu';
        } catch (e) {}

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('🏷️ Rôle créé')
            .setColor(role.color || 0x2ECC71)
            .addFields(
              { name: 'Rôle', value: `${role} (\`${role.name}\`)`, inline: true },
              { name: 'Couleur', value: role.hexColor, inline: true },
              { name: 'Créé par', value: executor, inline: true },
            )
            .setTimestamp()],
        });
      } catch (e) {}
    },
  },
  {
    name: 'roleDelete',
    async execute(role) {
      try {
        const ch = await getLogChannel(role.guild);
        if (!ch) return;

        let executor = 'Inconnu';
        try {
          const audit = await role.guild.fetchAuditLogs({ type: AuditLogEvent.RoleDelete, limit: 1 });
          executor = audit.entries.first()?.executor?.tag || 'Inconnu';
        } catch (e) {}

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('🗑️ Rôle supprimé')
            .setColor(0xE74C3C)
            .addFields(
              { name: 'Rôle', value: `\`${role.name}\``, inline: true },
              { name: 'Couleur', value: role.hexColor, inline: true },
              { name: 'Supprimé par', value: executor, inline: true },
            )
            .setTimestamp()],
        });

        // Anti-nuke tracking
        const db = getDb();
        const guildConfig = await db('guild_config').where({ guild_id: role.guild.id }).first();
        const modules = guildConfig?.modules ? JSON.parse(guildConfig.modules) : {};
        if (modules.antinuke?.enabled) {
          await db('antinuke_log').insert({
            guild_id: role.guild.id,
            user_id: 'system',
            action: 'role_delete',
            details: `Rôle supprimé: ${role.name} par ${executor}`,
          }).catch(() => {});
        }
      } catch (e) {}
    },
  },
  {
    name: 'roleUpdate',
    async execute(oldRole, newRole) {
      try {
        const ch = await getLogChannel(newRole.guild);
        if (!ch) return;

        const changes = [];
        if (oldRole.name !== newRole.name) changes.push(`📝 Nom: \`${oldRole.name}\` → \`${newRole.name}\``);
        if (oldRole.hexColor !== newRole.hexColor) changes.push(`🎨 Couleur: ${oldRole.hexColor} → ${newRole.hexColor}`);
        if (oldRole.hoist !== newRole.hoist) changes.push(`📍 Hoisted: ${oldRole.hoist} → ${newRole.hoist}`);
        if (oldRole.mentionable !== newRole.mentionable) changes.push(`📢 Mentionnable: ${oldRole.mentionable} → ${newRole.mentionable}`);
        if (oldRole.permissions.bitfield !== newRole.permissions.bitfield) changes.push(`🔑 Permissions modifiées`);

        if (!changes.length) return;

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('✏️ Rôle modifié')
            .setColor(newRole.color || 0xF39C12)
            .addFields(
              { name: 'Rôle', value: `${newRole}`, inline: true },
              { name: 'Changements', value: changes.join('\n') },
            )
            .setTimestamp()],
        });
      } catch (e) {}
    },
  },
];
