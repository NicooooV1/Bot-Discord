// ===================================
// Ultra Suite — Config Service
// Cache mémoire + DB pour config guild
// Données séparées par serveur (guild_id)
// ===================================

const NodeCache = require('node-cache');
const guildQueries = require('../database/guildQueries');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('ConfigService');

// TTL 5 minutes — refresh automatique
const cache = new NodeCache({ stdTTL: 300, checkperiod: 60, useClones: true });

// Valeurs par défaut pour la config d'une guild
const DEFAULT_CONFIG = {
  prefix: '!',
  locale: 'fr',
  // Logs
  logChannel: null,
  modLogChannel: null,
  // Modération
  automod: { enabled: false, antiSpam: false, antiLink: false, antiMention: false, maxWarns: 5, warnAction: 'TIMEOUT', warnActionDuration: 3600 },
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
  xp: { enabled: false, min: 15, max: 25, cooldown: 60, levelUpChannel: null, levelUpMessage: null, roleRewards: {} },
  // Economy
  economy: { enabled: false, currencyName: '💰', currencySymbol: '$', dailyAmount: 100, weeklyAmount: 500 },
  // Voices
  tempVoiceCategory: null,
  tempVoiceLobby: null,
  // Security
  antiRaid: { enabled: false, joinThreshold: 10, joinWindow: 10, action: 'kick' },
  // Roles
  roleMenus: [],
  // Muted role
  muteRole: null,
};

/**
 * Charge la guild depuis la DB et met en cache config + modules
 * @param {string} guildId
 * @returns {{ config: object, modules: object }}
 */
async function loadGuild(guildId) {
  const guild = await guildQueries.getOrCreate(guildId, 'Unknown', '0');
  const config = { ...DEFAULT_CONFIG, ...guild.config };
  const modules = guild.modules_enabled || {};

  cache.set(`cfg:${guildId}`, config);
  cache.set(`mod:${guildId}`, modules);

  return { config, modules };
}

const configService = {
  /**
   * Récupère la config complète d'une guild (cache → DB)
   */
  async get(guildId) {
    const cached = cache.get(`cfg:${guildId}`);
    if (cached) return cached;

    const { config } = await loadGuild(guildId);
    return config;
  },

  /**
   * Met à jour la config et invalide le cache
   */
  async set(guildId, patch) {
    const merged = await guildQueries.updateConfig(guildId, patch);
    if (!merged) return null;
    const full = { ...DEFAULT_CONFIG, ...merged };
    cache.set(`cfg:${guildId}`, full);
    log.info(`Config updated for guild ${guildId}`, { keys: Object.keys(patch) });
    return full;
  },

  /**
   * Récupère une clé spécifique
   */
  async getKey(guildId, key) {
    const config = await this.get(guildId);
    return config[key];
  },

  /**
   * Modules activés (cache → DB)
   */
  async getModules(guildId) {
    const cached = cache.get(`mod:${guildId}`);
    if (cached) return cached;

    const { modules } = await loadGuild(guildId);
    return modules;
  },

  /**
   * Active/désactive un module
   */
  async setModule(guildId, moduleName, enabled) {
    const modules = await guildQueries.updateModules(guildId, { [moduleName]: enabled });
    cache.set(`mod:${guildId}`, modules);
    cache.del(`cfg:${guildId}`); // invalider la config aussi
    log.info(`Module ${moduleName} ${enabled ? 'enabled' : 'disabled'} for ${guildId}`);
    return modules;
  },

  /**
   * Vérifie si un module est activé (cache → DB)
   */
  async isModuleEnabled(guildId, moduleName) {
    const modules = await this.getModules(guildId);
    return modules[moduleName] === true;
  },

  /**
   * Invalide le cache d'une guild
   */
  invalidate(guildId) {
    cache.del(`cfg:${guildId}`);
    cache.del(`mod:${guildId}`);
  },

  /**
   * Vide tout le cache
   */
  flushAll() {
    cache.flushAll();
  },

  /** Constante : config par défaut */
  DEFAULT_CONFIG,
};

module.exports = configService;
