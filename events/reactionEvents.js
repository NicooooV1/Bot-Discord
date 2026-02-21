// ===================================
// Ultra Suite — messageReactionAdd / messageReactionRemove
// Starboard + Reaction Roles
// ===================================

const { EmbedBuilder } = require('discord.js');
const { getDb } = require('../database');

module.exports = [
  {
    name: 'messageReactionAdd',
    async execute(reaction, user) {
      if (user.bot) return;
      if (reaction.partial) await reaction.fetch().catch(() => {});
      if (reaction.message.partial) await reaction.message.fetch().catch(() => {});
      if (!reaction.message.guild) return;

      const db = getDb();
      const guildId = reaction.message.guild.id;

      // ===== REACTION ROLES =====
      try {
        const rr = await db('reaction_roles').where({ guild_id: guildId, message_id: reaction.message.id }).first();
        if (rr) {
          const roles = JSON.parse(rr.roles);
          const match = roles.find((r) => r.emoji === reaction.emoji.name || r.emoji === reaction.emoji.toString());
          if (match) {
            const member = await reaction.message.guild.members.fetch(user.id).catch(() => null);
            if (member) {
              // Unique mode: remove other roles first
              if (rr.mode === 'unique') {
                for (const r of roles) {
                  if (r.roleId !== match.roleId && member.roles.cache.has(r.roleId)) {
                    await member.roles.remove(r.roleId).catch(() => {});
                    // Remove other reactions
                    const otherReaction = reaction.message.reactions.cache.find((rx) => rx.emoji.name === r.emoji || rx.emoji.toString() === r.emoji);
                    if (otherReaction) await otherReaction.users.remove(user.id).catch(() => {});
                  }
                }
              }
              await member.roles.add(match.roleId).catch(() => {});
            }
          }
        }
      } catch (e) {}

      // ===== STARBOARD =====
      try {
        const emoji = reaction.emoji.name;
        const starConfig = await db('starboard_config').where({ guild_id: guildId }).first();
        if (!starConfig || !starConfig.enabled) return;

        const triggerEmoji = starConfig.emoji || '⭐';
        if (emoji !== triggerEmoji) return;

        const threshold = starConfig.threshold || 3;
        const count = reaction.count;

        if (count >= threshold) {
          const msg = reaction.message;
          const channelId = starConfig.channel_id;
          if (!channelId) return;

          const starChannel = await msg.guild.channels.fetch(channelId).catch(() => null);
          if (!starChannel) return;

          // Check if already on starboard
          const existing = await db('starboard_entries').where({ guild_id: guildId, message_id: msg.id }).first();

          const embed = new EmbedBuilder()
            .setAuthor({ name: msg.author.tag, iconURL: msg.author.displayAvatarURL() })
            .setColor(0xFFD700)
            .setDescription(msg.content || '*[Pas de contenu texte]*')
            .addFields(
              { name: 'Source', value: `[Aller au message](${msg.url})`, inline: true },
              { name: 'Salon', value: `<#${msg.channelId}>`, inline: true },
            )
            .setTimestamp(msg.createdAt);

          if (msg.attachments.size) embed.setImage(msg.attachments.first().url);

          const content = `⭐ **${count}** | <#${msg.channelId}>`;

          if (existing?.starboard_message_id) {
            // Update existing
            try {
              const starMsg = await starChannel.messages.fetch(existing.starboard_message_id);
              await starMsg.edit({ content, embeds: [embed] });
            } catch (e) {
              // Message deleted, recreate
              const newMsg = await starChannel.send({ content, embeds: [embed] });
              await db('starboard_entries').where({ id: existing.id }).update({ starboard_message_id: newMsg.id, stars: count });
            }
            await db('starboard_entries').where({ id: existing.id }).update({ stars: count });
          } else {
            // Create new
            const starMsg = await starChannel.send({ content, embeds: [embed] });
            await db('starboard_entries').insert({
              guild_id: guildId,
              message_id: msg.id,
              channel_id: msg.channelId,
              author_id: msg.author.id,
              starboard_message_id: starMsg.id,
              stars: count,
            }).onConflict(['guild_id', 'message_id']).merge();
          }
        }
      } catch (e) {}
    },
  },
  {
    name: 'messageReactionRemove',
    async execute(reaction, user) {
      if (user.bot) return;
      if (reaction.partial) await reaction.fetch().catch(() => {});
      if (reaction.message.partial) await reaction.message.fetch().catch(() => {});
      if (!reaction.message.guild) return;

      const db = getDb();
      const guildId = reaction.message.guild.id;

      // ===== REACTION ROLES - REMOVE =====
      try {
        const rr = await db('reaction_roles').where({ guild_id: guildId, message_id: reaction.message.id }).first();
        if (rr && rr.mode !== 'required') {
          const roles = JSON.parse(rr.roles);
          const match = roles.find((r) => r.emoji === reaction.emoji.name || r.emoji === reaction.emoji.toString());
          if (match) {
            const member = await reaction.message.guild.members.fetch(user.id).catch(() => null);
            if (member) await member.roles.remove(match.roleId).catch(() => {});
          }
        }
      } catch (e) {}

      // ===== STARBOARD UPDATE =====
      try {
        const emoji = reaction.emoji.name;
        const starConfig = await db('starboard_config').where({ guild_id: guildId }).first();
        if (!starConfig || emoji !== (starConfig.emoji || '⭐')) return;

        const existing = await db('starboard_entries').where({ guild_id: guildId, message_id: reaction.message.id }).first();
        if (!existing) return;

        const count = reaction.count || 0;
        if (count < (starConfig.threshold || 3)) {
          // Remove from starboard
          if (existing.starboard_message_id) {
            const starChannel = await reaction.message.guild.channels.fetch(starConfig.channel_id).catch(() => null);
            if (starChannel) {
              const msg = await starChannel.messages.fetch(existing.starboard_message_id).catch(() => null);
              if (msg) await msg.delete().catch(() => {});
            }
          }
          await db('starboard_entries').where({ id: existing.id }).delete();
        } else {
          await db('starboard_entries').where({ id: existing.id }).update({ stars: count });
        }
      } catch (e) {}
    },
  },
];
