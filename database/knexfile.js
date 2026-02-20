// ===================================
// Ultra Suite — Knex Configuration
// MySQL (mysql2) — Multi-serveur
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

  // Fuseau horaire UTC pour cohérence multi-serveur
  timezone: '+00:00',

  // Retourner les dates en string ISO (cohérent avec le code JS)
  dateStrings: true,

  // Timeout de connexion initiale (évite de bloquer indéfiniment)
  connectTimeout: 10000, // 10s

  // Reconnexion automatique si la connexion est perdue
  // (Pterodactyl peut redémarrer MySQL)
  enableKeepAlive: true,
  keepAliveInitialDelay: 30000, // 30s

  // Autoriser plusieurs requêtes dans un seul appel (utile pour les migrations)
  multipleStatements: false,

  // Support SSL optionnel (si DB distante)
  ...(process.env.DB_SSL === 'true' && {
    ssl: { rejectUnauthorized: process.env.DB_SSL_REJECT_UNAUTHORIZED !== 'false' },
  }),
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

  // Timeout pour créer une nouvelle connexion (ms)
  createTimeoutMillis: 10000, // 10s

  // Intervalle de retry si la création échoue (ms)
  createRetryIntervalMillis: 500,

  // Timeout d'inactivité avant de fermer une connexion (ms)
  idleTimeoutMillis: isProduction ? 60000 : 30000,

  // Intervalle de vérification des connexions mortes (ms)
  reapIntervalMillis: 1000,

  // Propager les erreurs de création de connexion (ne pas rester bloqué)
  propagateCreateError: false,

  // Configurer chaque nouvelle connexion
  afterCreate(conn, done) {
    // Exécuter les SETtings de session séquentiellement
    // (multipleStatements est désactivé pour la sécurité)
    conn.query('SET SESSION wait_timeout = 28800', (err) => {
      if (err) return done(err, conn);
      conn.query('SET SESSION interactive_timeout = 28800', (err2) => {
        if (err2) return done(err2, conn);
        conn.query("SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO'", (err3) => {
          if (err3) return done(err3, conn);
          conn.query("SET SESSION time_zone = '+00:00'", (err4) => {
            done(err4, conn);
          });
        });
      });
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
    // Charger les extensions .js
    loadExtensions: ['.js'],
  },

  // Répertoire des seeds (optionnel, pour les données de test)
  seeds: {
    directory: path.join(__dirname, 'seeds'),
  },

  // Options de debug (désactivé en production)
  debug: process.env.DB_DEBUG === 'true',

  // Wrapper de requête pour monitoring multi-serveur
  wrapIdentifier: (value, origImpl) => origImpl(value),

  // Log structuré
  log: {
    warn(message) {
      // Log les requêtes lentes et les avertissements
      if (typeof message === 'string') {
        if (message.includes('long running')) {
          console.warn('[DB SLOW]', message);
        } else if (message.includes('migration')) {
          console.warn('[DB MIGRATION]', message);
        }
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