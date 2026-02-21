// ===================================
// Ultra Suite — Config Service
// Redis (DB0) + fallback mémoire pour config guild
// Données 100% séparées par serveur (guild_id)
//
// Chaque serveur a sa propre config indépendante.
// Redis sert de cache distribué (TTL 5 min).
// Fallback automatique sur NodeCache si Redis indisponible.
// ===================================

const NodeCache = require('node-cache');
const redis = require('./redis');
const guildQueries = require('../database/guildQueries');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('ConfigService');

// TTL 5 minutes — Redis + fallback mémoire
const CACHE_TTL = 300;
const localCache = new NodeCache({ stdTTL: CACHE_TTL, checkperiod: 60, useClones: true });

// Indicateur de disponibilité Redis
let redisAvailable = false;

/**
 * Initialise la connexion Redis du config service.
 * Si Redis n'est pas dispo, le service utilise le cache mémoire.
 */
async function initRedis() {
  try {
    await redis.cache().ping();
    redisAvailable = true;
    log.info('ConfigService: Redis connecté (cache distribué)');
  } catch {
    redisAvailable = false;
    log.warn('ConfigService: Redis indisponible — cache mémoire utilisé');
  }
}

// Appeler l'init Redis de manière non-bloquante
initRedis().catch(() => {});

/**
 * Helpers internes pour le cache hybride Redis + mémoire
 */
async function cacheGet(key) {
  // Essayer Redis d'abord
  if (redisAvailable) {
    try {
      const val = await redis.getJSON(key, 0);
      if (val) return val;
    } catch { /* fallback */ }
  }
  // Fallback mémoire
  return localCache.get(key) || null;
}

async function cacheSet(key, value) {
  // Toujours stocker en mémoire
  localCache.set(key, value);
  // Aussi en Redis si disponible
  if (redisAvailable) {
    try {
      await redis.setJSON(key, value, CACHE_TTL, 0);
    } catch { /* non-bloquant */ }
  }
}

async function cacheDel(key) {
  localCache.del(key);
  if (redisAvailable) {
    try {
      await redis.del(key, 0);
    } catch { /* non-bloquant */ }
  }
}

// ===================================
// Config par défaut pour un nouveau serveur
// Chaque guild reçoit une copie indépendante
// ===================================
const DEFAULT_CONFIG = {
  prefix: '!',
  locale: 'fr',

  // Logs
  logChannel: null,
  modLogChannel: null,

  // Modération
  automod: {
    enabled: false,
    antiSpam: false,
    antiLink: false,
    antiMention: false,
    maxWarns: 5,
    warnAction: 'TIMEOUT',      // TIMEOUT | KICK | BAN
    warnActionDuration: 3600,   // secondes
  },

  // Onboarding
  welcomeChannel: null,
  welcomeMessage: null,
  welcomeRole: null,
  goodbyeChannel: null,
  goodbyeMessage: null,

  // Tickets
  ticketCategory: null,
  ticketLogChannel: null,
  ticketStaffRole: null,
  maxTicketsPerUser: 3,

  // XP
  xp: {
    enabled: false,
    min: 15,
    max: 25,
    cooldown: 60,
    levelUpChannel: null,
    levelUpMessage: null,
    roleRewards: {},
  },

  // Economy
  economy: {
    enabled: false,
    currencyName: '💰',
    currencySymbol: '$',
    dailyAmount: 100,
    weeklyAmount: 500,
  },

  // Voices
  tempVoiceCategory: null,
  tempVoiceLobby: null,

  // Security
  antiRaid: {
    enabled: false,
    joinThreshold: 10,
    joinWindow: 10,
    action: 'kick',
  },

  // Roles
  roleMenus: [],

  // Muted role
  muteRole: null,
};

// ===================================
// Modules disponibles et leur état par défaut
// ===================================
const DEFAULT_MODULES = {
  moderation: false,
  tickets: false,
  logs: false,
  security: false,
  onboarding: false,
  xp: false,
  economy: false,
  roles: false,
  utility: false,     // Zéro comportement par défaut — activer via /config
  fun: false,          // Zéro comportement par défaut — activer via /config
  tags: false,
  announcements: false,
  stats: false,
  tempvoice: false,
  applications: false,
  events: false,
  custom_commands: false,
  music: false,
  rp: false,
  integrations: false,
};

// ===================================
// Helpers
// ===================================

/**
 * Résout une clé à points dans un objet
 * Ex: resolveKey({ a: { b: 3 } }, 'a.b') → 3
 */
function resolveKey(obj, key) {
  if (!obj || !key) return undefined;
  const parts = key.split('.');
  let current = obj;
  for (const part of parts) {
    if (current === undefined || current === null) return undefined;
    current = current[part];
  }
  return current;
}

/**
 * Parse JSON de manière sécurisée (protection contre les données corrompues)
 */
function safeJsonParse(str, fallback = {}) {
  if (!str) return fallback;
  if (typeof str === 'object') return str; // Déjà un objet
  try {
    return JSON.parse(str);
  } catch (err) {
    log.warn(`JSON corrompu détecté, fallback utilisé: ${err.message}`);
    return fallback;
  }
}

/**
 * Deep clone pour éviter les mutations entre serveurs
 */
function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

/**
 * Charge la guild depuis la DB et met en cache config + modules
 * @param {string} guildId
 * @returns {{ config: object, modules: object }}
 */
async function loadGuild(guildId) {
  try {
    const guild = await guildQueries.getOrCreate(guildId, 'Unknown', '0');

    // Merge avec les defaults pour garantir que toutes les clés existent
    const rawConfig = typeof guild.config === 'string'
      ? safeJsonParse(guild.config, {})
      : (guild.config || {});

    const rawModules = typeof guild.modules_enabled === 'string'
      ? safeJsonParse(guild.modules_enabled, {})
      : (guild.modules_enabled || {});

    // Deep merge : DEFAULT → DB (les valeurs DB écrasent les defaults)
    const config = deepMergeConfig(deepClone(DEFAULT_CONFIG), rawConfig);
    const modules = { ...deepClone(DEFAULT_MODULES), ...rawModules };

    await cacheSet(`cfg:${guildId}`, config);
    await cacheSet(`mod:${guildId}`, modules);

    return { config, modules };
  } catch (err) {
    log.error(`Erreur chargement config guild ${guildId}:`, err.message);
    // Retourner les defaults pour ne pas bloquer le bot
    const config = deepClone(DEFAULT_CONFIG);
    const modules = deepClone(DEFAULT_MODULES);
    return { config, modules };
  }
}

/**
 * Deep merge récursif (ne remplace pas les sous-objets entiers)
 */
function deepMergeConfig(target, source) {
  if (!source) return target;
  const output = { ...target };
  for (const key of Object.keys(source)) {
    if (source[key] === null || source[key] === undefined) {
      output[key] = source[key];
    } else if (
      typeof source[key] === 'object' &&
      !Array.isArray(source[key]) &&
      target[key] &&
      typeof target[key] === 'object' &&
      !Array.isArray(target[key])
    ) {
      output[key] = deepMergeConfig(target[key], source[key]);
    } else {
      output[key] = source[key];
    }
  }
  return output;
}

// ===================================
// Service principal
// ===================================
const configService = {
  /**
   * Récupère la config complète d'une guild (cache → DB)
   * Chaque guild a sa propre config totalement isolée.
   *
   * @param {string} guildId
   * @returns {Promise<object>}
   */
  async get(guildId) {
    if (!guildId) {
      log.warn('configService.get() appelé sans guildId');
      return deepClone(DEFAULT_CONFIG);
    }

    const cached = await cacheGet(`cfg:${guildId}`);
    if (cached) return cached;

    const { config } = await loadGuild(guildId);
    return config;
  },

  /**
   * Met à jour la config et invalide le cache
   * Merge intelligent : ne remplace pas les sous-objets entiers
   *
   * @param {string} guildId
   * @param {object} patch — Clés à modifier
   * @returns {Promise<object|null>}
   */
  async set(guildId, patch) {
    if (!guildId || !patch) return null;

    try {
      const merged = await guildQueries.updateConfig(guildId, patch);
      if (!merged) return null;

      const full = deepMergeConfig(deepClone(DEFAULT_CONFIG), merged);
      await cacheSet(`cfg:${guildId}`, full);

      log.info(`Config mise à jour pour guild ${guildId}`, {
        keys: Object.keys(patch),
      });

      return full;
    } catch (err) {
      log.error(`Erreur mise à jour config guild ${guildId}:`, err.message);
      return null;
    }
  },

  /**
   * Récupère une clé spécifique (supporte les clés à points)
   * Ex: getKey(guildId, 'automod.maxWarns')
   * Ex: getKey(guildId, 'economy.dailyAmount')
   *
   * @param {string} guildId
   * @param {string} key — Clé simple ou à points
   * @returns {Promise<any>}
   */
  async getKey(guildId, key) {
    const config = await this.get(guildId);
    return resolveKey(config, key);
  },

  /**
   * Modules activés pour une guild (cache → DB)
   * Retourne un objet { moduleName: boolean }
   *
   * @param {string} guildId
   * @returns {Promise<object>}
   */
  async getModules(guildId) {
    if (!guildId) return deepClone(DEFAULT_MODULES);

    const cached = await cacheGet(`mod:${guildId}`);
    if (cached) return cached;

    const { modules } = await loadGuild(guildId);
    return modules;
  },

  /**
   * Active/désactive un module pour une guild
   *
   * @param {string} guildId
   * @param {string} moduleName
   * @param {boolean} enabled
   * @returns {Promise<object>}
   */
  async setModule(guildId, moduleName, enabled) {
    if (!guildId || !moduleName) return null;

    // Valider que le module est enregistré dans le registry
    // Accepté même si absent (flexibilité pour modules custom)
    try {
      const moduleRegistry = require('./moduleRegistry');
      if (!moduleRegistry.has(moduleName) && !(moduleName in DEFAULT_MODULES)) {
        log.warn(`Module inconnu : "${moduleName}" (guild ${guildId})`);
      }
    } catch {
      // moduleRegistry pas encore chargé — pas grave
    }

    try {
      const modules = await guildQueries.updateModules(guildId, { [moduleName]: enabled });
      await cacheSet(`mod:${guildId}`, { ...deepClone(DEFAULT_MODULES), ...modules });
      await cacheDel(`cfg:${guildId}`); // Invalider la config aussi

      log.info(`Module "${moduleName}" ${enabled ? 'activé' : 'désactivé'} pour guild ${guildId}`);
      return modules;
    } catch (err) {
      log.error(`Erreur activation module ${moduleName} pour guild ${guildId}:`, err.message);
      return null;
    }
  },

  /**
   * Vérifie si un module est activé pour une guild (cache → DB)
   * Module "admin" est TOUJOURS activé (non désactivable).
   *
   * @param {string} guildId
   * @param {string} moduleName
   * @returns {Promise<boolean>}
   */
  async isModuleEnabled(guildId, moduleName) {
    // Le module admin est toujours activé
    if (moduleName === 'admin') return true;

    if (!guildId) return false;

    const modules = await this.getModules(guildId);
    const enabled = modules[moduleName] === true;

    // Log en debug pour aider au diagnostic
    if (!enabled) {
      log.debug(`Module "${moduleName}" désactivé pour guild ${guildId}`);
    }

    return enabled;
  },

  /**
   * Récupère la liste des modules activés (noms uniquement)
   *
   * @param {string} guildId
   * @returns {Promise<string[]>}
   */
  async getEnabledModuleNames(guildId) {
    const modules = await this.getModules(guildId);
    return Object.entries(modules)
      .filter(([, enabled]) => enabled)
      .map(([name]) => name);
  },

  /**
   * Réinitialise la config d'une guild aux valeurs par défaut
   * Attention : opération destructive !
   *
   * @param {string} guildId
   * @returns {Promise<object>}
   */
  async reset(guildId) {
    if (!guildId) return null;

    try {
      const config = deepClone(DEFAULT_CONFIG);
      await guildQueries.updateConfig(guildId, config);
      await cacheDel(`cfg:${guildId}`);
      await cacheDel(`mod:${guildId}`);

      log.info(`Config réinitialisée pour guild ${guildId}`);
      return config;
    } catch (err) {
      log.error(`Erreur reset config guild ${guildId}:`, err.message);
      return null;
    }
  },

  /**
   * Réinitialise les modules d'une guild aux valeurs par défaut
   *
   * @param {string} guildId
   * @returns {Promise<object>}
   */
  async resetModules(guildId) {
    if (!guildId) return null;

    try {
      const modules = deepClone(DEFAULT_MODULES);
      await guildQueries.updateModules(guildId, modules);
      await cacheSet(`mod:${guildId}`, modules);

      log.info(`Modules réinitialisés pour guild ${guildId}`);
      return modules;
    } catch (err) {
      log.error(`Erreur reset modules guild ${guildId}:`, err.message);
      return null;
    }
  },

  /**
   * Invalide le cache d'une guild
   * Utile après une modification directe en DB (phpMyAdmin)
   *
   * @param {string} guildId
   */
  async invalidate(guildId) {
    await cacheDel(`cfg:${guildId}`);
    await cacheDel(`mod:${guildId}`);
    log.debug(`Cache invalidé pour guild ${guildId}`);
  },

  /**
   * Vide tout le cache (toutes les guilds)
   * Utile en cas de migration ou modification massive
   */
  async flushAll() {
    const stats = localCache.getStats();
    localCache.flushAll();
    // Aussi vider Redis si disponible
    if (redisAvailable) {
      try { await redis.delPattern('cfg:*', 0); await redis.delPattern('mod:*', 0); } catch { /* non-bloquant */ }
    }
    log.info(`Cache vidé (${stats.keys} clés supprimées)`);
  },

  /**
   * Retourne les statistiques du cache
   * Utile pour le monitoring multi-serveur
   */
  getCacheStats() {
    return {
      ...localCache.getStats(),
      keys: localCache.keys().length,
      redisAvailable,
    };
  },

  /** Config par défaut (lecture seule) */
  DEFAULT_CONFIG: Object.freeze(deepClone(DEFAULT_CONFIG)),

  /** Modules par défaut (lecture seule) */
  DEFAULT_MODULES: Object.freeze(deepClone(DEFAULT_MODULES)),

  /** Liste des noms de modules disponibles */
  AVAILABLE_MODULES: Object.freeze(Object.keys(DEFAULT_MODULES)),
};

module.exports = configService;