// ===================================
// Ultra Suite — Config Engine
// Moteur de configuration par module
//
// Gère :
// - Lecture/écriture de config par module
// - Validation via les schémas du registry
// - Migration des anciennes clés flat → module
// - Historique des changements
// - Export/import de config
// ===================================

const { createModuleLogger } = require('./logger');
const moduleRegistry = require('./moduleRegistry');

const log = createModuleLogger('ConfigEngine');

// ===================================
// Mapping ancien config flat → module
// Pour migration transparente
// ===================================
const LEGACY_KEY_MAP = {
  // Logs
  logChannel: { module: 'logs', key: 'logChannel' },
  // Modération
  modLogChannel: { module: 'moderation', key: 'modLogChannel' },
  muteRole: { module: 'moderation', key: 'muteRole' },
  // Onboarding
  welcomeChannel: { module: 'onboarding', key: 'welcomeChannel' },
  welcomeMessage: { module: 'onboarding', key: 'welcomeMessage' },
  welcomeRole: { module: 'onboarding', key: 'welcomeRole' },
  goodbyeChannel: { module: 'onboarding', key: 'goodbyeChannel' },
  goodbyeMessage: { module: 'onboarding', key: 'goodbyeMessage' },
  // Tickets
  ticketCategory: { module: 'tickets', key: 'ticketCategory' },
  ticketLogChannel: { module: 'tickets', key: 'ticketLogChannel' },
  ticketStaffRole: { module: 'tickets', key: 'ticketStaffRole' },
  maxTicketsPerUser: { module: 'tickets', key: 'maxTicketsPerUser' },
  // Tempvoice
  tempVoiceCategory: { module: 'tempvoice', key: 'tempVoiceCategory' },
  tempVoiceLobby: { module: 'tempvoice', key: 'tempVoiceLobby' },
};

// Sous-objets de l'ancien config → modules
const LEGACY_OBJECT_MAP = {
  automod: {
    module: 'security',
    keyMap: {
      antiSpam: 'antiSpam',
      antiLink: 'antiLink',
      antiMention: 'antiMention',
      maxWarns: 'maxWarns',
      warnAction: 'warnAction',
      warnActionDuration: 'warnActionDuration',
    },
  },
  xp: {
    module: 'xp',
    keyMap: {
      min: 'min',
      max: 'max',
      cooldown: 'cooldown',
      levelUpChannel: 'levelUpChannel',
      levelUpMessage: 'levelUpMessage',
      roleRewards: 'roleRewards',
    },
  },
  economy: {
    module: 'economy',
    keyMap: {
      currencyName: 'currencyName',
      currencySymbol: 'currencySymbol',
      dailyAmount: 'dailyAmount',
      weeklyAmount: 'weeklyAmount',
    },
  },
  antiRaid: {
    module: 'security',
    keyMap: {
      joinThreshold: 'joinThreshold',
      joinWindow: 'joinWindow',
      action: 'raidAction',
    },
  },
};

// ===================================
// Migration de l'ancien format flat vers le nouveau
// ===================================

/**
 * Migre un objet config ancien format vers le nouveau format par module.
 *
 * @param {object} legacyConfig - Config ancienne au format flat
 * @returns {{ global: object, modules: object }}
 *   global: { locale, prefix }
 *   modules: { moduleId: { key: value } }
 */
function migrateLegacyConfig(legacyConfig) {
  if (!legacyConfig) return { global: {}, modules: {} };

  const global = {};
  const modules = {};

  // Clés globales (non liées à un module)
  if (legacyConfig.prefix) global.prefix = legacyConfig.prefix;
  if (legacyConfig.locale) global.locale = legacyConfig.locale;

  // Migrer les clés flat
  for (const [legacyKey, mapping] of Object.entries(LEGACY_KEY_MAP)) {
    if (legacyConfig[legacyKey] !== null && legacyConfig[legacyKey] !== undefined) {
      if (!modules[mapping.module]) modules[mapping.module] = {};
      modules[mapping.module][mapping.key] = legacyConfig[legacyKey];
    }
  }

  // Migrer les sous-objets
  for (const [legacyKey, mapping] of Object.entries(LEGACY_OBJECT_MAP)) {
    const obj = legacyConfig[legacyKey];
    if (obj && typeof obj === 'object') {
      if (!modules[mapping.module]) modules[mapping.module] = {};
      for (const [oldKey, newKey] of Object.entries(mapping.keyMap)) {
        if (obj[oldKey] !== null && obj[oldKey] !== undefined) {
          modules[mapping.module][newKey] = obj[oldKey];
        }
      }
    }
  }

  // Migrer roleMenus → roles module
  if (legacyConfig.roleMenus && Array.isArray(legacyConfig.roleMenus)) {
    if (!modules.roles) modules.roles = {};
    modules.roles.roleMenus = legacyConfig.roleMenus;
  }

  return { global, modules };
}

// ===================================
// Récupération de la config d'un module
// ===================================

/**
 * Retourne la config d'un module pour une guild.
 * Applique les defaults du schema sur les clés manquantes.
 *
 * @param {object} guildConfig - Config complète de la guild { modules: { moduleId: { ... } } }
 * @param {string} moduleId
 * @returns {object} Config du module avec defaults appliqués
 */
function getModuleConfig(guildConfig, moduleId) {
  const defaults = moduleRegistry.getDefaultConfig(moduleId);
  const stored = guildConfig?.modules?.[moduleId] || {};

  // Merge : stored surcharge defaults
  return { ...defaults, ...stored };
}

/**
 * Retourne une clé de config d'un module.
 *
 * @param {object} guildConfig
 * @param {string} moduleId
 * @param {string} key
 * @returns {*} Valeur ou default
 */
function getModuleConfigValue(guildConfig, moduleId, key) {
  const config = getModuleConfig(guildConfig, moduleId);
  return config[key];
}

// ===================================
// Écriture & validation
// ===================================

/**
 * Valide et applique une modification de config module.
 *
 * @param {object} guildConfig - Config complète de la guild (mutée en place)
 * @param {string} moduleId
 * @param {string} key
 * @param {*} value
 * @returns {{ success: boolean, error?: string }}
 */
function setModuleConfigValue(guildConfig, moduleId, key, value) {
  // Valider via le registry
  const validation = moduleRegistry.validateConfigValue(moduleId, key, value);
  if (!validation.valid) {
    return { success: false, error: validation.error };
  }

  // Initialiser la structure si nécessaire
  if (!guildConfig.modules) guildConfig.modules = {};
  if (!guildConfig.modules[moduleId]) guildConfig.modules[moduleId] = {};

  guildConfig.modules[moduleId][key] = value;
  return { success: true };
}

/**
 * Applique plusieurs modifications de config en batch (à valider individuellement).
 *
 * @param {object} guildConfig
 * @param {string} moduleId
 * @param {object} values - { key: value }
 * @returns {{ success: boolean, errors: object }}
 */
function setModuleConfigBatch(guildConfig, moduleId, values) {
  const errors = {};
  let hasErrors = false;

  for (const [key, value] of Object.entries(values)) {
    const result = setModuleConfigValue(guildConfig, moduleId, key, value);
    if (!result.success) {
      errors[key] = result.error;
      hasErrors = true;
    }
  }

  return { success: !hasErrors, errors };
}

/**
 * Reset la config d'un module à ses valeurs par défaut.
 *
 * @param {object} guildConfig
 * @param {string} moduleId
 */
function resetModuleConfig(guildConfig, moduleId) {
  if (guildConfig.modules) {
    guildConfig.modules[moduleId] = {};
  }
}

// ===================================
// État du module
// ===================================

/**
 * Calcule l'état d'un module pour l'affichage.
 *
 * @param {string} moduleId
 * @param {object} guildConfig
 * @param {object} enabledModules - { moduleId: boolean }
 * @returns {{ state: string, stateLabel: string, stateEmoji: string, missing: Array }}
 */
function getModuleStatus(moduleId, guildConfig, enabledModules) {
  const isEnabled = enabledModules?.[moduleId] || false;
  const moduleConfig = getModuleConfig(guildConfig, moduleId);
  const state = moduleRegistry.getModuleState(moduleId, moduleConfig, isEnabled);
  const missing = moduleRegistry.getMissingRequired(moduleId, moduleConfig);

  const stateMap = {
    DISABLED: { label: 'Désactivé', emoji: '⚫' },
    ENABLED_UNCONFIGURED: { label: 'Activé (non configuré)', emoji: '🟡' },
    ACTIVE: { label: 'Actif', emoji: '🟢' },
  };

  const { label, emoji } = stateMap[state] || stateMap.DISABLED;

  return {
    state,
    stateLabel: label,
    stateEmoji: emoji,
    missing,
  };
}

// ===================================
// Export / Import
// ===================================

/**
 * Exporte la config complète d'une guild dans un format portable.
 *
 * @param {object} guildConfig
 * @param {object} enabledModules
 * @returns {object} Config exportable
 */
function exportConfig(guildConfig, enabledModules) {
  return {
    version: 2,
    exportedAt: new Date().toISOString(),
    global: guildConfig.global || {},
    modules: guildConfig.modules || {},
    enabled: enabledModules || {},
  };
}

/**
 * Valide un objet d'import de config.
 *
 * @param {object} imported
 * @returns {{ valid: boolean, errors: string[] }}
 */
function validateImport(imported) {
  const errors = [];

  if (!imported || typeof imported !== 'object') {
    errors.push('Format invalide');
    return { valid: false, errors };
  }

  if (imported.version !== 2) {
    errors.push('Version de config incompatible');
  }

  if (imported.modules && typeof imported.modules === 'object') {
    for (const [moduleId, moduleConfig] of Object.entries(imported.modules)) {
      if (!moduleRegistry.has(moduleId)) {
        errors.push(`Module inconnu: ${moduleId}`);
        continue;
      }
      for (const [key, value] of Object.entries(moduleConfig)) {
        const result = moduleRegistry.validateConfigValue(moduleId, key, value);
        if (!result.valid) {
          errors.push(`${moduleId}.${key}: ${result.error}`);
        }
      }
    }
  }

  return { valid: errors.length === 0, errors };
}

// ===================================
// Historique (pour audit log)
// ===================================

/**
 * Crée un enregistrement de changement pour l'audit log.
 *
 * @param {string} moduleId
 * @param {string} key
 * @param {*} oldValue
 * @param {*} newValue
 * @param {string} userId - ID de l'utilisateur qui a fait le changement
 * @returns {object}
 */
function createChangeRecord(moduleId, key, oldValue, newValue, userId) {
  return {
    moduleId,
    key,
    oldValue: typeof oldValue === 'object' ? JSON.stringify(oldValue) : String(oldValue ?? ''),
    newValue: typeof newValue === 'object' ? JSON.stringify(newValue) : String(newValue ?? ''),
    changedBy: userId,
    changedAt: new Date().toISOString(),
  };
}

// ===================================
// Exports
// ===================================

module.exports = {
  // Migration
  migrateLegacyConfig,
  LEGACY_KEY_MAP,
  LEGACY_OBJECT_MAP,

  // Lecture
  getModuleConfig,
  getModuleConfigValue,

  // Écriture
  setModuleConfigValue,
  setModuleConfigBatch,
  resetModuleConfig,

  // État
  getModuleStatus,

  // Export/Import
  exportConfig,
  validateImport,

  // Audit
  createChangeRecord,
};
