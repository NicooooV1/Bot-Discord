// ===================================
// Ultra Suite — Event: guildMemberRemove
// Déclenché quand un membre quitte un serveur
// (départ volontaire, kick, ou ban)
//
// Actions (si modules activés) :
// 1. [onboarding] Message d'au revoir
// 2. [logs]       Log dans le channel de logs
// 3. [stats]      Incrémenter les métriques du jour
// ===================================

const { EmbedBuilder, AuditLogEvent } = require('discord.js');
const configService = require('../core/configService');
const { t } = require('../core/i18n');
const { getDb } = require('../database');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('MemberRemove');

module.exports = {
  name: 'guildMemberRemove',
  once: false,

  /**
   * @param {import('discord.js').GuildMember} member
   */
  async execute(member) {
    const { guild, user } = member;
    const guildId = guild.id;

    // Ignorer les bots
    if (user.bot) return;

    const config = await configService.get(guildId);

    // =======================================
    // 1. Message d'au revoir (module: onboarding)
    // =======================================
    if (await configService.isModuleEnabled(guildId, 'onboarding')) {
      await sendGoodbye(member, config);
    }

    // =======================================
    // 2. Log (module: logs)
    // =======================================
    if (await configService.isModuleEnabled(guildId, 'logs')) {
      await logMemberLeave(member, config);
    }

    // =======================================
    // 3. Métriques
    // =======================================
    await incrementMetric(guildId, 'left_members');
  },
};

// ===================================
// Fonctions
// ===================================

/**
 * Envoie le message d'au revoir
 */
async function sendGoodbye(member, config) {
  const channelId = config.goodbyeChannel;
  if (!channelId) return;

  const channel = member.guild.channels.cache.get(channelId);
  if (!channel) return;

  try {
    const messageTemplate = config.goodbyeMessage ||
      await t(member.guild.id, 'goodbye.default_message');

    const text = messageTemplate
      .replace(/\{user\}/g, member.user.username)
      .replace(/\{username\}/g, member.user.username)
      .replace(/\{tag\}/g, member.user.tag)
      .replace(/\{guild\}/g, member.guild.name)
      .replace(/\{count\}/g, String(member.guild.memberCount));

    const embed = new EmbedBuilder()
      .setDescription(text)
      .setThumbnail(member.user.displayAvatarURL({ size: 256 }))
      .setColor(0xED4245)
      .setTimestamp();

    await channel.send({ embeds: [embed] });
  } catch (err) {
    log.error(`Erreur goodbye ${member.guild.name}: ${err.message}`);
  }
}

/**
 * Log le départ d'un membre avec la raison (kick/ban/volontaire)
 */
async function logMemberLeave(member, config) {
  const logChannelId = config.logChannel;
  if (!logChannelId) return;

  const logChannel = member.guild.channels.cache.get(logChannelId);
  if (!logChannel) return;

  try {
    // Tenter de déterminer si c'est un kick ou un ban via l'audit log
    const leaveReason = await detectLeaveReason(member);

    const embed = new EmbedBuilder()
      .setAuthor({
        name: `${member.user.tag} a quitté`,
        iconURL: member.user.displayAvatarURL(),
      })
      .addFields(
        { name: 'Utilisateur', value: member.toString(), inline: true },
        { name: 'ID', value: member.id, inline: true },
        { name: 'Rejoint le', value: member.joinedAt
          ? `<t:${Math.floor(member.joinedTimestamp / 1000)}:R>`
          : 'Inconnu', inline: true },
      )
      .setColor(0xED4245)
      .setFooter({ text: `Membres : ${member.guild.memberCount}` })
      .setTimestamp();

    // Ajouter la raison du départ si détectée
    if (leaveReason.type !== 'leave') {
      embed.addFields({
        name: 'Type de départ',
        value: leaveReason.type === 'kick'
          ? `👢 Expulsé par ${leaveReason.executor || 'Inconnu'}`
          : `🔨 Banni par ${leaveReason.executor || 'Inconnu'}`,
        inline: false,
      });

      if (leaveReason.reason) {
        embed.addFields({
          name: 'Raison',
          value: leaveReason.reason,
          inline: false,
        });
      }
    }

    // Rôles qu'avait le membre
    const roles = member.roles.cache
      .filter((r) => r.id !== member.guild.id) // Exclure @everyone
      .sort((a, b) => b.position - a.position)
      .map((r) => r.toString())
      .slice(0, 15); // Max 15 rôles affichés

    if (roles.length > 0) {
      embed.addFields({
        name: `Rôles (${roles.length})`,
        value: roles.join(', '),
        inline: false,
      });
    }

    await logChannel.send({ embeds: [embed] });
  } catch (err) {
    log.error(`Erreur log memberRemove ${member.guild.name}: ${err.message}`);
  }
}

/**
 * Tente de détecter si le départ est un kick, ban ou départ volontaire
 * en consultant l'audit log du serveur
 */
async function detectLeaveReason(member) {
  const defaultResult = { type: 'leave', executor: null, reason: null };

  try {
    // Vérifier les permissions avant de lire l'audit log
    const botMember = member.guild.members.me;
    if (!botMember?.permissions?.has('ViewAuditLog')) {
      return defaultResult;
    }

    // Chercher un kick récent (dans les 5 dernières secondes)
    const kickLogs = await member.guild.fetchAuditLogs({
      type: AuditLogEvent.MemberKick,
      limit: 5,
    });

    const kickEntry = kickLogs.entries.find(
      (entry) =>
        entry.target?.id === member.id &&
        Date.now() - entry.createdTimestamp < 5000
    );

    if (kickEntry) {
      return {
        type: 'kick',
        executor: kickEntry.executor?.tag || null,
        reason: kickEntry.reason || null,
      };
    }

    // Chercher un ban récent
    const banLogs = await member.guild.fetchAuditLogs({
      type: AuditLogEvent.MemberBanAdd,
      limit: 5,
    });

    const banEntry = banLogs.entries.find(
      (entry) =>
        entry.target?.id === member.id &&
        Date.now() - entry.createdTimestamp < 5000
    );

    if (banEntry) {
      return {
        type: 'ban',
        executor: banEntry.executor?.tag || null,
        reason: banEntry.reason || null,
      };
    }

    return defaultResult;
  } catch {
    return defaultResult;
  }
}

/**
 * Incrémente une métrique du jour pour une guild
 */
async function incrementMetric(guildId, field) {
  try {
    const db = getDb();
    const today = new Date().toISOString().slice(0, 10);
    await db('daily_metrics')
      .where('guild_id', guildId)
      .where('date', today)
      .increment(field, 1);
  } catch {
    // Non critique
  }
}