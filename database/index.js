// ===================================
// Ultra Suite — Database Manager
// Initialise Knex + run migrations
// ===================================

const knex = require('knex');
const knexConfig = require('./knexfile');
const path = require('path');
const fs = require('fs');

let db = null;

/**
 * Initialise la connexion et lance les migrations
 * @returns {import('knex').Knex}
 */
async function init() {
  // Crée le dossier data/ s'il n'existe pas
  const dataDir = path.join(__dirname, '..', '..', 'data');
  if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });

  db = knex(knexConfig);

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
 * Ferme proprement la connexion
 */
async function close() {
  if (db) await db.destroy();
  db = null;
}

module.exports = { init, getDb, close };
