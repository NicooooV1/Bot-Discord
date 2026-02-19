// ===================================
// Ultra Suite — Knex Configuration
// MySQL (mysql2) — phpMyAdmin compatible
// ===================================

const path = require('path');

module.exports = {
  client: 'mysql2',
  connection: {
    host: process.env.DB_HOST || '127.0.0.1',
    port: parseInt(process.env.DB_PORT, 10) || 3306,
    user: process.env.DB_USER || 'root',
    password: process.env.DB_PASSWORD || '',
    database: process.env.DB_NAME || 'ultra_suite',
    charset: 'utf8mb4',
    // Retourner les dates en string ISO (cohérent avec le code existant)
    dateStrings: true,
  },
  pool: {
    min: 2,
    max: 10,
  },
  migrations: {
    directory: path.join(__dirname, 'migrations'),
    tableName: 'knex_migrations',
  },
};
