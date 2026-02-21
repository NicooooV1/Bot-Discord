// ===================================
// Ultra Suite — Database Manager
// PostgreSQL 16 + Knex — Multi-serveur
//
// Single pool de connexions partagé entre
// tous les serveurs (guild_id sépare les données).
//
// Features :
//   - Retry exponentiel à l'init
//   - Health monitoring périodique
//   - Auto-reconnexion sur perte de connexion
//   - Transaction helper
//   - Query builder helpers multi-guild
//   - Migration sécurisée avec lock cleanup
//   - Métriques détaillées
// ===================================

const knex = require('knex');
const knexConfig = require('./knexfile');
const path = require('path');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('Database');

let db = null;
let healthMonitorInterval = null;
let isShuttingDown = false;

// ===================================
// Configuration
// ===================================
const CONFIG = {
  // Retry de connexion initiale
  maxRetries: parseInt(process.env.DB_MAX_RETRIES, 10) || 7,
  baseRetryDelay: 2000,   // 2s — doublé à chaque tentative (exponentiel)
  maxRetryDelay: 30000,   // 30s max entre les tentatives

  // Health monitoring
  healthCheckInterval: parseInt(process.env.DB_HEALTH_INTERVAL, 10) || 60000, // 60s
  healthCheckTimeout: 5000, // 5s max pour un ping

  // Requêtes
  defaultPageSize: 25,
  maxPageSize: 100,
};

// ===================================
// Helpers internes
// ===================================

/**
 * Attend un délai en ms
 */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Calcule le délai de retry exponentiel avec jitter
 * @param {number} attempt - Numéro de la tentative (1-based)
 * @returns {number} Délai en ms
 */
function getRetryDelay(attempt) {
  const exponential = CONFIG.baseRetryDelay * Math.pow(2, attempt - 1);
  const jitter = Math.random() * 1000; // 0-1s de jitter
  return Math.min(exponential + jitter, CONFIG.maxRetryDelay);
}

/**
 * Teste la connexion à PostgreSQL avec timeout
 * @param {import('knex').Knex} instance
 * @returns {Promise<boolean>}
 */
async function testConnection(instance) {
  try {
    const timeout = new Promise((_, reject) =>
      setTimeout(() => reject(new Error('Health check timeout')), CONFIG.healthCheckTimeout)
    );
    await Promise.race([instance.raw('SELECT 1 + 1 AS result'), timeout]);
    return true;
  } catch {
    return false;
  }
}

/**
 * Tente de nettoyer les locks de migration orphelins
 * (Peut arriver si le bot crash pendant une migration)
 * @param {import('knex').Knex} instance
 */
async function cleanMigrationLocks(instance) {
  try {
    const lockTable = 'knex_migrations_lock';
    const hasLock = await instance.schema.hasTable(lockTable);
    if (hasLock) {
      const lock = await instance(lockTable).first();
      if (lock?.is_locked) {
        log.warn('Lock de migration orphelin détecté, nettoyage...');
        await instance(lockTable).update({ is_locked: 0 });
        log.info('Lock de migration nettoyé');
      }
    }
  } catch (err) {
    log.warn('Impossible de nettoyer le lock de migration:', err.message);
  }
}

// ===================================
// Initialisation
// ===================================

/**
 * Initialise la connexion PostgreSQL avec retry exponentiel et lance les migrations.
 *
 * En multi-serveur, un seul pool de connexions est utilisé.
 * Les données sont séparées par guild_id dans chaque table.
 *
 * @returns {Promise<import('knex').Knex>}
 */
async function init() {
  if (db) {
    log.warn('Database déjà initialisée, réutilisation du pool existant');
    return db;
  }

  const host = process.env.DB_HOST || '127.0.0.1';
  const port = process.env.DB_PORT || 3306;
  const dbName = process.env.DB_NAME || 'discordbot';

  log.info('Connexion à PostgreSQL...');
  log.info(`  Host : ${host}:${port}`);
  log.info(`  DB   : ${dbName}`);
  log.info(`  User : ${process.env.DB_USER || 'root'}`);
  log.info(`  Pool : min=${knexConfig.pool?.min || 2}, max=${knexConfig.pool?.max || 10}`);

  db = knex(knexConfig);

  // === Tentatives de connexion avec retry exponentiel ===
  let connected = false;
  for (let attempt = 1; attempt <= CONFIG.maxRetries; attempt++) {
    connected = await testConnection(db);

    if (connected) {
      log.info(`Connexion PostgreSQL établie (tentative ${attempt}/${CONFIG.maxRetries})`);
      break;
    }

    if (attempt < CONFIG.maxRetries) {
      const delay = getRetryDelay(attempt);
      log.warn(
        `Connexion échouée (tentative ${attempt}/${CONFIG.maxRetries}), ` +
        `retry dans ${(delay / 1000).toFixed(1)}s...`
      );
      await sleep(delay);
    }
  }

  if (!connected) {
    // Détruire le pool inutilisable
    try { await db.destroy(); } catch { /* ignore */ }
    db = null;
    log.error(`Impossible de se connecter à PostgreSQL après ${CONFIG.maxRetries} tentatives.`);
    log.error('Vérifiez les variables DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME dans .env');
    throw new Error('Database connection failed');
  }

  // === Nettoyer les locks de migration orphelins ===
  await cleanMigrationLocks(db);

  // === Exécuter les migrations en attente ===
  try {
    const [batch, migrations] = await db.migrate.latest();

    if (migrations.length > 0) {
      log.info(`Migrations exécutées (batch ${batch}) :`);
      migrations.forEach((m) => log.info(`  ↳ ${path.basename(m)}`));
    } else {
      log.info('Base de données à jour (aucune migration en attente)');
    }
  } catch (err) {
    log.error('Erreur lors des migrations :', err.message);

    // En cas d'erreur de lock, tenter un nettoyage
    if (err.message.includes('lock')) {
      log.warn('Tentative de nettoyage du lock et re-migration...');
      await cleanMigrationLocks(db);
      try {
        const [batch, migrations] = await db.migrate.latest();
        if (migrations.length > 0) {
          log.info(`Migrations récupérées (batch ${batch}) : ${migrations.map(m => path.basename(m)).join(', ')}`);
        }
      } catch (retryErr) {
        log.error('Échec de la re-migration :', retryErr.message);
        throw retryErr;
      }
    } else {
      throw err;
    }
  }

  // === Afficher les infos de la DB ===
  try {
    const tables = await db.raw(
      `SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_schema = 'public' AND table_catalog = ?`,
      [dbName]
    );
    const tableCount = tables.rows?.[0]?.cnt || 0;

    // Compter les guilds existantes
    const hasGuilds = await db.schema.hasTable('guilds');
    let guildCount = 0;
    if (hasGuilds) {
      const result = await db('guilds').count('* as cnt').first();
      guildCount = result?.cnt || 0;
    }

    log.info(`Base de données prête : ${tableCount} table(s), ${guildCount} guild(s)`);
  } catch {
    log.info('Base de données prête');
  }

  // === Démarrer le health monitoring ===
  startHealthMonitor();

  return db;
}

// ===================================
// Health Monitoring
// ===================================

/**
 * Démarre le monitoring périodique de la connexion DB.
 * Tente une reconnexion automatique si la connexion est perdue.
 */
function startHealthMonitor() {
  if (healthMonitorInterval) clearInterval(healthMonitorInterval);

  healthMonitorInterval = setInterval(async () => {
    if (isShuttingDown || !db) return;

    const healthy = await testConnection(db);

    if (!healthy) {
      log.warn('Connexion DB perdue, tentative de reconnexion...');

      // Tenter de recréer le pool
      try {
        await db.destroy();
      } catch { /* ignore */ }

      db = knex(knexConfig);
      const reconnected = await testConnection(db);

      if (reconnected) {
        log.info('Reconnexion à PostgreSQL réussie');
      } else {
        log.error('Échec de reconnexion à PostgreSQL — le bot pourrait ne pas fonctionner correctement');
        try { await db.destroy(); } catch { /* ignore */ }
        db = knex(knexConfig); // Prêt pour le prochain check
      }
    }
  }, CONFIG.healthCheckInterval);

  // Ne pas empêcher Node de s'arrêter
  if (healthMonitorInterval.unref) {
    healthMonitorInterval.unref();
  }
}

// ===================================
// Accès au pool
// ===================================

/**
 * Retourne l'instance Knex (après init).
 * Toutes les requêtes passent par cette instance unique.
 *
 * @returns {import('knex').Knex}
 * @throws {Error} Si la DB n'est pas initialisée
 */
function getDb() {
  if (!db) {
    throw new Error(
      'Base de données non initialisée. Appelez db.init() au démarrage (index.js).\n' +
      'Si cette erreur survient pendant le fonctionnement normal, la connexion a peut-être été perdue.'
    );
  }
  return db;
}

// ===================================
// Transactions
// ===================================

/**
 * Exécute un callback dans une transaction DB.
 * La transaction est commit automatiquement si le callback réussit,
 * rollback si une erreur est levée.
 *
 * Idéal pour les opérations multi-table (ex: créer une sanction + log).
 *
 * @param {Function} callback - async (trx) => { ... }
 * @returns {Promise<*>} Le résultat du callback
 *
 * @example
 * const result = await db.transaction(async (trx) => {
 *   await trx('sanctions').insert({ guild_id, ... });
 *   await trx('logs').insert({ guild_id, type: 'BAN', ... });
 *   return { success: true };
 * });
 */
async function transaction(callback) {
  const instance = getDb();
  return instance.transaction(callback);
}

// ===================================
// Query Helpers Multi-Serveur
// ===================================

/**
 * Requête paginée pour une guild.
 * Retourne les résultats + métadonnées de pagination.
 *
 * @param {string} table - Nom de la table
 * @param {string} guildId - ID du serveur Discord
 * @param {object} options
 * @param {number} [options.page=1] - Page (1-based)
 * @param {number} [options.perPage=25] - Résultats par page
 * @param {string} [options.orderBy='created_at'] - Colonne de tri
 * @param {string} [options.order='desc'] - Direction du tri
 * @param {object} [options.where] - Conditions supplémentaires
 * @returns {Promise<{ data: Array, pagination: object }>}
 */
async function paginatedQuery(table, guildId, options = {}) {
  const {
    page = 1,
    perPage = CONFIG.defaultPageSize,
    orderBy = 'created_at',
    order = 'desc',
    where = {},
  } = options;

  const safePerPage = Math.min(Math.max(1, perPage), CONFIG.maxPageSize);
  const safePage = Math.max(1, page);
  const offset = (safePage - 1) * safePerPage;

  const instance = getDb();

  // Compter le total
  const countQuery = instance(table).where({ guild_id: guildId, ...where });
  const [{ cnt }] = await countQuery.count('* as cnt');

  // Récupérer les données
  const data = await instance(table)
    .where({ guild_id: guildId, ...where })
    .orderBy(orderBy, order)
    .limit(safePerPage)
    .offset(offset);

  const totalPages = Math.ceil(cnt / safePerPage);

  return {
    data,
    pagination: {
      page: safePage,
      perPage: safePerPage,
      total: cnt,
      totalPages,
      hasNext: safePage < totalPages,
      hasPrev: safePage > 1,
    },
  };
}

/**
 * Opération bulk (insert/update) avec découpage en chunks.
 * Découpe les gros inserts en chunks pour éviter les dépassements.
 *
 * @param {string} table - Nom de la table
 * @param {Array<object>} rows - Données à insérer
 * @param {object} [options]
 * @param {number} [options.chunkSize=500] - Taille des chunks
 * @param {boolean} [options.useTransaction=true] - Wrapper en transaction
 * @returns {Promise<number>} Nombre total de lignes insérées
 */
async function bulkInsert(table, rows, options = {}) {
  if (!rows || rows.length === 0) return 0;

  const { chunkSize = 500, useTransaction = true } = options;
  const instance = getDb();

  const doInsert = async (trx) => {
    const conn = trx || instance;
    let inserted = 0;

    for (let i = 0; i < rows.length; i += chunkSize) {
      const chunk = rows.slice(i, i + chunkSize);
      await conn(table).insert(chunk);
      inserted += chunk.length;
    }

    return inserted;
  };

  if (useTransaction) {
    return instance.transaction(doInsert);
  }
  return doInsert(null);
}

/**
 * Suppression de données d'une guild dans une table.
 * Sécurisé : exige toujours un guild_id.
 *
 * @param {string} table - Nom de la table
 * @param {string} guildId - ID du serveur Discord
 * @param {object} [where] - Conditions supplémentaires
 * @returns {Promise<number>} Nombre de lignes supprimées
 */
async function deleteGuildData(table, guildId, where = {}) {
  if (!guildId) throw new Error('guild_id requis pour la suppression de données');
  const instance = getDb();
  return instance(table).where({ guild_id: guildId, ...where }).del();
}

/**
 * Compte les lignes pour une guild dans une table.
 *
 * @param {string} table - Nom de la table
 * @param {string} guildId - ID du serveur Discord
 * @param {object} [where] - Conditions supplémentaires
 * @returns {Promise<number>}
 */
async function countGuildRows(table, guildId, where = {}) {
  const instance = getDb();
  const [{ cnt }] = await instance(table)
    .where({ guild_id: guildId, ...where })
    .count('* as cnt');
  return cnt;
}

// ===================================
// Health Check & Métriques
// ===================================

/**
 * Vérifie que la connexion DB est active.
 * Utile pour le health check API et le monitoring.
 *
 * @returns {Promise<{ ok: boolean, latency?: number, error?: string }>}
 */
async function healthCheck() {
  if (!db) {
    return { ok: false, error: 'Database not initialized' };
  }

  const start = Date.now();
  try {
    await db.raw('SELECT 1');
    return { ok: true, latency: Date.now() - start };
  } catch (err) {
    return { ok: false, latency: Date.now() - start, error: err.message };
  }
}

/**
 * Retourne des statistiques détaillées sur la DB.
 * Utile pour /stats et le dashboard.
 *
 * @returns {Promise<object>}
 */
async function getStats() {
  if (!db) return { connected: false };

  try {
    const health = await healthCheck();
    const pool = db.client.pool;

    // Statistiques de base
    const stats = {
      connected: health.ok,
      latency: health.latency,
      pool: {
        used: pool?.numUsed?.() ?? 0,
        free: pool?.numFree?.() ?? 0,
        pending: pool?.numPendingCreates?.() ?? 0,
        total: (pool?.numUsed?.() ?? 0) + (pool?.numFree?.() ?? 0),
        max: knexConfig.pool?.max || 10,
      },
    };

    // Compter les données par guild pour la vue d'ensemble
    if (health.ok) {
      try {
        const guilds = await db('guilds').count('* as cnt').first();
        const users = await db.schema.hasTable('users')
          ? await db('users').count('* as cnt').first()
          : { cnt: 0 };
        const sanctions = await db.schema.hasTable('sanctions')
          ? await db('sanctions').count('* as cnt').first()
          : { cnt: 0 };

        stats.data = {
          guilds: guilds?.cnt || 0,
          users: users?.cnt || 0,
          sanctions: sanctions?.cnt || 0,
        };
      } catch {
        // Non-bloquant
      }
    }

    return stats;
  } catch {
    return { connected: false };
  }
}

/**
 * Retourne le statut des migrations.
 *
 * @returns {Promise<{ current: string[], pending: string[] }>}
 */
async function getMigrationStatus() {
  if (!db) throw new Error('Database not initialized');

  try {
    const [completed] = await db.migrate.list();
    const current = completed.map((m) => path.basename(m));

    // Les migrations sources (fichiers dispo)
    const config = db.migrate;
    return { current, pending: [] };
  } catch (err) {
    return { current: [], pending: [], error: err.message };
  }
}

// ===================================
// Shutdown
// ===================================

/**
 * Ferme proprement le pool de connexions.
 * Appelé lors du shutdown du bot.
 */
async function close() {
  isShuttingDown = true;

  // Arrêter le health monitor
  if (healthMonitorInterval) {
    clearInterval(healthMonitorInterval);
    healthMonitorInterval = null;
  }

  if (db) {
    try {
      // Attendre un court instant pour les requêtes en cours
      await sleep(500);
      await db.destroy();
      log.info('Pool de connexions PostgreSQL fermé');
    } catch (err) {
      log.error('Erreur fermeture pool PostgreSQL:', err.message);
    }
    db = null;
  }
}

// ===================================
// Exports
// ===================================

module.exports = {
  // Lifecycle
  init,
  close,
  getDb,

  // Transactions
  transaction,

  // Query helpers multi-serveur
  paginatedQuery,
  bulkInsert,
  deleteGuildData,
  countGuildRows,

  // Monitoring
  healthCheck,
  getStats,
  getMigrationStatus,
};