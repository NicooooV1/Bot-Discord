// ===================================
// Ultra Suite — Config Service
// Cache mémoire + DB pour config guild
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

const configService = {
  /**
   * Récupère la config complète d'une guild (cache → DB)
   */
  async get(guildId) {
    const cached = cache.get(`guild:${guildId}`);
    if (cached) return cached;

    const guild = await guildQueries.getOrCreate(guildId, 'Unknown', '0');
    const config = { ...DEFAULT_CONFIG, ...guild.config };
    cache.set(`guild:${guildId}`, config);
    return config;
  },

  /**
   * Met à jour la config et invalide le cache
   */
  async set(guildId, patch) {
    const merged = await guildQueries.updateConfig(guildId, patch);
    if (!merged) return null;
    const full = { ...DEFAULT_CONFIG, ...merged };
    cache.set(`guild:${guildId}`, full);
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
   * Modules activés
   */
  async getModules(guildId) {
    const guild = await guildQueries.getOrCreate(guildId, 'Unknown', '0');
    return guild.modules_enabled;
  },

  /**
   * Active/désactive un module
   */
  async setModule(guildId, moduleName, enabled) {
    const modules = await guildQueries.updateModules(guildId, { [moduleName]: enabled });
    cache.del(`guild:${guildId}`);
    log.info(`Module ${moduleName} ${enabled ? 'enabled' : 'disabled'} for ${guildId}`);
    return modules;
  },

  /**
   * Vérifie si un module est activé
   */
  async isModuleEnabled(guildId, moduleName) {
    return guildQueries.isModuleEnabled(guildId, moduleName);
  },

  /**
   * Invalide le cache d'une guild
   */
  invalidate(guildId) {
    cache.del(`guild:${guildId}`);
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
