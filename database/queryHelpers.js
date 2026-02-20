// ===================================
// Ultra Suite — Query Helpers
// Helpers de requêtes multi-serveur
//
// Fonctions utilitaires réutilisables dans
// toutes les commandes et événements.
// ===================================

const { getDb, transaction } = require('./index');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('QueryHelpers');

// ===================================
// Users
// ===================================

/**
 * Récupère un utilisateur dans une guild, ou le crée.
 *
 * @param {string} guildId - ID du serveur Discord
 * @param {string} userId - ID de l'utilisateur Discord
 * @param {string} [username] - Nom d'utilisateur (pour mise à jour)
 * @returns {Promise<object>}
 */
async function getOrCreateUser(guildId, userId, username = null) {
  const db = getDb();
  let user = await db('users').where({ guild_id: guildId, user_id: userId }).first();

  if (!user) {
    await db('users').insert({
      guild_id: guildId,
      user_id: userId,
      username: username || 'Unknown',
    });
    user = await db('users').where({ guild_id: guildId, user_id: userId }).first();
  } else if (username && user.username !== username) {
    await db('users').where({ guild_id: guildId, user_id: userId }).update({ username });
    user.username = username;
  }

  return user;
}

/**
 * Met à jour un champ numérique d'un utilisateur (increment/decrement).
 *
 * @param {string} guildId
 * @param {string} userId
 * @param {string} field - Nom de la colonne (ex: 'xp', 'balance')
 * @param {number} amount - Montant (positif ou négatif)
 * @returns {Promise<number>} Nouvelle valeur
 */
async function updateUserField(guildId, userId, field, amount) {
  const db = getDb();

  if (amount >= 0) {
    await db('users')
      .where({ guild_id: guildId, user_id: userId })
      .increment(field, amount);
  } else {
    await db('users')
      .where({ guild_id: guildId, user_id: userId })
      .decrement(field, Math.abs(amount));
  }

  const user = await db('users')
    .where({ guild_id: guildId, user_id: userId })
    .select(field)
    .first();

  return user?.[field] ?? 0;
}

// ===================================
// Sanctions
// ===================================

/**
 * Crée une sanction et son log en une seule transaction.
 *
 * @param {object} sanctionData - Données de la sanction
 * @param {object} [logData] - Données du log (optionnel)
 * @returns {Promise<object>} La sanction créée
 */
async function createSanction(sanctionData, logData = null) {
  return transaction(async (trx) => {
    // Déterminer le prochain case_id pour cette guild
    const lastCase = await trx('sanctions')
      .where('guild_id', sanctionData.guild_id)
      .max('case_id as max_case')
      .first();

    const caseId = (lastCase?.max_case || 0) + 1;

    const [id] = await trx('sanctions').insert({
      ...sanctionData,
      case_id: caseId,
    });

    // Créer le log si fourni
    if (logData) {
      await trx('logs').insert({
        guild_id: sanctionData.guild_id,
        type: sanctionData.type,
        actor_id: sanctionData.moderator_id,
        target_id: sanctionData.target_id,
        details: JSON.stringify(logData),
      });
    }

    return {
      id,
      case_id: caseId,
      ...sanctionData,
    };
  });
}

/**
 * Récupère les sanctions actives d'un utilisateur.
 *
 * @param {string} guildId
 * @param {string} userId
 * @param {string} [type] - Filtrer par type (BAN, WARN, etc.)
 * @returns {Promise<object[]>}
 */
async function getActiveSanctions(guildId, userId, type = null) {
  const db = getDb();
  let query = db('sanctions')
    .where({ guild_id: guildId, target_id: userId })
    .whereNull('revoked_at');

  if (type) {
    query = query.where('type', type);
  }

  return query.orderBy('created_at', 'desc');
}

/**
 * Compte les sanctions d'un utilisateur par type.
 *
 * @param {string} guildId
 * @param {string} userId
 * @returns {Promise<object>} { WARN: 3, BAN: 1, ... }
 */
async function countSanctionsByType(guildId, userId) {
  const db = getDb();
  const rows = await db('sanctions')
    .where({ guild_id: guildId, target_id: userId })
    .whereNull('revoked_at')
    .groupBy('type')
    .select('type')
    .count('* as cnt');

  const result = {};
  for (const row of rows) {
    result[row.type] = row.cnt;
  }
  return result;
}

// ===================================
// Leaderboards
// ===================================

/**
 * Récupère un leaderboard paginé pour une guild.
 *
 * @param {string} guildId
 * @param {string} field - Champ de tri (ex: 'xp', 'balance', 'level')
 * @param {object} [options]
 * @param {number} [options.page=1]
 * @param {number} [options.perPage=10]
 * @param {number} [options.minValue=0] - Valeur minimum pour apparaître
 * @returns {Promise<{ entries: object[], total: number, page: number, totalPages: number }>}
 */
async function getLeaderboard(guildId, field, options = {}) {
  const { page = 1, perPage = 10, minValue = 0 } = options;
  const db = getDb();

  const offset = (page - 1) * perPage;

  const [{ cnt }] = await db('users')
    .where('guild_id', guildId)
    .where(field, '>', minValue)
    .count('* as cnt');

  const entries = await db('users')
    .where('guild_id', guildId)
    .where(field, '>', minValue)
    .orderBy(field, 'desc')
    .select('user_id', 'username', field)
    .limit(perPage)
    .offset(offset);

  // Ajouter le rang
  const rankedEntries = entries.map((entry, index) => ({
    ...entry,
    rank: offset + index + 1,
  }));

  return {
    entries: rankedEntries,
    total: cnt,
    page,
    totalPages: Math.ceil(cnt / perPage),
  };
}

/**
 * Récupère le rang d'un utilisateur dans un leaderboard.
 *
 * @param {string} guildId
 * @param {string} userId
 * @param {string} field
 * @returns {Promise<number|null>} Rang (1-based) ou null si non trouvé
 */
async function getUserRank(guildId, userId, field) {
  const db = getDb();

  const user = await db('users')
    .where({ guild_id: guildId, user_id: userId })
    .select(field)
    .first();

  if (!user) return null;

  const [{ cnt }] = await db('users')
    .where('guild_id', guildId)
    .where(field, '>', user[field])
    .count('* as cnt');

  return cnt + 1;
}

// ===================================
// Logs
// ===================================

/**
 * Crée une entrée de log.
 *
 * @param {object} data
 * @param {string} data.guild_id
 * @param {string} data.type
 * @param {string} [data.actor_id]
 * @param {string} [data.target_id]
 * @param {string} [data.target_type]
 * @param {object} [data.details]
 * @returns {Promise<number>} ID du log
 */
async function createLog(data) {
  const db = getDb();
  const [id] = await db('logs').insert({
    guild_id: data.guild_id,
    type: data.type,
    actor_id: data.actor_id || null,
    target_id: data.target_id || null,
    target_type: data.target_type || null,
    details: data.details ? JSON.stringify(data.details) : null,
  });
  return id;
}

/**
 * Récupère les logs récents d'une guild.
 *
 * @param {string} guildId
 * @param {object} [options]
 * @param {string} [options.type] - Filtrer par type
 * @param {string} [options.actorId] - Filtrer par acteur
 * @param {string} [options.targetId] - Filtrer par cible
 * @param {number} [options.limit=50]
 * @returns {Promise<object[]>}
 */
async function getRecentLogs(guildId, options = {}) {
  const { type, actorId, targetId, limit = 50 } = options;
  const db = getDb();

  let query = db('logs').where('guild_id', guildId);

  if (type) query = query.where('type', type);
  if (actorId) query = query.where('actor_id', actorId);
  if (targetId) query = query.where('target_id', targetId);

  const logs = await query.orderBy('created_at', 'desc').limit(limit);

  // Parser les details JSON
  return logs.map((l) => ({
    ...l,
    details: l.details ? JSON.parse(l.details) : null,
  }));
}

// ===================================
// Nettoyage & Maintenance
// ===================================

/**
 * Supprime les anciens logs (> X jours).
 *
 * @param {number} [days=90] - Nombre de jours à conserver
 * @returns {Promise<number>} Nombre de logs supprimés
 */
async function cleanOldLogs(days = 90) {
  const db = getDb();
  const hasTable = await db.schema.hasTable('logs');
  if (!hasTable) return 0;

  const cutoff = new Date(Date.now() - days * 24 * 60 * 60 * 1000);
  const deleted = await db('logs').where('created_at', '<', cutoff).del();

  if (deleted > 0) {
    log.info(`${deleted} log(s) de +${days}j supprimé(s)`);
  }

  return deleted;
}

/**
 * Supprime les rappels déjà envoyés (> 7 jours).
 *
 * @returns {Promise<number>}
 */
async function cleanFiredReminders() {
  const db = getDb();
  const hasTable = await db.schema.hasTable('reminders');
  if (!hasTable) return 0;

  const cutoff = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
  const deleted = await db('reminders')
    .where('fired', true)
    .where('fire_at', '<', cutoff)
    .del();

  return deleted;
}

/**
 * Supprime les métriques quotidiennes anciennes (> X jours).
 *
 * @param {number} [days=365]
 * @returns {Promise<number>}
 */
async function cleanOldMetrics(days = 365) {
  const db = getDb();
  const hasTable = await db.schema.hasTable('daily_metrics');
  if (!hasTable) return 0;

  const cutoff = new Date(Date.now() - days * 24 * 60 * 60 * 1000);
  return db('daily_metrics').where('date', '<', cutoff).del();
}

// ===================================
// Exports
// ===================================

module.exports = {
  // Users
  getOrCreateUser,
  updateUserField,

  // Sanctions
  createSanction,
  getActiveSanctions,
  countSanctionsByType,

  // Leaderboards
  getLeaderboard,
  getUserRank,

  // Logs
  createLog,
  getRecentLogs,

  // Maintenance
  cleanOldLogs,
  cleanFiredReminders,
  cleanOldMetrics,
};
