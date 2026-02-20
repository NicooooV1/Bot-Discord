// ===================================
// Ultra Suite — Event: voiceStateUpdate
// Déclenché quand un membre change d'état vocal
// (rejoint, quitte, change de channel, mute, etc.)
//
// Actions (si modules activés) :
// 1. [tempvoice] Créer/supprimer des salons vocaux temporaires
// 2. [xp/stats]  Tracker les sessions vocales (durée)
// 3. [logs]      Log les mouvements vocaux
// ===================================

const { ChannelType, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const configService = require('../core/configService');
const { getDb } = require('../database');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('VoiceState');

module.exports = {
  name: 'voiceStateUpdate',
  once: false,

  /**
   * @param {import('discord.js').VoiceState} oldState
   * @param {import('discord.js').VoiceState} newState
   */
  async execute(oldState, newState) {
    const guild = newState.guild || oldState.guild;
    if (!guild) return;

    const member = newState.member || oldState.member;
    if (!member || member.user.bot) return;

    const guildId = guild.id;
    const oldChannel = oldState.channel;
    const newChannel = newState.channel;

    // Même channel → c'est un mute/deafen, pas un mouvement
    if (oldChannel?.id === newChannel?.id) return;

    const config = await configService.get(guildId);

    // =======================================
    // 1. Temp Voice (module: tempvoice)
    // =======================================
    if (await configService.isModuleEnabled(guildId, 'tempvoice')) {
      // Rejoint le lobby → créer un salon temp
      if (newChannel && newChannel.id === config.tempVoiceLobby) {
        await createTempVoice(member, newChannel, config);
      }

      // Quitte un channel → vérifier si c'est un temp voice vide
      if (oldChannel) {
        await cleanupTempVoice(oldChannel);
      }
    }

    // =======================================
    // 2. Voice Sessions (module: xp ou stats)
    // =======================================
    const xpEnabled = await configService.isModuleEnabled(guildId, 'xp');
    const statsEnabled = await configService.isModuleEnabled(guildId, 'stats');

    if (xpEnabled || statsEnabled) {
      // Quitte un channel → fermer la session
      if (oldChannel && !newChannel) {
        await closeVoiceSession(guildId, member.id, oldChannel.id);
      }
      // Rejoint un channel → ouvrir une session
      else if (!oldChannel && newChannel) {
        await openVoiceSession(guildId, member.id, newChannel.id);
      }
      // Change de channel → fermer l'ancienne + ouvrir la nouvelle
      else if (oldChannel && newChannel && oldChannel.id !== newChannel.id) {
        await closeVoiceSession(guildId, member.id, oldChannel.id);
        await openVoiceSession(guildId, member.id, newChannel.id);
      }
    }

    // =======================================
    // 3. Logs (module: logs)
    // =======================================
    if (await configService.isModuleEnabled(guildId, 'logs')) {
      await logVoiceMove(member, oldChannel, newChannel, config);
    }
  },
};

// ===================================
// Temp Voice
// ===================================

/**
 * Crée un salon vocal temporaire quand un membre rejoint le lobby
 */
async function createTempVoice(member, lobbyChannel, config) {
  const guild = member.guild;
  const categoryId = config.tempVoiceCategory || lobbyChannel.parentId;

  try {
    // Vérifier si le membre a déjà un temp voice
    const db = getDb();
    const existing = await db('temp_voice_channels')
      .where('guild_id', guild.id)
      .where('owner_id', member.id)
      .first();

    if (existing) {
      // Déplacer vers son salon existant
      const existingChannel = guild.channels.cache.get(existing.channel_id);
      if (existingChannel) {
        await member.voice.setChannel(existingChannel).catch(() => {});
        return;
      }
      // Le channel n'existe plus → nettoyer
      await db('temp_voice_channels').where('id', existing.id).del();
    }

    // Créer le nouveau salon
    const tempChannel = await guild.channels.create({
      name: `🔊 ${member.user.username}`,
      type: ChannelType.GuildVoice,
      parent: categoryId,
      permissionOverwrites: [
        {
          id: member.id,
          allow: [
            PermissionFlagsBits.ManageChannels,
            PermissionFlagsBits.MoveMembers,
            PermissionFlagsBits.MuteMembers,
            PermissionFlagsBits.DeafenMembers,
          ],
        },
      ],
    });

    // Déplacer le membre dans le nouveau salon
    await member.voice.setChannel(tempChannel).catch(() => {});

    // Sauvegarder en DB
    await db('temp_voice_channels').insert({
      guild_id: guild.id,
      channel_id: tempChannel.id,
      owner_id: member.id,
    });

    log.debug(`TempVoice créé : ${tempChannel.name} par ${member.user.tag} dans ${guild.name}`);
  } catch (err) {
    log.error(`Erreur création tempVoice dans ${guild.name}: ${err.message}`);
  }
}

/**
 * Supprime un salon vocal temporaire s'il est vide
 */
async function cleanupTempVoice(channel) {
  try {
    const db = getDb();
    const tempVoice = await db('temp_voice_channels')
      .where('channel_id', channel.id)
      .first();

    if (!tempVoice) return; // Pas un temp voice

    // Vérifier si le channel est vide
    if (channel.members.size === 0) {
      await channel.delete('Salon vocal temporaire vide').catch(() => {});
      await db('temp_voice_channels').where('id', tempVoice.id).del();
      log.debug(`TempVoice supprimé : ${channel.name} (vide)`);
    }
  } catch (err) {
    log.error(`Erreur cleanup tempVoice: ${err.message}`);
  }
}

// ===================================
// Voice Sessions (tracking durée)
// ===================================

/**
 * Ouvre une session vocale pour un membre
 */
async function openVoiceSession(guildId, userId, channelId) {
  try {
    const db = getDb();
    await db('voice_sessions').insert({
      guild_id: guildId,
      user_id: userId,
      channel_id: channelId,
      joined_at: new Date(),
    });
  } catch {
    // Non critique
  }
}

/**
 * Ferme une session vocale et calcule la durée
 */
async function closeVoiceSession(guildId, userId, channelId) {
  try {
    const db = getDb();
    const now = new Date();

    // Trouver la session ouverte
    const session = await db('voice_sessions')
      .where('guild_id', guildId)
      .where('user_id', userId)
      .where('channel_id', channelId)
      .whereNull('left_at')
      .orderBy('joined_at', 'desc')
      .first();

    if (!session) return;

    // Calculer la durée en secondes
    const joinedAt = new Date(session.joined_at);
    const durationSec = Math.floor((now - joinedAt) / 1000);

    // Mettre à jour la session
    await db('voice_sessions')
      .where('id', session.id)
      .update({
        left_at: now,
        duration: durationSec,
      });

    // Ajouter les minutes au profil utilisateur
    const durationMin = Math.floor(durationSec / 60);
    if (durationMin > 0) {
      await db('users')
        .where('guild_id', guildId)
        .where('user_id', userId)
        .increment('voice_minutes', durationMin);

      // Métriques du jour
      const today = now.toISOString().slice(0, 10);
      await db('daily_metrics')
        .where('guild_id', guildId)
        .where('date', today)
        .increment('voice_minutes', durationMin);
    }
  } catch {
    // Non critique
  }
}

// ===================================
// Logs
// ===================================

/**
 * Log les mouvements vocaux
 */
async function logVoiceMove(member, oldChannel, newChannel, config) {
  const logChannelId = config.logChannel;
  if (!logChannelId) return;

  const logChannel = member.guild.channels.cache.get(logChannelId);
  if (!logChannel) return;

  try {
    let description;
    let color;

    if (!oldChannel && newChannel) {
      // Rejoint
      description = `🔊 **${member.user.tag}** a rejoint ${newChannel}`;
      color = 0x57F287;
    } else if (oldChannel && !newChannel) {
      // Quitte
      description = `🔇 **${member.user.tag}** a quitté ${oldChannel}`;
      color = 0xED4245;
    } else if (oldChannel && newChannel) {
      // Changement
      description = `🔀 **${member.user.tag}** a changé de ${oldChannel} vers ${newChannel}`;
      color = 0xFEE75C;
    } else {
      return;
    }

    const embed = new EmbedBuilder()
      .setDescription(description)
      .setColor(color)
      .setFooter({ text: `ID: ${member.id}` })
      .setTimestamp();

    await logChannel.send({ embeds: [embed] });
  } catch {
    // Non critique — ne pas polluer les logs pour un log raté
  }
}