const { EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getGuildConfig, addModLog, addWarn } = require('./database');
const { modLog, COLORS } = require('./logger');

// ===================================
// Configuration anti-spam
// ===================================
const SPAM_CONFIG = {
  MESSAGE_LIMIT: 5,         // Nombre de messages max
  TIME_WINDOW: 5000,        // En millisecondes (5 secondes)
  MUTE_DURATION: 300_000,   // 5 minutes en ms
  DUPLICATE_LIMIT: 3,       // Messages identiques max
  DUPLICATE_WINDOW: 10000,  // En 10 secondes
  MENTION_LIMIT: 5,         // Mentions max par message
  CAPS_PERCENT: 70,         // % de majuscules max (messages > 10 chars)
  CAPS_MIN_LENGTH: 15,      // Longueur min pour vérifier les majuscules
};

// Cache pour tracker les messages
const messageCache = new Map(); // userId -> [{ content, timestamp }]

/**
 * Nettoie les anciennes entrées du cache
 */
function cleanCache(userId) {
  const now = Date.now();
  const userMessages = messageCache.get(userId);
  if (!userMessages) return;

  const filtered = userMessages.filter(m => now - m.timestamp < SPAM_CONFIG.TIME_WINDOW * 2);
  if (filtered.length === 0) {
    messageCache.delete(userId);
  } else {
    messageCache.set(userId, filtered);
  }
}

/**
 * Vérifier et traiter le spam
 * Retourne true si le message est du spam
 */
async function checkSpam(message) {
  // Ignorer les bots, admins, et DMs
  if (!message.guild) return false;
  if (message.author.bot) return false;
  if (message.member?.permissions.has(PermissionFlagsBits.ManageMessages)) return false;

  const userId = message.author.id;
  const now = Date.now();

  // Initialiser le cache utilisateur
  if (!messageCache.has(userId)) {
    messageCache.set(userId, []);
  }

  const userMessages = messageCache.get(userId);
  userMessages.push({
    content: message.content,
    timestamp: now,
    channelId: message.channel.id,
  });

  // Nettoyer les anciennes entrées
  cleanCache(userId);

  let spamReason = null;

  // ===================================
  // 1. Spam de messages (trop rapide)
  // ===================================
  const recentMessages = userMessages.filter(m => now - m.timestamp < SPAM_CONFIG.TIME_WINDOW);
  if (recentMessages.length >= SPAM_CONFIG.MESSAGE_LIMIT) {
    spamReason = `Envoi de ${recentMessages.length} messages en ${SPAM_CONFIG.TIME_WINDOW / 1000}s`;
  }

  // ===================================
  // 2. Messages dupliqués
  // ===================================
  if (!spamReason) {
    const recentDuplicates = userMessages.filter(
      m => now - m.timestamp < SPAM_CONFIG.DUPLICATE_WINDOW && m.content === message.content
    );
    if (recentDuplicates.length >= SPAM_CONFIG.DUPLICATE_LIMIT && message.content.length > 0) {
      spamReason = `${recentDuplicates.length} messages identiques envoyés`;
    }
  }

  // ===================================
  // 3. Mention spam
  // ===================================
  if (!spamReason) {
    const mentionCount = message.mentions.users.size + message.mentions.roles.size;
    if (mentionCount >= SPAM_CONFIG.MENTION_LIMIT) {
      spamReason = `${mentionCount} mentions dans un seul message`;
    }
    if (message.mentions.everyone) {
      spamReason = 'Tentative de mention @everyone';
    }
  }

  // ===================================
  // 4. Excès de majuscules
  // ===================================
  if (!spamReason && message.content.length >= SPAM_CONFIG.CAPS_MIN_LENGTH) {
    const letters = message.content.replace(/[^a-zA-ZÀ-ÿ]/g, '');
    if (letters.length > 0) {
      const uppercase = letters.replace(/[^A-ZÀ-Ý]/g, '').length;
      const capsPercent = (uppercase / letters.length) * 100;
      if (capsPercent >= SPAM_CONFIG.CAPS_PERCENT) {
        spamReason = `${Math.round(capsPercent)}% de majuscules`;
      }
    }
  }

  // ===================================
  // Appliquer la sanction si spam détecté
  // ===================================
  if (spamReason) {
    await handleSpam(message, spamReason);
    return true;
  }

  return false;
}

/**
 * Sanctionner le spam
 */
async function handleSpam(message, reason) {
  const fullReason = `[AUTO-MOD] ${reason}`;

  try {
    // Supprimer le message
    await message.delete().catch(() => {});

    // Supprimer les messages récents du spammeur dans le salon
    try {
      const messages = await message.channel.messages.fetch({ limit: 50 });
      const spamMessages = messages.filter(
        m => m.author.id === message.author.id &&
        Date.now() - m.createdTimestamp < 10000
      );
      if (spamMessages.size > 1) {
        await message.channel.bulkDelete(spamMessages, true).catch(() => {});
      }
    } catch { /* Erreur lors de la suppression en masse */ }

    // Mute l'utilisateur
    if (message.member && message.member.moderatable) {
      await message.member.timeout(SPAM_CONFIG.MUTE_DURATION, fullReason);
    }

    // Ajouter un warn
    addWarn(message.guild.id, message.author.id, message.client.user.id, fullReason);

    // Log en base
    addModLog(
      message.guild.id, 'AUTO-MUTE', message.author.id,
      message.client.user.id, fullReason, '5m'
    );

    // Log dans le salon de logs
    await modLog(message.guild, {
      action: 'Auto-Modération (Spam)',
      moderator: message.client.user,
      target: message.author,
      reason: fullReason,
      duration: '5 minutes',
      color: COLORS.PURPLE,
    });

    // Avertissement dans le salon
    const embed = new EmbedBuilder()
      .setTitle('🛡️ Auto-Modération')
      .setColor(COLORS.PURPLE)
      .setDescription(`${message.author} a été muté pour **5 minutes**.`)
      .addFields({ name: '📝 Raison', value: reason })
      .setTimestamp();

    const warning = await message.channel.send({ embeds: [embed] });

    // Supprimer l'avertissement après 10 secondes
    setTimeout(() => warning.delete().catch(() => {}), 10_000);

  } catch (error) {
    console.error('[ANTISPAM]', error);
  }
}

/**
 * Nettoyer le cache périodiquement (toutes les 5 minutes)
 */
setInterval(() => {
  const now = Date.now();
  for (const [userId, messages] of messageCache) {
    const filtered = messages.filter(m => now - m.timestamp < 60_000);
    if (filtered.length === 0) {
      messageCache.delete(userId);
    } else {
      messageCache.set(userId, filtered);
    }
  }
}, 300_000);

module.exports = { checkSpam, SPAM_CONFIG };
