// ===================================
// Ultra Suite — Event: messageCreate
// Déclenché à chaque nouveau message
//
// Actions (si modules activés) :
// 1. [xp]       Gain d'XP + level up
// 2. [security] Automod (spam, links, mentions, mots interdits)
// 3. [stats]    Compteur de messages
// ===================================

const configService = require('../core/configService');
const { t } = require('../core/i18n');
const { getDb } = require('../database');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('MessageCreate');

// Anti-spam : tracking par guild:user
const spamTracker = new Map();

module.exports = {
  name: 'messageCreate',
  once: false,

  async execute(message) {
    if (!message.guild) return;
    if (message.author.bot) return;

    const guildId = message.guild.id;
    const userId = message.author.id;
    const config = await configService.get(guildId);

    // 1. Automod — AVANT tout
    if (await configService.isModuleEnabled(guildId, 'security')) {
      const blocked = await runAutomod(message, config);
      if (blocked) return;
    }

    // 2. XP
    if (await configService.isModuleEnabled(guildId, 'xp')) {
      await processXp(message, config);
    }

    // 3. Stats
    await incrementMessageStats(guildId, userId);
  },
};

// ===================================
// Automod
// ===================================
async function runAutomod(message, config) {
  if (!config.automod?.enabled) return false;
  const member = message.member;
  if (!member) return false;
  if (member.permissions.has('ManageMessages')) return false;

  const content = message.content;
  const guildId = message.guild.id;

  // Anti-Spam (5 msg / 5s)
  if (config.automod.antiSpam) {
    const key = `${guildId}:${message.author.id}`;
    const now = Date.now();
    if (!spamTracker.has(key)) spamTracker.set(key, { timestamps: [], warned: false });
    const tracker = spamTracker.get(key);
    tracker.timestamps.push(now);
    tracker.timestamps = tracker.timestamps.filter((ts) => now - ts < 5000);

    if (tracker.timestamps.length >= 5 && !tracker.warned) {
      tracker.warned = true;
      await message.delete().catch(() => {});
      await message.channel.send({
        content: `⚠️ ${message.author}, merci de ne pas spammer.`,
      }).then((msg) => setTimeout(() => msg.delete().catch(() => {}), 5000)).catch(() => {});
      await saveSecuritySignal(guildId, message.author.id, 'SPAM', 'low');
      setTimeout(() => { const t = spamTracker.get(key); if (t) t.warned = false; }, 10000);
      return true;
    }
  }

  // Anti-Link
  if (config.automod.antiLink) {
    const urlRegex = /https?:\/\/[^\s]+/gi;
    if (urlRegex.test(content)) {
      const db = getDb();
      const domainFilters = await db('automod_filters')
        .where('guild_id', guildId).where('type', 'domain').where('enabled', true);
      const shouldBlock = domainFilters.length === 0 ||
        domainFilters.some((f) => content.toLowerCase().includes(f.pattern.toLowerCase()));
      if (shouldBlock) {
        await message.delete().catch(() => {});
        await message.channel.send({
          content: `⚠️ ${message.author}, les liens ne sont pas autorisés ici.`,
        }).then((msg) => setTimeout(() => msg.delete().catch(() => {}), 5000)).catch(() => {});
        return true;
      }
    }
  }

  // Anti-Mention Mass (5+)
  if (config.automod.antiMention) {
    const mentionCount = (content.match(/<@!?\d+>/g) || []).length;
    if (mentionCount >= 5) {
      await message.delete().catch(() => {});
      await message.channel.send({
        content: `⚠️ ${message.author}, trop de mentions dans votre message.`,
      }).then((msg) => setTimeout(() => msg.delete().catch(() => {}), 5000)).catch(() => {});
      await saveSecuritySignal(guildId, message.author.id, 'MASS_MENTION', 'medium');
      return true;
    }
  }

  // Filtres personnalisés (mots, regex)
  const db = getDb();
  const filters = await db('automod_filters')
    .where('guild_id', guildId).whereIn('type', ['word', 'regex']).where('enabled', true);
  for (const filter of filters) {
    let matches = false;
    if (filter.type === 'word') matches = content.toLowerCase().includes(filter.pattern.toLowerCase());
    else if (filter.type === 'regex') {
      try { matches = new RegExp(filter.pattern, 'i').test(content); } catch { /* regex invalide */ }
    }
    if (matches) {
      if (filter.action === 'delete' || filter.action === 'warn') await message.delete().catch(() => {});
      return true;
    }
  }
  return false;
}

// ===================================
// XP System
// ===================================
async function processXp(message, config) {
  if (!config.xp?.enabled) return;
  const guildId = message.guild.id;
  const userId = message.author.id;
  const db = getDb();

  try {
    const user = await db('users').where('guild_id', guildId).where('user_id', userId).first();
    const now = new Date();
    const cooldownSec = config.xp.cooldown || 60;

    if (user?.last_message_xp) {
      const elapsed = (now - new Date(user.last_message_xp)) / 1000;
      if (elapsed < cooldownSec) return;
    }

    const min = config.xp.min || 15;
    const max = config.xp.max || 25;
    const xpGain = Math.floor(Math.random() * (max - min + 1)) + min;

    if (!user) {
      await db('users').insert({
        guild_id: guildId, user_id: userId, xp: xpGain,
        level: 0, last_message_xp: now, total_messages: 1,
      });
    } else {
      const newXp = (user.xp || 0) + xpGain;
      const currentLevel = user.level || 0;
      const newLevel = Math.floor(0.1 * Math.sqrt(newXp));

      await db('users').where('guild_id', guildId).where('user_id', userId).update({
        xp: newXp, level: newLevel, last_message_xp: now,
      });

      if (newLevel > currentLevel) await handleLevelUp(message, newLevel, config);
    }
  } catch (err) {
    log.error(`Erreur XP pour ${userId} dans ${guildId}: ${err.message}`);
  }
}

async function handleLevelUp(message, newLevel, config) {
  try {
    const text = await t(message.guild.id, 'xp.level_up', {
      user: message.author.toString(), level: String(newLevel),
    });
    const ch = config.xp.levelUpChannel
      ? message.guild.channels.cache.get(config.xp.levelUpChannel)
      : message.channel;
    if (ch) await ch.send({ content: text }).catch(() => {});

    const rewardRoleId = config.xp.roleRewards?.[String(newLevel)];
    if (rewardRoleId && message.member) {
      const role = message.guild.roles.cache.get(rewardRoleId);
      if (role) await message.member.roles.add(role, `Récompense niveau ${newLevel}`).catch(() => {});
    }
  } catch { /* Non critique */ }
}

// ===================================
// Stats
// ===================================
async function incrementMessageStats(guildId, userId) {
  try {
    const db = getDb();
    await db('users').where('guild_id', guildId).where('user_id', userId).increment('total_messages', 1);
    const today = new Date().toISOString().slice(0, 10);
    await db('daily_metrics').where('guild_id', guildId).where('date', today).increment('messages', 1);
  } catch { /* Non critique */ }
}

async function saveSecuritySignal(guildId, userId, type, severity) {
  try {
    const db = getDb();
    await db('security_signals').insert({
      guild_id: guildId, user_id: userId, signal_type: type,
      severity, evidence: JSON.stringify({ detected_at: new Date().toISOString() }),
      action_taken: 'delete',
    });
  } catch { /* Non critique */ }
}