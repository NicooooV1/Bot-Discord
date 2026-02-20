// ===================================
// Ultra Suite — Database Manager
// MySQL (mysql2) + Knex — migrations auto
//
// Single pool de connexions partagé entre
// tous les serveurs (guild_id sépare les données)
// ===================================

const knex = require('knex');
const knexConfig = require('./knexfile');
const path = require('path');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('Database');

let db = null;

// ===================================
// Configuration retry
// ===================================
const MAX_RETRIES = 5;
const RETRY_DELAY_MS = 3000; // 3 secondes entre chaque tentative

/**
 * Attend un délai en ms
 */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Teste la connexion à MySQL
 * @param {import('knex').Knex} instance
 * @returns {Promise<boolean>}
 */
async function testConnection(instance) {
  try {
    await instance.raw('SELECT 1 + 1 AS result');
    return true;
  } catch {
    return false;
  }
}

/**
 * Initialise la connexion MySQL avec retry et lance les migrations
 *
 * En multi-serveur, un seul pool de connexions est utilisé.
 * Les données sont séparées par guild_id dans chaque table.
 *
 * @returns {Promise<import('knex').Knex>}
 */
async function init() {
  log.info('Connexion à MySQL...');
  log.info(`  Host : ${process.env.DB_HOST || '127.0.0.1'}:${process.env.DB_PORT || 3306}`);
  log.info(`  DB   : ${process.env.DB_NAME || 'ultra_suite'}`);
  log.info(`  User : ${process.env.DB_USER || 'root'}`);

  db = knex(knexConfig);

  // === Tentatives de connexion avec retry ===
  let connected = false;
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    connected = await testConnection(db);

    if (connected) {
      log.info(`Connexion MySQL établie (tentative ${attempt}/${MAX_RETRIES})`);
      break;
    }

    if (attempt < MAX_RETRIES) {
      log.warn(`Connexion échouée (tentative ${attempt}/${MAX_RETRIES}), retry dans ${RETRY_DELAY_MS / 1000}s...`);
      await sleep(RETRY_DELAY_MS);
    }
  }

  if (!connected) {
    log.error(`Impossible de se connecter à MySQL après ${MAX_RETRIES} tentatives.`);
    log.error('Vérifiez les variables DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME dans .env');
    throw new Error('Database connection failed');
  }

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
    log.error('La base de données pourrait être dans un état incohérent.');
    throw err;
  }

  // === Afficher les infos de la DB ===
  try {
    const tables = await db.raw(
      `SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_schema = ?`,
      [process.env.DB_NAME || 'ultra_suite']
    );
    const tableCount = tables[0]?.[0]?.cnt || 0;
    log.info(`Base de données prête : ${tableCount} table(s)`);

    // Compter les guilds existantes
    const hasGuilds = await db.schema.hasTable('guilds');
    if (hasGuilds) {
      const guildCount = await db('guilds').count('* as cnt').first();
      log.info(`Guilds enregistrées : ${guildCount?.cnt || 0}`);
    }
  } catch {
    // Non-bloquant : juste pour l'affichage
  }

  // === Pool info ===
  const poolConfig = knexConfig.pool || {};
  log.info(`Pool connexions : min=${poolConfig.min || 2}, max=${poolConfig.max || 10}`);

  return db;
}

/**
 * Retourne l'instance Knex (après init)
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

/**
 * Vérifie que la connexion DB est active
 * Utile pour le health check API et le monitoring
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
 * Retourne des statistiques sur la DB
 * Utile pour la commande /stats et le dashboard
 *
 * @returns {Promise<object>}
 */
async function getStats() {
  if (!db) return { connected: false };

  try {
    const health = await healthCheck();
    const pool = db.client.pool;

    return {
      connected: health.ok,
      latency: health.latency,
      pool: {
        used: pool?.numUsed?.() ?? 0,
        free: pool?.numFree?.() ?? 0,
        pending: pool?.numPendingCreates?.() ?? 0,
      },
    };
  } catch {
    return { connected: false };
  }
}

/**
 * Ferme proprement le pool de connexions
 * Appelé lors du shutdown du bot
 */
async function close() {
  if (db) {
    try {
      await db.destroy();
      log.info('Pool de connexions MySQL fermé');
    } catch (err) {
      log.error('Erreur fermeture pool MySQL:', err.message);
    }
    db = null;
  }
}

module.exports = { init, getDb, close, healthCheck, getStats };