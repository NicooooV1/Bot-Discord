// ===================================
// Ultra Suite — inviteCreate / inviteDelete
// ===================================

const { EmbedBuilder } = require('discord.js');
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
    name: 'inviteCreate',
    async execute(invite) {
      try {
        if (!invite.guild) return;
        const ch = await getLogChannel(invite.guild);
        if (!ch) return;

        // Track invite for invite tracking
        const db = getDb();
        await db('invite_tracking').insert({
          guild_id: invite.guild.id,
          code: invite.code,
          inviter_id: invite.inviter?.id || 'unknown',
          channel_id: invite.channel?.id,
          uses: 0,
          max_uses: invite.maxUses || 0,
          expires_at: invite.expiresAt,
        }).onConflict(['guild_id', 'code']).merge().catch(() => {});

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('🔗 Invitation créée')
            .setColor(0x3498DB)
            .addFields(
              { name: 'Code', value: `\`${invite.code}\``, inline: true },
              { name: 'Créée par', value: invite.inviter?.tag || 'Inconnu', inline: true },
              { name: 'Salon', value: invite.channel ? `${invite.channel}` : 'N/A', inline: true },
              { name: 'Max utilisations', value: String(invite.maxUses || '∞'), inline: true },
              { name: 'Expire', value: invite.expiresAt ? `<t:${Math.floor(invite.expiresAt.getTime() / 1000)}:R>` : 'Jamais', inline: true },
            )
            .setTimestamp()],
        });
      } catch (e) {}
    },
  },
  {
    name: 'inviteDelete',
    async execute(invite) {
      try {
        if (!invite.guild) return;
        const ch = await getLogChannel(invite.guild);
        if (!ch) return;

        await ch.send({
          embeds: [new EmbedBuilder()
            .setTitle('🗑️ Invitation supprimée')
            .setColor(0xE74C3C)
            .addFields(
              { name: 'Code', value: `\`${invite.code}\``, inline: true },
              { name: 'Salon', value: invite.channel ? `${invite.channel}` : 'N/A', inline: true },
            )
            .setTimestamp()],
        });
      } catch (e) {}
    },
  },
];
