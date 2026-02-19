// ===================================
// Ultra Suite — Winston Logger
// Logs rotatifs en fichier + console
// ===================================

const winston = require('winston');
const path = require('path');
const fs = require('fs');

const logsDir = path.join(__dirname, '..', '..', 'logs');
if (!fs.existsSync(logsDir)) fs.mkdirSync(logsDir, { recursive: true });

const LOG_LEVEL = process.env.LOG_LEVEL || 'info';

// Format console lisible
const consoleFormat = winston.format.combine(
  winston.format.colorize(),
  winston.format.timestamp({ format: 'HH:mm:ss' }),
  winston.format.printf(({ timestamp, level, message, module, ...meta }) => {
    const mod = module ? `[${module}]` : '';
    const extra = Object.keys(meta).length ? ` ${JSON.stringify(meta)}` : '';
    return `${timestamp} ${level} ${mod} ${message}${extra}`;
  })
);

// Format fichier JSON
const fileFormat = winston.format.combine(
  winston.format.timestamp(),
  winston.format.json()
);

const logger = winston.createLogger({
  level: LOG_LEVEL,
  defaultMeta: {},
  transports: [
    // Console
    new winston.transports.Console({ format: consoleFormat }),

    // Fichier combiné
    new winston.transports.File({
      filename: path.join(logsDir, 'combined.log'),
      format: fileFormat,
      maxsize: 5 * 1024 * 1024, // 5 MB
      maxFiles: 5,
      tailable: true,
    }),

    // Fichier erreurs uniquement
    new winston.transports.File({
      filename: path.join(logsDir, 'error.log'),
      format: fileFormat,
      level: 'error',
      maxsize: 5 * 1024 * 1024,
      maxFiles: 3,
      tailable: true,
    }),
  ],
});

/**
 * Crée un logger enfant avec un nom de module
 * @param {string} moduleName
 * @returns {winston.Logger}
 */
function createModuleLogger(moduleName) {
  return logger.child({ module: moduleName });
}

module.exports = { logger, createModuleLogger };
