// ===================================
// Ultra Suite — Logger
// Système de log structuré pour le bot
//
// Fonctionnalités :
// - Niveaux : ERROR, WARN, INFO, DEBUG
// - Format : couleurs en dev, JSON en prod
// - Timestamp ISO sur chaque ligne
// - Tag module (ex: [ConfigService], [Database])
// - Filtrage par LOG_LEVEL (.env)
// - Écrit sur stdout/stderr (compatible Docker/Pterodactyl)
//
// Usage :
//   const { logger, createModuleLogger } = require('./core/logger');
//   logger.info('Message global');
//
//   const log = createModuleLogger('MonModule');
//   log.info('Message avec tag [MonModule]');
//   log.error('Erreur', err.message);
//   log.debug('Debug verbose');
// ===================================

// ===================================
// Niveaux de log
// ===================================
const LOG_LEVELS = {
  ERROR: 0,
  WARN: 1,
  INFO: 2,
  DEBUG: 3,
};

// Niveau actuel (configurable via .env)
const currentLevel = LOG_LEVELS[
  (process.env.LOG_LEVEL || 'INFO').toUpperCase()
] ?? LOG_LEVELS.INFO;

// Mode production (JSON output, pas de couleurs)
const isProduction = process.env.NODE_ENV === 'production';

// ===================================
// Couleurs ANSI (dev uniquement)
// ===================================
const COLORS = {
  RESET: '\x1b[0m',
  RED: '\x1b[31m',
  YELLOW: '\x1b[33m',
  GREEN: '\x1b[32m',
  CYAN: '\x1b[36m',
  GRAY: '\x1b[90m',
  BOLD: '\x1b[1m',
  DIM: '\x1b[2m',
};

const LEVEL_COLORS = {
  ERROR: COLORS.RED,
  WARN: COLORS.YELLOW,
  INFO: COLORS.GREEN,
  DEBUG: COLORS.GRAY,
};

const LEVEL_ICONS = {
  ERROR: '✖',
  WARN: '⚠',
  INFO: '●',
  DEBUG: '○',
};

// ===================================
// Formattage
// ===================================

/**
 * Formate un timestamp ISO court (HH:mm:ss.SSS)
 */
function shortTimestamp() {
  const now = new Date();
  return now.toISOString().slice(11, 23);
}

/**
 * Formate un message de log en mode développement (couleurs)
 *
 * @param {string} level
 * @param {string} module
 * @param {string} message
 * @param {any[]} args
 * @returns {string}
 */
function formatDev(level, module, message, args) {
  const color = LEVEL_COLORS[level] || COLORS.RESET;
  const icon = LEVEL_ICONS[level] || '●';
  const time = `${COLORS.DIM}${shortTimestamp()}${COLORS.RESET}`;
  const tag = module ? `${COLORS.CYAN}[${module}]${COLORS.RESET} ` : '';
  const lvl = `${color}${icon} ${level.padEnd(5)}${COLORS.RESET}`;

  let msg = `${time} ${lvl} ${tag}${message}`;

  // Ajouter les arguments supplémentaires
  if (args.length > 0) {
    for (const arg of args) {
      if (typeof arg === 'object') {
        msg += ' ' + JSON.stringify(arg);
      } else {
        msg += ' ' + String(arg);
      }
    }
  }

  return msg;
}

/**
 * Formate un message de log en mode production (JSON)
 *
 * @param {string} level
 * @param {string} module
 * @param {string} message
 * @param {any[]} args
 * @returns {string}
 */
function formatProd(level, module, message, args) {
  const entry = {
    time: new Date().toISOString(),
    level,
    msg: message,
  };

  if (module) entry.module = module;

  // Extraire les données supplémentaires
  if (args.length > 0) {
    const extra = {};
    for (let i = 0; i < args.length; i++) {
      const arg = args[i];
      if (typeof arg === 'object' && arg !== null) {
        Object.assign(extra, arg);
      } else {
        extra[`arg${i}`] = arg;
      }
    }
    if (Object.keys(extra).length > 0) {
      entry.data = extra;
    }
  }

  return JSON.stringify(entry);
}

/**
 * Écrit un message de log
 *
 * @param {string} level
 * @param {string} module
 * @param {string} message
 * @param {any[]} args
 */
function write(level, module, message, args) {
  // Filtrer par niveau
  if ((LOG_LEVELS[level] ?? 0) > currentLevel) return;

  const formatted = isProduction
    ? formatProd(level, module, message, args)
    : formatDev(level, module, message, args);

  // ERROR et WARN → stderr, le reste → stdout
  if (level === 'ERROR' || level === 'WARN') {
    process.stderr.write(formatted + '\n');
  } else {
    process.stdout.write(formatted + '\n');
  }
}

// ===================================
// Logger principal (sans tag module)
// ===================================
const logger = {
  error: (message, ...args) => write('ERROR', null, message, args),
  warn: (message, ...args) => write('WARN', null, message, args),
  info: (message, ...args) => write('INFO', null, message, args),
  debug: (message, ...args) => write('DEBUG', null, message, args),

  /**
   * Retourne le niveau de log actuel
   * @returns {string}
   */
  getLevel() {
    return Object.entries(LOG_LEVELS).find(([, v]) => v === currentLevel)?.[0] || 'INFO';
  },
};

// ===================================
// Logger par module
// ===================================

/**
 * Crée un logger taggé avec le nom du module
 * Chaque message sera préfixé par [NomDuModule]
 *
 * @param {string} moduleName — Nom du module (ex: 'ConfigService', 'Database')
 * @returns {{ error, warn, info, debug }}
 *
 * @example
 * const log = createModuleLogger('Tickets');
 * log.info('Ticket créé');           // → 12:34:56.789 ● INFO  [Tickets] Ticket créé
 * log.error('Erreur', err.message);  // → 12:34:56.789 ✖ ERROR [Tickets] Erreur ...
 */
function createModuleLogger(moduleName) {
  return {
    error: (message, ...args) => write('ERROR', moduleName, message, args),
    warn: (message, ...args) => write('WARN', moduleName, message, args),
    info: (message, ...args) => write('INFO', moduleName, message, args),
    debug: (message, ...args) => write('DEBUG', moduleName, message, args),
  };
}

module.exports = { logger, createModuleLogger };