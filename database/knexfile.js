// ===================================
// Ultra Suite — Knex Configuration
// MySQL (mysql2) — phpMyAdmin compatible
//
// Un seul pool de connexions pour tous les serveurs.
// Les données sont séparées par guild_id dans chaque table.
// ===================================

const path = require('path');

// ===================================
// Déterminer l'environnement
// ===================================
const isProduction = process.env.NODE_ENV === 'production';

// ===================================
// Configuration de base MySQL
// ===================================
const connection = {
  host: process.env.DB_HOST || '127.0.0.1',
  port: parseInt(process.env.DB_PORT, 10) || 3306,
  user: process.env.DB_USER || 'root',
  password: process.env.DB_PASSWORD || '',
  database: process.env.DB_NAME || 'ultra_suite',

  // Charset UTF-8 complet (emojis, caractères spéciaux)
  charset: 'utf8mb4',

  // Retourner les dates en string ISO (cohérent avec le code JS)
  dateStrings: true,

  // Timeouts de connexion (évite de bloquer indéfiniment)
  connectTimeout: 10000,    // 10s pour établir la connexion
  acquireTimeout: 10000,    // 10s pour obtenir une connexion du pool

  // Reconnexion automatique si la connexion est perdue
  // (Pterodactyl peut redémarrer MySQL)
  enableKeepAlive: true,
  keepAliveInitialDelay: 30000, // 30s
};

// ===================================
// Configuration du pool de connexions
// Dimensionné pour le multi-serveur
// ===================================
const pool = {
  // Minimum de connexions maintenues ouvertes
  min: isProduction ? 2 : 1,

  // Maximum de connexions simultanées
  // En multi-serveur, les requêtes arrivent de toutes les guilds
  // 10 suffit pour la plupart des cas (< 100 serveurs)
  // Augmenter si > 100 serveurs actifs simultanément
  max: parseInt(process.env.DB_POOL_MAX, 10) || 10,

  // Timeout pour obtenir une connexion du pool (ms)
  acquireTimeoutMillis: 15000, // 15s

  // Timeout d'inactivité avant de fermer une connexion (ms)
  idleTimeoutMillis: 30000, // 30s

  // Intervalle de vérification des connexions mortes (ms)
  reapIntervalMillis: 1000,

  // Vérifier que la connexion est valide avant de la donner
  afterCreate(conn, done) {
    conn.query('SET SESSION wait_timeout = 28800', (err) => {
      // 8h de timeout session (défaut MySQL)
      done(err, conn);
    });
  },
};

// ===================================
// Export Knex config
// ===================================
module.exports = {
  client: 'mysql2',
  connection,
  pool,

  // Répertoire des migrations
  migrations: {
    directory: path.join(__dirname, 'migrations'),
    tableName: 'knex_migrations',
    // Trier les migrations par nom de fichier
    sortDirsSeparately: true,
  },

  // Répertoire des seeds (optionnel, pour les données de test)
  seeds: {
    directory: path.join(__dirname, 'seeds'),
  },

  // Options de debug (désactivé en production)
  debug: process.env.DB_DEBUG === 'true',

  // Log des requêtes lentes (> 1s)
  log: {
    warn(message) {
      if (message?.includes?.('long running')) {
        console.warn('[DB SLOW]', message);
      }
    },
    error(message) {
      console.error('[DB ERROR]', message);
    },
    deprecate(message) {
      console.warn('[DB DEPRECATE]', message);
    },
    debug() {
      // Silencieux en production
    },
  },
};