// ===================================
// Ultra Suite — Event: voiceStateUpdate
// Logs + Temp Voice + Voice Sessions
// ===================================

const configService = require('../../core/configService');
const logQueries = require('../../database/logQueries');
const { getDb } = require('../../database');
const { logEmbed } = require('../../utils/embeds');
const { ChannelType, PermissionFlagsBits } = require('discord.js');

module.exports = {
  name: 'voiceStateUpdate',

  async execute(oldState, newState) {
    const member = newState.member || oldState.member;
    if (!member || member.user.bot) return;

    const guild = newState.guild;
    const config = await configService.get(guild.id);
    const db = getDb();

    // === LOGS VOCAL ===
    const logsEnabled = await configService.isModuleEnabled(guild.id, 'logs');
    if (logsEnabled && config.logChannel) {
      const logChannel = guild.channels.cache.get(config.logChannel);
      if (logChannel) {
        // Join
        if (!oldState.channel && newState.channel) {
          logChannel
            .send({
              embeds: [
                logEmbed({
                  title: '🔊 Connexion vocale',
                  color: 'success',
                  fields: [
                    { name: 'Membre', value: `${member} (${member.user.tag})`, inline: true },
                    { name: 'Salon', value: `${newState.channel}`, inline: true },
                  ],
                }),
              ],
            })
            .catch(() => {});
        }
        // Leave
        else if (oldState.channel && !newState.channel) {
          logChannel
            .send({
              embeds: [
                logEmbed({
                  title: '🔇 Déconnexion vocale',
                  color: 'error',
                  fields: [
                    { name: 'Membre', value: `${member} (${member.user.tag})`, inline: true },
                    { name: 'Salon', value: `${oldState.channel}`, inline: true },
                  ],
                }),
              ],
            })
            .catch(() => {});
        }
        // Move
        else if (oldState.channel && newState.channel && oldState.channelId !== newState.channelId) {
          logChannel
            .send({
              embeds: [
                logEmbed({
                  title: '🔀 Déplacement vocal',
                  color: 'info',
                  fields: [
                    { name: 'Membre', value: `${member} (${member.user.tag})`, inline: true },
                    { name: 'De', value: `${oldState.channel}`, inline: true },
                    { name: 'Vers', value: `${newState.channel}`, inline: true },
                  ],
                }),
              ],
            })
            .catch(() => {});
        }
      }
    }

    // === VOICE SESSIONS ===
    // Join → créer session
    if (!oldState.channel && newState.channel) {
      await db('voice_sessions').insert({
        guild_id: guild.id,
        user_id: member.id,
        channel_id: newState.channel.id,
      });
    }
    // Leave → fermer session
    if (oldState.channel && !newState.channel) {
      const session = await db('voice_sessions')
        .where({ guild_id: guild.id, user_id: member.id })
        .whereNull('left_at')
        .orderBy('joined_at', 'desc')
        .first();

      if (session) {
        const duration = Math.floor((Date.now() - new Date(session.joined_at).getTime()) / 1000);
        await db('voice_sessions').where('id', session.id).update({
          left_at: new Date().toISOString(),
          duration,
        });
      }
    }

    // === TEMP VOICE ===
    const tempVoiceEnabled = await configService.isModuleEnabled(guild.id, 'tempvoice');
    if (tempVoiceEnabled && config.tempVoiceLobby) {
      // Rejoint le lobby → créer un salon temporaire
      if (newState.channelId === config.tempVoiceLobby) {
        try {
          const categoryId = config.tempVoiceCategory || newState.channel.parentId;
          const tempChannel = await guild.channels.create({
            name: `🔊 ${member.displayName}`,
            type: ChannelType.GuildVoice,
            parent: categoryId,
            permissionOverwrites: [
              {
                id: member.id,
                allow: [PermissionFlagsBits.ManageChannels, PermissionFlagsBits.MoveMembers],
              },
            ],
          });

          await member.voice.setChannel(tempChannel);

          await db('temp_voice_channels').insert({
            guild_id: guild.id,
            channel_id: tempChannel.id,
            owner_id: member.id,
          });
        } catch {}
      }

      // Quitte un salon temporaire → supprimer si vide
      if (oldState.channel && !newState.channel) {
        const tempEntry = await db('temp_voice_channels').where('channel_id', oldState.channelId).first();
        if (tempEntry && oldState.channel.members.size === 0) {
          await oldState.channel.delete('Salon vocal temporaire vide').catch(() => {});
          await db('temp_voice_channels').where('id', tempEntry.id).del();
        }
      }
    }
  },
};
