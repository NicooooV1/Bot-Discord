// ===================================
// Ultra Suite — Knex Configuration
// PostgreSQL 16 — Multi-serveur
//
// Connexion vers le LXC PostgreSQL (LXC 112)
// Pool configure pour la production multi-serveur
// ===================================

const path = require('path');

// ===================================
// Configuration de connexion PostgreSQL
// ===================================
const connection = {
  host: process.env.DB_HOST || '192.168.1.216',
  port: parseInt(process.env.DB_PORT, 10) || 5432,
  user: process.env.DB_USER || 'botuser',
  password: process.env.DB_PASSWORD || '',
  database: process.env.DB_NAME || 'discordbot',

  // SSL (active si DB_SSL=true)
  ...(process.env.DB_SSL === 'true' && {
    ssl: { rejectUnauthorized: false },
  }),
};

// ===================================
// Configuration du pool de connexions
// Adapte pour un bot multi-serveur en production
// ===================================
const pool = {
  // Min/Max connexions actives
  min: parseInt(process.env.DB_POOL_MIN, 10) || 2,
  max: parseInt(process.env.DB_POOL_MAX, 10) || 10,

  // Timeout d acquisition d une connexion (30s)
  acquireTimeoutMillis: 30000,

  // Timeout de creation d une connexion (20s)
  createTimeoutMillis: 20000,

  // Timeout d inactivite avant destruction (30s)
  idleTimeoutMillis: 30000,

  // Interval de verification des connexions idle
  reapIntervalMillis: 1000,

  // Propager les erreurs de creation de connexion
  propagateCreateError: false,

  // Configurer chaque nouvelle connexion PostgreSQL
  afterCreate(conn, done) {
    conn.query("SET timezone = 'UTC'", (err) => {
      if (err) return done(err, conn);
      conn.query("SET statement_timeout = '30s'", (err2) => {
        done(err2, conn);
      });
    });
  },
};

// ===================================
// Export Knex config
// ===================================
module.exports = {
  client: 'pg',
  connection,
  pool,

  // Repertoire des migrations
  migrations: {
    directory: path.join(__dirname, 'migrations'),
    tableName: 'knex_migrations',
    sortDirsSeparately: true,
    loadExtensions: ['.js'],
  },

  // Repertoire des seeds (optionnel)
  seeds: {
    directory: path.join(__dirname, 'seeds'),
  },

  // Options de debug (desactive en production)
  debug: process.env.DB_DEBUG === 'true',

  // Wrapper d identifiants
  wrapIdentifier: (value, origImpl) => origImpl(value),

  // Log structure
  log: {
    warn(message) {
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
