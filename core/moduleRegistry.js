// ===================================
// Ultra Suite — Module Registry
// Registre central de tous les modules
//
// Chaque module déclare un "manifest" :
//   id, nom, description, schéma de config,
//   commandes, events, jobs, permissions requises,
//   dépendances inter-modules.
//
// États possibles d'un module :
//   DISABLED → module désactivé (rien ne s'exécute)
//   ENABLED_UNCONFIGURED → activé mais champs requis manquants
//   ACTIVE → activé ET entièrement configuré
// ===================================

const fs = require('fs');
const path = require('path');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('ModuleRegistry');

/** @type {Map<string, object>} */
const modules = new Map();

// ===================================
// Enregistrement
// ===================================

/**
 * Valide et enregistre un manifest de module.
 *
 * @param {object} manifest
 * @returns {object} Le manifest complet (avec defaults appliqués)
 */
function register(manifest) {
  if (!manifest?.id) {
    throw new Error('Module manifest missing required field "id"');
  }
  if (!manifest?.name) {
    throw new Error(`Module "${manifest.id}" missing required field "name"`);
  }

  if (modules.has(manifest.id)) {
    log.warn(`Module "${manifest.id}" déjà enregistré — écrasement`);
  }

  // Appliquer les valeurs par défaut
  const full = {
    category: 'other',
    emoji: '📦',
    description: '',
    dependencies: [],
    requiredPermissions: [],
    configSchema: {},
    commands: [],
    events: [],
    jobs: [],
    ...manifest,
  };

  // Appliquer les defaults dans le schema de config
  for (const [key, schema] of Object.entries(full.configSchema)) {
    full.configSchema[key] = {
      required: false,
      label: key,
      description: '',
      default: null,
      ...schema,
    };
  }

  modules.set(manifest.id, Object.freeze(full));
  return full;
}

/**
 * Charge tous les manifests depuis le dossier `modules/`.
 */
function loadAll() {
  const dir = path.join(__dirname, '..', 'modules');
  if (!fs.existsSync(dir)) {
    log.warn('Dossier modules/ introuvable — aucun manifest chargé');
    return;
  }

  const files = fs.readdirSync(dir).filter(
    (f) => f.endsWith('.js') && f !== 'index.js'
  );

  for (const file of files) {
    try {
      const manifestPath = path.join(dir, file);
      // Supprimer le cache require au cas où (hot reload)
      delete require.cache[require.resolve(manifestPath)];
      const manifest = require(manifestPath);
      register(manifest);
    } catch (err) {
      log.error(`Erreur chargement manifest ${file}: ${err.message}`);
    }
  }

  log.info(`${modules.size} module(s) enregistré(s)`);

  // Valider les dépendances
  for (const [id, mod] of modules) {
    for (const dep of mod.dependencies) {
      if (!modules.has(dep)) {
        log.warn(`Module "${id}" dépend de "${dep}" (non enregistré)`);
      }
    }
  }
}

// ===================================
// Accès
// ===================================

/** Récupère un manifest par ID */
function get(id) {
  return modules.get(id) || null;
}

/** Tous les manifests enregistrés */
function getAll() {
  return [...modules.values()];
}

/** Vérifie si un module est enregistré */
function has(id) {
  return modules.has(id);
}

/** IDs de tous les modules */
function getAllIds() {
  return [...modules.keys()];
}

/** Modules d'une catégorie */
function getByCategory(category) {
  return getAll().filter((m) => m.category === category);
}

/** Catégories uniques */
function getCategories() {
  const cats = new Map();
  for (const m of modules.values()) {
    if (!cats.has(m.category)) {
      cats.set(m.category, []);
    }
    cats.get(m.category).push(m);
  }
  return cats;
}

// ===================================
// État des modules
// ===================================

/** Catégories d'affichage avec labels et emojis */
const CATEGORY_LABELS = {
  management: { label: 'Gestion', emoji: '⚙️' },
  moderation: { label: 'Modération & Sécurité', emoji: '🛡️' },
  community: { label: 'Communauté', emoji: '👥' },
  engagement: { label: 'Engagement', emoji: '🎯' },
  creative: { label: 'Créatif', emoji: '🎨' },
  utility: { label: 'Utilitaires', emoji: '🔧' },
  other: { label: 'Autres', emoji: '📦' },
};

/**
 * Détermine l'état d'un module pour une guild.
 *
 * @param {string} moduleId - ID du module
 * @param {object} moduleConfig - Config du module pour cette guild
 * @param {boolean} isEnabled - Le module est-il activé ?
 * @returns {'DISABLED' | 'ENABLED_UNCONFIGURED' | 'ACTIVE'}
 */
function getModuleState(moduleId, moduleConfig, isEnabled) {
  if (!isEnabled) return 'DISABLED';

  const manifest = modules.get(moduleId);
  if (!manifest) return 'DISABLED';

  // Vérifier les champs requis
  const config = moduleConfig || {};
  for (const [key, schema] of Object.entries(manifest.configSchema)) {
    if (schema.required) {
      const value = config[key];
      if (value === null || value === undefined || value === '') {
        return 'ENABLED_UNCONFIGURED';
      }
    }
  }

  return 'ACTIVE';
}

/**
 * Liste les champs requis non remplis pour un module.
 *
 * @param {string} moduleId
 * @param {object} moduleConfig
 * @returns {Array<{ key: string, label: string, type: string }>}
 */
function getMissingRequired(moduleId, moduleConfig) {
  const manifest = modules.get(moduleId);
  if (!manifest) return [];

  const config = moduleConfig || {};
  const missing = [];

  for (const [key, schema] of Object.entries(manifest.configSchema)) {
    if (schema.required) {
      const value = config[key];
      if (value === null || value === undefined || value === '') {
        missing.push({ key, label: schema.label, type: schema.type });
      }
    }
  }

  return missing;
}

/**
 * Vérifie les permissions Discord manquantes pour un module.
 *
 * @param {string} moduleId
 * @param {import('discord.js').GuildMember} botMember - Le membre bot dans la guild
 * @returns {string[]} Permissions manquantes
 */
function getMissingPermissions(moduleId, botMember) {
  const manifest = modules.get(moduleId);
  if (!manifest) return [];

  return manifest.requiredPermissions.filter(
    (perm) => !botMember.permissions.has(perm)
  );
}

/**
 * Vérifie si toutes les dépendances d'un module sont activées.
 *
 * @param {string} moduleId
 * @param {object} enabledModules - { moduleId: boolean }
 * @returns {{ satisfied: boolean, missing: string[] }}
 */
function checkDependencies(moduleId, enabledModules) {
  const manifest = modules.get(moduleId);
  if (!manifest) return { satisfied: true, missing: [] };

  const missing = manifest.dependencies.filter(
    (dep) => !enabledModules[dep]
  );

  return {
    satisfied: missing.length === 0,
    missing,
  };
}

/**
 * Retourne la config par défaut d'un module (basée sur le schema).
 *
 * @param {string} moduleId
 * @returns {object}
 */
function getDefaultConfig(moduleId) {
  const manifest = modules.get(moduleId);
  if (!manifest) return {};

  const defaults = {};
  for (const [key, schema] of Object.entries(manifest.configSchema)) {
    defaults[key] = schema.default !== undefined ? schema.default : null;
  }
  return defaults;
}

/**
 * Valide une valeur de config par rapport au schéma.
 *
 * @param {string} moduleId
 * @param {string} key - Clé de config
 * @param {*} value - Valeur à valider
 * @returns {{ valid: boolean, error?: string }}
 */
function validateConfigValue(moduleId, key, value) {
  const manifest = modules.get(moduleId);
  if (!manifest) return { valid: false, error: 'Module inconnu' };

  const schema = manifest.configSchema[key];
  if (!schema) return { valid: false, error: `Paramètre "${key}" inconnu pour ce module` };

  // null est OK si le champ n'est pas requis
  if (value === null || value === undefined) {
    if (schema.required) return { valid: false, error: `"${schema.label}" est obligatoire` };
    return { valid: true };
  }

  // Validation par type
  switch (schema.type) {
    case 'channel':
    case 'role':
    case 'user':
      if (typeof value !== 'string' || !/^\d{17,20}$/.test(value)) {
        return { valid: false, error: `"${schema.label}" doit être un ID Discord valide` };
      }
      break;

    case 'string':
      if (typeof value !== 'string') return { valid: false, error: `"${schema.label}" doit être du texte` };
      if (schema.maxLength && value.length > schema.maxLength) {
        return { valid: false, error: `"${schema.label}" max ${schema.maxLength} caractères` };
      }
      if (schema.minLength && value.length < schema.minLength) {
        return { valid: false, error: `"${schema.label}" min ${schema.minLength} caractères` };
      }
      if (schema.regex && !new RegExp(schema.regex).test(value)) {
        return { valid: false, error: `"${schema.label}" format invalide` };
      }
      break;

    case 'integer':
    case 'number': {
      const num = Number(value);
      if (isNaN(num)) return { valid: false, error: `"${schema.label}" doit être un nombre` };
      if (schema.type === 'integer' && !Number.isInteger(num)) {
        return { valid: false, error: `"${schema.label}" doit être un entier` };
      }
      if (schema.min !== undefined && num < schema.min) {
        return { valid: false, error: `"${schema.label}" minimum ${schema.min}` };
      }
      if (schema.max !== undefined && num > schema.max) {
        return { valid: false, error: `"${schema.label}" maximum ${schema.max}` };
      }
      break;
    }

    case 'boolean':
      if (typeof value !== 'boolean') {
        return { valid: false, error: `"${schema.label}" doit être true/false` };
      }
      break;

    case 'enum':
      if (!schema.values?.includes(value)) {
        return { valid: false, error: `"${schema.label}" doit être : ${schema.values.join(', ')}` };
      }
      break;

    case 'json':
      if (typeof value === 'string') {
        try { JSON.parse(value); } catch { return { valid: false, error: `"${schema.label}" JSON invalide` }; }
      }
      break;

    case 'channels':
    case 'roles':
      if (!Array.isArray(value)) {
        return { valid: false, error: `"${schema.label}" doit être une liste` };
      }
      break;

    default:
      break;
  }

  return { valid: true };
}

// ===================================
// Exports
// ===================================

module.exports = {
  register,
  loadAll,
  get,
  getAll,
  has,
  getAllIds,
  getByCategory,
  getCategories,
  CATEGORY_LABELS,

  // État & validation
  getModuleState,
  getMissingRequired,
  getMissingPermissions,
  checkDependencies,
  getDefaultConfig,
  validateConfigValue,
};
