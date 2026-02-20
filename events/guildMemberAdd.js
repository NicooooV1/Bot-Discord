// ===================================
// Ultra Suite — Event: guildMemberAdd
// Déclenché quand un membre rejoint un serveur
//
// Actions (si modules activés) :
// 1. [onboarding] Message de bienvenue
// 2. [onboarding] Auto-role
// 3. [security]   Anti-raid (détection de join massif)
// 4. [logs]       Log dans le channel de logs
// 5. [stats]      Incrémenter les métriques du jour
// ===================================

const { EmbedBuilder } = require('discord.js');
const configService = require('../core/configService');
const { t } = require('../core/i18n');
const { getDb } = require('../database');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('MemberAdd');

// Anti-raid : tracking des joins récents par guild
// Map<guildId, { timestamps: number[], alerted: boolean }>
const joinTracker = new Map();

module.exports = {
  name: 'guildMemberAdd',
  once: false,

  /**
   * @param {import('discord.js').GuildMember} member
   */
  async execute(member) {
    const { guild, user } = member;
    const guildId = guild.id;

    // Ignorer les bots (optionnel, configurable)
    if (user.bot) return;

    const config = await configService.get(guildId);

    // =======================================
    // 1. Anti-raid (module: security)
    // =======================================
    if (await configService.isModuleEnabled(guildId, 'security')) {
      await checkAntiRaid(member, config);
    }

    // =======================================
    // 2. Message de bienvenue (module: onboarding)
    // =======================================
    if (await configService.isModuleEnabled(guildId, 'onboarding')) {
      await sendWelcome(member, config);
    }

    // =======================================
    // 3. Auto-role (module: onboarding)
    // =======================================
    if (await configService.isModuleEnabled(guildId, 'onboarding')) {
      await assignWelcomeRole(member, config);
    }

    // =======================================
    // 4. Log (module: logs)
    // =======================================
    if (await configService.isModuleEnabled(guildId, 'logs')) {
      await logMemberJoin(member, config);
    }

    // =======================================
    // 5. Métriques
    // =======================================
    await incrementMetric(guildId, 'new_members');
  },
};

// ===================================
// Fonctions
// ===================================

/**
 * Envoie le message de bienvenue
 */
async function sendWelcome(member, config) {
  const channelId = config.welcomeChannel;
  if (!channelId) return;

  const channel = member.guild.channels.cache.get(channelId);
  if (!channel) return;

  try {
    // Message personnalisé ou par défaut
    const messageTemplate = config.welcomeMessage ||
      await t(member.guild.id, 'welcome.default_message');

    const text = messageTemplate
      .replace(/\{user\}/g, member.toString())
      .replace(/\{username\}/g, member.user.username)
      .replace(/\{tag\}/g, member.user.tag)
      .replace(/\{guild\}/g, member.guild.name)
      .replace(/\{count\}/g, String(member.guild.memberCount));

    const embed = new EmbedBuilder()
      .setDescription(text)
      .setThumbnail(member.user.displayAvatarURL({ size: 256 }))
      .setColor(0x57F287)
      .setTimestamp();

    await channel.send({ embeds: [embed] });
  } catch (err) {
    log.error(`Erreur welcome ${member.guild.name}: ${err.message}`);
  }
}

/**
 * Attribue le rôle de bienvenue automatique
 */
async function assignWelcomeRole(member, config) {
  const roleId = config.welcomeRole;
  if (!roleId) return;

  try {
    const role = member.guild.roles.cache.get(roleId);
    if (!role) {
      log.warn(`WelcomeRole ${roleId} introuvable dans ${member.guild.name}`);
      return;
    }

    // Vérifier que le bot peut attribuer ce rôle
    const botMember = member.guild.members.me;
    if (botMember && role.position >= botMember.roles.highest.position) {
      log.warn(`Impossible d'attribuer le rôle ${role.name} — position trop haute`);
      return;
    }

    await member.roles.add(role, 'Auto-role de bienvenue');
    log.debug(`Auto-role ${role.name} attribué à ${member.user.tag} dans ${member.guild.name}`);
  } catch (err) {
    log.error(`Erreur auto-role ${member.guild.name}: ${err.message}`);
  }
}

/**
 * Détection anti-raid (joins massifs)
 */
async function checkAntiRaid(member, config) {
  const raid = config.antiRaid;
  if (!raid?.enabled) return;

  const guildId = member.guild.id;
  const now = Date.now();
  const windowMs = (raid.joinWindow || 10) * 1000;
  const threshold = raid.joinThreshold || 10;

  // Initialiser le tracker pour cette guild
  if (!joinTracker.has(guildId)) {
    joinTracker.set(guildId, { timestamps: [], alerted: false });
  }

  const tracker = joinTracker.get(guildId);

  // Ajouter le timestamp et nettoyer les anciens
  tracker.timestamps.push(now);
  tracker.timestamps = tracker.timestamps.filter((ts) => now - ts < windowMs);

  // Seuil atteint ?
  if (tracker.timestamps.length >= threshold && !tracker.alerted) {
    tracker.alerted = true;

    log.warn(
      `🚨 ANTI-RAID: ${tracker.timestamps.length} joins en ${raid.joinWindow}s ` +
      `dans ${member.guild.name} (${guildId})`
    );

    // Action configurée
    const action = raid.action || 'kick';
    if (action === 'kick') {
      try {
        await member.kick('Anti-raid : join massif détecté');
      } catch { /* Permissions insuffisantes */ }
    } else if (action === 'ban') {
      try {
        await member.ban({ reason: 'Anti-raid : join massif détecté', deleteMessageSeconds: 0 });
      } catch { /* Permissions insuffisantes */ }
    }

    // Alerter dans le modLogChannel
    const modLogId = config.modLogChannel;
    if (modLogId) {
      const modLog = member.guild.channels.cache.get(modLogId);
      if (modLog) {
        await modLog.send({
          embeds: [{
            title: '🚨 Alerte Anti-Raid',
            description:
              `**${tracker.timestamps.length}** membres ont rejoint en **${raid.joinWindow}s**.\n` +
              `Action appliquée : **${action}**`,
            color: 0xED4245,
            timestamp: new Date().toISOString(),
          }],
        }).catch(() => {});
      }
    }

    // Reset après 30s
    setTimeout(() => {
      const t = joinTracker.get(guildId);
      if (t) t.alerted = false;
    }, 30000);
  }
}

/**
 * Log l'arrivée d'un membre dans le channel de logs
 */
async function logMemberJoin(member, config) {
  const logChannelId = config.logChannel;
  if (!logChannelId) return;

  const logChannel = member.guild.channels.cache.get(logChannelId);
  if (!logChannel) return;

  try {
    const accountAge = Math.floor((Date.now() - member.user.createdTimestamp) / 86400000);
    const isNew = accountAge < 7;

    const embed = new EmbedBuilder()
      .setAuthor({
        name: `${member.user.tag} a rejoint`,
        iconURL: member.user.displayAvatarURL(),
      })
      .addFields(
        { name: 'Utilisateur', value: member.toString(), inline: true },
        { name: 'ID', value: member.id, inline: true },
        { name: 'Compte créé', value: `<t:${Math.floor(member.user.createdTimestamp / 1000)}:R>`, inline: true },
      )
      .setColor(0x57F287)
      .setFooter({ text: `Membres : ${member.guild.memberCount}` })
      .setTimestamp();

    if (isNew) {
      embed.addFields({
        name: '⚠️ Nouveau compte',
        value: `Créé il y a seulement **${accountAge} jour(s)**`,
        inline: false,
      });
    }

    await logChannel.send({ embeds: [embed] });
  } catch (err) {
    log.error(`Erreur log memberAdd ${member.guild.name}: ${err.message}`);
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