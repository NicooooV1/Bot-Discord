// ===================================
// Ultra Suite — Guild Queries
// Requêtes DB pour la config par serveur
//
// Chaque serveur Discord (guild) a sa propre
// ligne dans la table `guilds` avec :
//   - config JSON (paramètres du bot)
//   - modules_enabled JSON (modules activés)
//   - theme JSON (couleurs embeds)
//   - locale (fr/en)
//
// Optimisé multi-serveur :
//   - Batch operations
//   - Error handling robuste
//   - Nettoyage cascade
//   - Export/Import avec versioning
// ===================================

const { getDb, transaction } = require('./index');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('GuildQueries');

// ===================================
// Constantes
// ===================================

/** Tables qui ont une colonne guild_id (pour le nettoyage complet) */
const GUILD_DATA_TABLES = [
  'logs', 'temp_voice_channels', 'reminders', 'rp_inventory',
  'rp_characters', 'custom_commands', 'server_events', 'applications',
  'security_signals', 'automod_filters', 'mod_notes', 'role_menus',
  'shop_items', 'tags', 'transactions', 'tickets', 'sanctions', 'users',
];

/** Config par défaut pour une nouvelle guild */
const DEFAULT_CONFIG = {};

/** Modules par défaut */
const DEFAULT_MODULES = {};

/** Thème par défaut */
const DEFAULT_THEME = { primary: '#5865F2' };

/** Version du format d'export */
const EXPORT_VERSION = '2.2';

// ===================================
// Helpers
// ===================================

/**
 * Deep merge récursif : fusionne les objets imbriqués
 * sans écraser les sous-objets entiers.
 *
 * @param {object} target - Objet de base
 * @param {object} source - Objet à fusionner
 * @returns {object} Nouvel objet fusionné
 */
function deepMerge(target, source) {
  if (!source) return target;
  const output = { ...target };
  for (const key of Object.keys(source)) {
    if (
      source[key] !== null &&
      source[key] !== undefined &&
      typeof source[key] === 'object' &&
      !Array.isArray(source[key]) &&
      target[key] &&
      typeof target[key] === 'object' &&
      !Array.isArray(target[key])
    ) {
      output[key] = deepMerge(target[key], source[key]);
    } else {
      output[key] = source[key];
    }
  }
  return output;
}

/**
 * Parse JSON sécurisé (protection contre les données corrompues en DB)
 *
 * @param {string|object} str - Valeur à parser
 * @param {*} fallback - Valeur par défaut si parsing échoue
 * @returns {*}
 */
function safeJsonParse(str, fallback = {}) {
  if (!str) return fallback;
  if (typeof str === 'object') return str;
  try {
    return JSON.parse(str);
  } catch (err) {
    log.warn(`JSON corrompu en DB: ${err.message} — fallback utilisé`);
    return fallback;
  }
}

/**
 * Parse une ligne guild brute de la DB en objet exploitable.
 *
 * @param {object|null} guild - Ligne brute de la table guilds
 * @returns {object|null}
 */
function parseGuildRow(guild) {
  if (!guild) return null;
  return {
    ...guild,
    config: safeJsonParse(guild.config, DEFAULT_CONFIG),
    modules_enabled: safeJsonParse(guild.modules_enabled, DEFAULT_MODULES),
    theme: safeJsonParse(guild.theme, DEFAULT_THEME),
  };
}

/**
 * Valide un ID Discord (snowflake).
 *
 * @param {string} id
 * @returns {boolean}
 */
function isValidSnowflake(id) {
  return typeof id === 'string' && /^\d{17,20}$/.test(id);
}

// ===================================
// Requêtes
// ===================================
const guildQueries = {

  // ===================================
  // CRUD de base
  // ===================================

  /**
   * Récupère une guild par ID, ou la crée si elle n'existe pas.
   * Si elle existe déjà, met à jour le nom et l'owner_id
   * (au cas où ils auraient changé depuis la dernière visite).
   *
   * @param {string} guildId - Discord guild ID (snowflake)
   * @param {string} [name='Unknown'] - Nom actuel du serveur
   * @param {string} [ownerId='0'] - ID du propriétaire actuel
   * @returns {Promise<object>}
   */
  async getOrCreate(guildId, name = 'Unknown', ownerId = '0') {
    if (!guildId) throw new Error('guildId requis');

    const db = getDb();
    let guild = await db('guilds').where('id', guildId).first();

    if (!guild) {
      // Nouvelle guild → insérer avec les valeurs par défaut
      log.info(`Nouvelle guild enregistrée : ${name} (${guildId})`);
      await db('guilds').insert({
        id: guildId,
        name: name || 'Unknown',
        owner_id: ownerId || '0',
        config: JSON.stringify(DEFAULT_CONFIG),
        modules_enabled: JSON.stringify(DEFAULT_MODULES),
        theme: JSON.stringify(DEFAULT_THEME),
        locale: process.env.DEFAULT_LOCALE || 'fr',
      });
      guild = await db('guilds').where('id', guildId).first();
    } else {
      // Guild existante → mettre à jour le nom et l'owner si changé
      const needsUpdate =
        (name && name !== 'Unknown' && guild.name !== name) ||
        (ownerId && ownerId !== '0' && guild.owner_id !== ownerId);

      if (needsUpdate) {
        const patch = { updated_at: db.fn.now() };
        if (name && name !== 'Unknown') patch.name = name;
        if (ownerId && ownerId !== '0') patch.owner_id = ownerId;
        await db('guilds').where('id', guildId).update(patch);
        guild = { ...guild, ...patch };
      }
    }

    return parseGuildRow(guild);
  },

  /**
   * Récupère une guild par ID (sans créer).
   *
   * @param {string} guildId
   * @returns {Promise<object|null>}
   */
  async get(guildId) {
    if (!guildId) return null;
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();
    return parseGuildRow(guild);
  },

  /**
   * Récupère plusieurs guilds par IDs (batch).
   * Plus efficace que des appels individuels.
   *
   * @param {string[]} guildIds - Liste d'IDs
   * @returns {Promise<Map<string, object>>} Map<guildId, guildData>
   */
  async getMultiple(guildIds) {
    if (!guildIds || guildIds.length === 0) return new Map();

    const db = getDb();
    const guilds = await db('guilds').whereIn('id', guildIds);
    const map = new Map();
    for (const guild of guilds) {
      map.set(guild.id, parseGuildRow(guild));
    }
    return map;
  },

  /**
   * Liste toutes les guilds enregistrées.
   * Utile pour le scheduler et les tâches multi-serveur.
   *
   * @param {object} [options]
   * @param {string[]} [options.select] - Colonnes à sélectionner (défaut: toutes)
   * @param {number} [options.limit] - Nombre max de résultats
   * @returns {Promise<object[]>}
   */
  async getAll(options = {}) {
    const db = getDb();
    let query = db('guilds');

    if (options.select) {
      query = query.select(options.select);
    } else {
      query = query.select('*');
    }

    if (options.limit) {
      query = query.limit(options.limit);
    }

    const guilds = await query;
    return guilds.map(parseGuildRow);
  },

  /**
   * Liste les IDs de toutes les guilds.
   * Version légère pour les boucles de maintenance.
   *
   * @returns {Promise<string[]>}
   */
  async getAllIds() {
    const db = getDb();
    const rows = await db('guilds').select('id');
    return rows.map((r) => r.id);
  },

  /**
   * Compte le nombre de guilds enregistrées.
   *
   * @returns {Promise<number>}
   */
  async count() {
    const db = getDb();
    const row = await db('guilds').count('* as cnt').first();
    return row?.cnt || 0;
  },

  /**
   * Vérifie si une guild existe en DB.
   *
   * @param {string} guildId
   * @returns {Promise<boolean>}
   */
  async exists(guildId) {
    if (!guildId) return false;
    const db = getDb();
    const row = await db('guilds').where('id', guildId).select('id').first();
    return !!row;
  },

  // ===================================
  // Configuration
  // ===================================

  /**
   * Met à jour la config d'une guild (deep merge).
   * Ne remplace pas les sous-objets entiers.
   *
   * @param {string} guildId
   * @param {object} configPatch - Clés à modifier
   * @returns {Promise<object|null>} Config complète après merge
   */
  async updateConfig(guildId, configPatch) {
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();

    if (!guild) {
      log.warn(`updateConfig: guild ${guildId} introuvable — création automatique`);
      await this.getOrCreate(guildId);
      return this.updateConfig(guildId, configPatch);
    }

    const current = safeJsonParse(guild.config, {});
    const merged = deepMerge(current, configPatch);

    await db('guilds').where('id', guildId).update({
      config: JSON.stringify(merged),
      updated_at: db.fn.now(),
    });

    return merged;
  },

  /**
   * Récupère une clé spécifique de la config (accès rapide).
   *
   * @param {string} guildId
   * @param {string} key - Clé de config (supporte dot notation: 'moderation.logChannel')
   * @param {*} [defaultValue] - Valeur par défaut si la clé n'existe pas
   * @returns {Promise<*>}
   */
  async getConfigValue(guildId, key, defaultValue = undefined) {
    const guild = await this.get(guildId);
    if (!guild) return defaultValue;

    const keys = key.split('.');
    let value = guild.config;
    for (const k of keys) {
      if (value === null || value === undefined || typeof value !== 'object') {
        return defaultValue;
      }
      value = value[k];
    }

    return value !== undefined ? value : defaultValue;
  },

  /**
   * Remplace entièrement la config d'une guild.
   * ⚠️ Opération destructive — utilisé uniquement pour reset.
   *
   * @param {string} guildId
   * @param {object} config - Config complète
   * @returns {Promise<object|null>}
   */
  async replaceConfig(guildId, config) {
    const db = getDb();
    const exists = await db('guilds').where('id', guildId).first();
    if (!exists) return null;

    await db('guilds').where('id', guildId).update({
      config: JSON.stringify(config),
      updated_at: db.fn.now(),
    });

    return config;
  },

  // ===================================
  // Modules
  // ===================================

  /**
   * Met à jour les modules activés (merge simple).
   *
   * @param {string} guildId
   * @param {object} modulesPatch - { moduleName: boolean }
   * @returns {Promise<object|null>}
   */
  async updateModules(guildId, modulesPatch) {
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();

    if (!guild) {
      log.warn(`updateModules: guild ${guildId} introuvable — création automatique`);
      await this.getOrCreate(guildId);
      return this.updateModules(guildId, modulesPatch);
    }

    const current = safeJsonParse(guild.modules_enabled, {});
    const merged = { ...current, ...modulesPatch };

    await db('guilds').where('id', guildId).update({
      modules_enabled: JSON.stringify(merged),
      updated_at: db.fn.now(),
    });

    return merged;
  },

  /**
   * Vérifie si un module est activé pour une guild.
   *
   * @param {string} guildId
   * @param {string} moduleName
   * @returns {Promise<boolean>}
   */
  async isModuleEnabled(guildId, moduleName) {
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();
    if (!guild) return false;
    const modules = safeJsonParse(guild.modules_enabled, {});
    return modules[moduleName] === true;
  },

  /**
   * Vérifie plusieurs modules d'un coup.
   *
   * @param {string} guildId
   * @param {string[]} moduleNames
   * @returns {Promise<object>} { moduleName: boolean }
   */
  async getModulesStatus(guildId, moduleNames) {
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();
    const modules = guild ? safeJsonParse(guild.modules_enabled, {}) : {};

    const result = {};
    for (const name of moduleNames) {
      result[name] = modules[name] === true;
    }
    return result;
  },

  // ===================================
  // Thème & Locale
  // ===================================

  /**
   * Met à jour le thème (couleurs embeds) d'une guild.
   *
   * @param {string} guildId
   * @param {object} themePatch
   * @returns {Promise<object|null>}
   */
  async updateTheme(guildId, themePatch) {
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();
    if (!guild) return null;

    const current = safeJsonParse(guild.theme, DEFAULT_THEME);
    const merged = { ...current, ...themePatch };

    await db('guilds').where('id', guildId).update({
      theme: JSON.stringify(merged),
      updated_at: db.fn.now(),
    });

    return merged;
  },

  /**
   * Met à jour la locale d'une guild.
   *
   * @param {string} guildId
   * @param {string} locale - 'fr', 'en', etc.
   * @returns {Promise<void>}
   */
  async setLocale(guildId, locale) {
    const db = getDb();
    await db('guilds').where('id', guildId).update({
      locale,
      updated_at: db.fn.now(),
    });
  },

  // ===================================
  // Suppression & Nettoyage
  // ===================================

  /**
   * Supprime une guild et TOUTES ses données de la DB.
   * Utilise CASCADE via les FK, mais vérifie aussi les tables orphelines.
   * Appelé quand le bot quitte un serveur (guildDelete event).
   *
   * @param {string} guildId
   * @returns {Promise<boolean>}
   */
  async delete(guildId) {
    if (!guildId) return false;

    const db = getDb();

    try {
      const deleted = await db('guilds').where('id', guildId).del();
      if (deleted) {
        log.info(`Guild supprimée de la DB : ${guildId} (cascade activée)`);
      }
      return deleted > 0;
    } catch (err) {
      log.error(`Erreur suppression guild ${guildId}: ${err.message}`);
      // Fallback : suppression manuelle table par table
      return this.forceDelete(guildId);
    }
  },

  /**
   * Suppression forcée : nettoie toutes les tables manuellement.
   * Utilisé en fallback si le CASCADE échoue.
   *
   * @param {string} guildId
   * @returns {Promise<boolean>}
   */
  async forceDelete(guildId) {
    if (!guildId) return false;

    const db = getDb();
    let totalDeleted = 0;

    try {
      await transaction(async (trx) => {
        for (const table of GUILD_DATA_TABLES) {
          const hasTable = await trx.schema.hasTable(table);
          if (hasTable) {
            const count = await trx(table).where('guild_id', guildId).del();
            totalDeleted += count;
          }
        }
        // Supprimer la guild elle-même
        await trx('guilds').where('id', guildId).del();
      });

      log.info(`Guild ${guildId} supprimée (force delete: ${totalDeleted} lignes nettoyées)`);
      return true;
    } catch (err) {
      log.error(`Erreur force delete guild ${guildId}: ${err.message}`);
      return false;
    }
  },

  /**
   * Purge les guilds qui ne sont plus dans le cache Discord.
   * Utile pour nettoyer les guilds d'où le bot a été retiré.
   *
   * @param {Set<string>} activeGuildIds - IDs des guilds actives
   * @returns {Promise<{ purged: number, ids: string[] }>}
   */
  async purgeInactive(activeGuildIds) {
    const db = getDb();
    const allGuilds = await db('guilds').select('id', 'name');
    const toPurge = allGuilds.filter((g) => !activeGuildIds.has(g.id));

    if (toPurge.length === 0) {
      return { purged: 0, ids: [] };
    }

    const ids = toPurge.map((g) => g.id);

    // Suppression batch
    const deleted = await db('guilds').whereIn('id', ids).del();

    log.info(`${deleted} guild(s) inactive(s) purgée(s) : ${toPurge.map(g => g.name || g.id).join(', ')}`);
    return { purged: deleted, ids };
  },

  // ===================================
  // Export / Import
  // ===================================

  /**
   * Export la config complète d'une guild (pour backup/transfert).
   *
   * @param {string} guildId
   * @returns {Promise<object|null>}
   */
  async exportConfig(guildId) {
    const guild = await this.get(guildId);
    if (!guild) return null;

    return {
      version: EXPORT_VERSION,
      exported_at: new Date().toISOString(),
      guild_id: guildId,
      config: guild.config,
      modules_enabled: guild.modules_enabled,
      theme: guild.theme,
      locale: guild.locale,
    };
  },

  /**
   * Export complet d'une guild incluant toutes les données.
   * Utile pour les sauvegardes complètes.
   *
   * @param {string} guildId
   * @returns {Promise<object|null>}
   */
  async exportFull(guildId) {
    const guild = await this.get(guildId);
    if (!guild) return null;

    const db = getDb();
    const data = { ...await this.exportConfig(guildId), tables: {} };

    for (const table of GUILD_DATA_TABLES) {
      try {
        const hasTable = await db.schema.hasTable(table);
        if (hasTable) {
          const rows = await db(table).where('guild_id', guildId);
          if (rows.length > 0) {
            data.tables[table] = rows;
          }
        }
      } catch {
        // Table peut ne pas avoir guild_id (ex: rp_inventory)
      }
    }

    return data;
  },

  /**
   * Import une config dans une guild.
   *
   * @param {string} guildId
   * @param {object} data - Objet exporté par exportConfig
   * @returns {Promise<boolean>}
   */
  async importConfig(guildId, data) {
    if (!data?.config) return false;

    const db = getDb();
    const exists = await db('guilds').where('id', guildId).first();
    if (!exists) {
      await this.getOrCreate(guildId);
    }

    const patch = {
      config: JSON.stringify(data.config),
      updated_at: db.fn.now(),
    };

    if (data.modules_enabled) {
      patch.modules_enabled = JSON.stringify(data.modules_enabled);
    }
    if (data.theme) {
      patch.theme = JSON.stringify(data.theme);
    }
    if (data.locale) {
      patch.locale = data.locale;
    }

    await db('guilds').where('id', guildId).update(patch);
    log.info(`Config importée pour guild ${guildId}`);
    return true;
  },

  // ===================================
  // Statistiques par guild
  // ===================================

  /**
   * Retourne des statistiques détaillées pour une guild.
   * Utile pour la commande /stats.
   *
   * @param {string} guildId
   * @returns {Promise<object>}
   */
  async getGuildStats(guildId) {
    const db = getDb();
    const stats = {};

    const tablesToCount = [
      { key: 'users', table: 'users' },
      { key: 'sanctions', table: 'sanctions' },
      { key: 'tickets', table: 'tickets' },
      { key: 'tags', table: 'tags' },
      { key: 'custom_commands', table: 'custom_commands' },
      { key: 'events', table: 'server_events' },
      { key: 'applications', table: 'applications' },
    ];

    for (const { key, table } of tablesToCount) {
      try {
        const hasTable = await db.schema.hasTable(table);
        if (hasTable) {
          const [{ cnt }] = await db(table).where('guild_id', guildId).count('* as cnt');
          stats[key] = cnt;
        } else {
          stats[key] = 0;
        }
      } catch {
        stats[key] = 0;
      }
    }

    return stats;
  },
};

// ===================================
// Exports
// ===================================

module.exports = guildQueries;

// Exporter aussi les helpers pour les tests
module.exports.helpers = { deepMerge, safeJsonParse, parseGuildRow, isValidSnowflake };