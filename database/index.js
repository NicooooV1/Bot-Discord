// ===================================
// Ultra Suite — Database Manager
// MySQL (mysql2) + Knex — migrations auto
// ===================================

const knex = require('knex');
const knexConfig = require('./knexfile');
const path = require('path');

let db = null;

/**
 * Initialise la connexion MySQL et lance les migrations
 * @returns {import('knex').Knex}
 */
async function init() {
  db = knex(knexConfig);

  // Vérifier que MySQL est joignable
  try {
    await db.raw('SELECT 1');
  } catch (err) {
    console.error('[DB] Impossible de se connecter à MySQL :', err.message);
    throw err;
  }

  // Exécute les migrations en attente
  const [batch, migrations] = await db.migrate.latest();
  if (migrations.length > 0) {
    console.log(`[DB] Migrations exécutées (batch ${batch}) :`);
    migrations.forEach((m) => console.log(`     ↳ ${path.basename(m)}`));
  } else {
    console.log('[DB] Base de données à jour.');
  }

  return db;
}

/**
 * Retourne l'instance Knex (après init)
 * @returns {import('knex').Knex}
 */
function getDb() {
  if (!db) throw new Error('Database not initialized. Call db.init() first.');
  return db;
}

/**
 * Ferme proprement le pool de connexions
 */
async function close() {
  if (db) await db.destroy();
  db = null;
}

module.exports = { init, getDb, close };
