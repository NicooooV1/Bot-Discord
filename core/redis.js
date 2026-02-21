// ===================================
// Ultra Suite — Redis Client Manager
// Redis 7 — Multi-DB (LXC 113)
//
// DB0 : Cache général (config, sessions)
// DB1 : AutoMod (rate limiting, spam detection)
// DB2 : Economy (cooldowns, transactions temp)
// DB3 : Dashboard (sessions web)
//
// Utilise ioredis pour la connexion et les commandes.
// ===================================

const Redis = require('ioredis');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('Redis');

// ===================================
// Configuration
// ===================================
const REDIS_CONFIG = {
  host: process.env.REDIS_HOST || '192.168.1.217',
  port: parseInt(process.env.REDIS_PORT, 10) || 6379,
  password: process.env.REDIS_PASSWORD || '',
  connectTimeout: 10000,
  maxRetriesPerRequest: 3,
  retryStrategy(times) {
    const delay = Math.min(times * 200, 5000);
    log.warn(`Redis reconnexion tentative ${times} (délai: ${delay}ms)`);
    return delay;
  },
  lazyConnect: true,
};

// ===================================
// Instances par base de données
// ===================================
const clients = {};

/**
 * Crée ou retourne un client Redis pour la DB spécifiée.
 *
 * @param {number} db - Numéro de la base Redis (0-3)
 * @returns {import('ioredis').Redis}
 */
function getClient(db = 0) {
  if (clients[db]) return clients[db];

  const client = new Redis({
    ...REDIS_CONFIG,
    db,
    keyPrefix: '',
  });

  client.on('connect', () => {
    log.info(`Redis DB${db} connecté`);
  });

  client.on('error', (err) => {
    log.error(`Redis DB${db} erreur: ${err.message}`);
  });

  client.on('close', () => {
    log.debug(`Redis DB${db} connexion fermée`);
  });

  clients[db] = client;
  return client;
}

// ===================================
// Clients nommés (raccourcis)
// ===================================

/** Cache général : config guilds, données temporaires */
const cache = () => getClient(0);

/** AutoMod : rate limiting, compteurs spam, cooldowns modération */
const automod = () => getClient(1);

/** Economy : cooldowns work/daily/weekly, transactions en cours */
const economy = () => getClient(2);

/** Dashboard : sessions web, tokens OAuth2 */
const dashboard = () => getClient(3);

// ===================================
// Helpers de cache courants
// ===================================

/**
 * Récupère une valeur JSON du cache.
 *
 * @param {string} key - Clé Redis
 * @param {number} [db=0] - DB Redis
 * @returns {Promise<object|null>}
 */
async function getJSON(key, db = 0) {
  try {
    const raw = await getClient(db).get(key);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (err) {
    log.warn(`Redis getJSON(${key}) erreur: ${err.message}`);
    return null;
  }
}

/**
 * Stocke une valeur JSON dans le cache avec TTL optionnel.
 *
 * @param {string} key - Clé Redis
 * @param {object} value - Valeur à stocker
 * @param {number} [ttl=300] - Durée de vie en secondes (défaut: 5 min)
 * @param {number} [db=0] - DB Redis
 * @returns {Promise<boolean>}
 */
async function setJSON(key, value, ttl = 300, db = 0) {
  try {
    const serialized = JSON.stringify(value);
    if (ttl > 0) {
      await getClient(db).setex(key, ttl, serialized);
    } else {
      await getClient(db).set(key, serialized);
    }
    return true;
  } catch (err) {
    log.warn(`Redis setJSON(${key}) erreur: ${err.message}`);
    return false;
  }
}

/**
 * Supprime une ou plusieurs clés.
 *
 * @param {string|string[]} keys - Clé(s) à supprimer
 * @param {number} [db=0] - DB Redis
 * @returns {Promise<number>} Nombre de clés supprimées
 */
async function del(keys, db = 0) {
  try {
    const keyArray = Array.isArray(keys) ? keys : [keys];
    return await getClient(db).del(...keyArray);
  } catch (err) {
    log.warn(`Redis del erreur: ${err.message}`);
    return 0;
  }
}

/**
 * Incrémente un compteur avec TTL (utile pour rate limiting).
 *
 * @param {string} key - Clé du compteur
 * @param {number} [ttl=60] - TTL en secondes
 * @param {number} [db=1] - DB Redis (défaut: automod)
 * @returns {Promise<number>} Nouvelle valeur
 */
async function incr(key, ttl = 60, db = 1) {
  try {
    const client = getClient(db);
    const val = await client.incr(key);
    if (val === 1) {
      await client.expire(key, ttl);
    }
    return val;
  } catch (err) {
    log.warn(`Redis incr(${key}) erreur: ${err.message}`);
    return 0;
  }
}

/**
 * Supprime toutes les clés correspondant à un pattern.
 * Utilise SCAN pour ne pas bloquer Redis.
 *
 * @param {string} pattern - Pattern glob (ex: "cfg:*")
 * @param {number} [db=0] - DB Redis
 * @returns {Promise<number>} Nombre de clés supprimées
 */
async function delPattern(pattern, db = 0) {
  try {
    const client = getClient(db);
    let cursor = '0';
    let deleted = 0;

    do {
      const [nextCursor, keys] = await client.scan(cursor, 'MATCH', pattern, 'COUNT', 100);
      cursor = nextCursor;
      if (keys.length > 0) {
        await client.del(...keys);
        deleted += keys.length;
      }
    } while (cursor !== '0');

    return deleted;
  } catch (err) {
    log.warn(`Redis delPattern(${pattern}) erreur: ${err.message}`);
    return 0;
  }
}

// ===================================
// Lifecycle
// ===================================

/**
 * Initialise la connexion Redis (DB0 au minimum).
 * Les autres DB sont connectées à la demande.
 */
async function init() {
  try {
    const client = getClient(0);
    await client.connect();
    await client.ping();
    log.info('Redis initialisé (DB0 cache)');
    return true;
  } catch (err) {
    log.warn(`Redis non disponible: ${err.message} — le bot fonctionnera avec le cache mémoire`);
    return false;
  }
}

/**
 * Vérifie la connexion Redis.
 *
 * @returns {Promise<{ ok: boolean, latency?: number }>}
 */
async function healthCheck() {
  try {
    const start = Date.now();
    await getClient(0).ping();
    return { ok: true, latency: Date.now() - start };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * Ferme proprement toutes les connexions Redis.
 */
async function close() {
  const promises = Object.entries(clients).map(async ([db, client]) => {
    try {
      await client.quit();
      log.info(`Redis DB${db} fermé`);
    } catch (err) {
      log.warn(`Erreur fermeture Redis DB${db}: ${err.message}`);
    }
  });
  await Promise.allSettled(promises);
  Object.keys(clients).forEach((k) => delete clients[k]);
}

// ===================================
// Exports
// ===================================
module.exports = {
  // Lifecycle
  init,
  close,
  healthCheck,

  // Clients par DB
  getClient,
  cache,
  automod,
  economy,
  dashboard,

  // Helpers
  getJSON,
  setJSON,
  del,
  incr,
  delPattern,
};
