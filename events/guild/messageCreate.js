// ===================================
// Ultra Suite — Event: messageCreate
// Anti-spam, anti-link, XP messages
// ===================================

const NodeCache = require('node-cache');
const configService = require('../../core/configService');
const userQueries = require('../../database/userQueries');
const logQueries = require('../../database/logQueries');
const { getDb } = require('../../database');
const { errorEmbed } = require('../../utils/embeds');
const { xpToLevel } = require('../../utils/formatters');
const { t } = require('../../core/i18n');
const eventBus = require('../../core/eventBus');

// Anti-spam : messages par user
const spamMap = new Map(); // userId -> { count, firstTimestamp }
const SPAM_INTERVAL = 5000; // 5 secondes
const SPAM_THRESHOLD = 5; // 5 messages en 5s

// XP cooldown
const xpCooldowns = new NodeCache({ stdTTL: 60, checkperiod: 30 });

module.exports = {
  name: 'messageCreate',

  async execute(message, client) {
    if (!message.guild || message.author.bot) return;

    const config = await configService.get(message.guild.id);

    // ===================================
    // SÉCURITÉ : Anti-spam
    // ===================================
    const securityEnabled = await configService.isModuleEnabled(message.guild.id, 'security');
    if (securityEnabled && config.automod?.antiSpam) {
      const now = Date.now();
      const key = `${message.guild.id}:${message.author.id}`;
      const data = spamMap.get(key) || { count: 0, first: now };

      if (now - data.first > SPAM_INTERVAL) {
        data.count = 1;
        data.first = now;
      } else {
        data.count++;
      }

      spamMap.set(key, data);

      if (data.count >= SPAM_THRESHOLD) {
        spamMap.delete(key);

        // Timeout 5 minutes
        const member = message.member;
        if (member && member.moderatable) {
          await member.timeout(5 * 60 * 1000, 'Anti-spam automatique').catch(() => {});
        }

        // Supprimer les messages récents
        try {
          const recent = await message.channel.messages.fetch({ limit: 10 });
          const userMsgs = recent.filter(
            (m) => m.author.id === message.author.id && now - m.createdTimestamp < SPAM_INTERVAL
          );
          await message.channel.bulkDelete(userMsgs).catch(() => {});
        } catch {}

        await logQueries.create({
          guildId: message.guild.id,
          type: 'AUTOMOD',
          actorId: client.user.id,
          targetId: message.author.id,
          targetType: 'user',
          details: { trigger: 'SPAM', action: 'TIMEOUT', messages: data.count },
        });

        eventBus.dispatch('security:spam', {
          guildId: message.guild.id,
          userId: message.author.id,
          count: data.count,
        });
      }
    }

    // ===================================
    // SÉCURITÉ : Anti-link
    // ===================================
    if (securityEnabled && config.automod?.antiLink) {
      const urlRegex = /https?:\/\/[^\s]+/gi;
      if (urlRegex.test(message.content)) {
        // Ignorer les mods
        if (!message.member.permissions.has('ManageMessages')) {
          await message.delete().catch(() => {});
          await message.channel
            .send({ embeds: [errorEmbed(`${message.author}, les liens ne sont pas autorisés ici.`)] })
            .then((m) => setTimeout(() => m.delete().catch(() => {}), 5000))
            .catch(() => {});

          return; // Pas de XP pour les messages supprimés
        }
      }
    }

    // ===================================
    // SÉCURITÉ : Anti mass-mention
    // ===================================
    if (securityEnabled && config.automod?.antiMention) {
      if (message.mentions.users.size >= 5 || message.mentions.roles.size >= 3) {
        if (!message.member.permissions.has('ManageMessages')) {
          await message.delete().catch(() => {});
          const member = message.member;
          if (member && member.moderatable) {
            await member.timeout(10 * 60 * 1000, 'Anti mass-mention').catch(() => {});
          }

          await logQueries.create({
            guildId: message.guild.id,
            type: 'AUTOMOD',
            actorId: client.user.id,
            targetId: message.author.id,
            targetType: 'user',
            details: {
              trigger: 'MASS_MENTION',
              action: 'TIMEOUT',
              userMentions: message.mentions.users.size,
              roleMentions: message.mentions.roles.size,
            },
          });

          return;
        }
      }
    }

    // ===================================
    // XP : Gain de XP par message
    // ===================================
    const xpEnabled = await configService.isModuleEnabled(message.guild.id, 'xp');
    if (xpEnabled && config.xp?.enabled) {
      const cooldownKey = `xp:${message.guild.id}:${message.author.id}`;
      if (!xpCooldowns.get(cooldownKey)) {
        const xpGain = Math.floor(Math.random() * (config.xp.max - config.xp.min + 1)) + config.xp.min;

        await userQueries.getOrCreate(message.author.id, message.guild.id);
        await userQueries.increment(message.author.id, message.guild.id, 'xp', xpGain);
        await userQueries.increment(message.author.id, message.guild.id, 'total_messages', 1);

        // Vérifier level up
        const user = await userQueries.getOrCreate(message.author.id, message.guild.id);
        const newLevel = xpToLevel(user.xp);

        if (newLevel > user.level) {
          await userQueries.update(message.author.id, message.guild.id, { level: newLevel });

          // Notification level up
          const levelUpChannel = config.xp.levelUpChannel
            ? message.guild.channels.cache.get(config.xp.levelUpChannel)
            : message.channel;

          if (levelUpChannel) {
            const msg = (config.xp.levelUpMessage || t('xp.level_up'))
              .replace(/\{\{user\}\}/g, message.author.toString())
              .replace(/\{\{level\}\}/g, newLevel);
            levelUpChannel.send(msg).catch(() => {});
          }

          // Rôle reward
          if (config.xp.roleRewards?.[newLevel]) {
            const roleId = config.xp.roleRewards[newLevel];
            message.member.roles.add(roleId, `XP Level ${newLevel}`).catch(() => {});
          }

          eventBus.dispatch('xp:levelUp', {
            guildId: message.guild.id,
            userId: message.author.id,
            level: newLevel,
          });
        }

        xpCooldowns.set(cooldownKey, true, config.xp.cooldown || 60);
      }
    }

    // === Incrémenter daily_metrics.messages ===
    try {
      const db = getDb();
      const today = new Date().toISOString().split('T')[0];
      await db('daily_metrics')
        .where({ guild_id: message.guild.id, date: today })
        .increment('messages', 1)
        .catch(() => {
          // Créer la row si inexistante
          db('daily_metrics').insert({ guild_id: message.guild.id, date: today, messages: 1 }).catch(() => {});
        });
    } catch {}
  },
};
