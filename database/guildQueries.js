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
// ===================================

const { getDb } = require('./index');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('GuildQueries');

// ===================================
// Helpers
// ===================================

/**
 * Deep merge récursif : fusionne les objets imbriqués
 * sans écraser les sous-objets entiers
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
 * Parse une ligne guild brute de la DB en objet exploitable
 */
function parseGuildRow(guild) {
  if (!guild) return null;
  return {
    ...guild,
    config: safeJsonParse(guild.config, {}),
    modules_enabled: safeJsonParse(guild.modules_enabled, {}),
    theme: safeJsonParse(guild.theme, { primary: '#5865F2' }),
  };
}

// ===================================
// Requêtes
// ===================================
const guildQueries = {
  /**
   * Récupère une guild par ID, ou la crée si elle n'existe pas.
   * Si elle existe déjà, met à jour le nom et l'owner_id
   * (au cas où ils auraient changé depuis la dernière visite).
   *
   * @param {string} guildId — Discord guild ID (snowflake)
   * @param {string} name — Nom actuel du serveur
   * @param {string} ownerId — ID du propriétaire actuel
   * @returns {Promise<object>}
   */
  async getOrCreate(guildId, name = 'Unknown', ownerId = '0') {
    const db = getDb();
    let guild = await db('guilds').where('id', guildId).first();

    if (!guild) {
      // Nouvelle guild → insérer avec les valeurs par défaut
      log.info(`Nouvelle guild enregistrée : ${name} (${guildId})`);
      await db('guilds').insert({
        id: guildId,
        name: name || 'Unknown',
        owner_id: ownerId || '0',
        config: JSON.stringify({}),
        modules_enabled: JSON.stringify({}),
        theme: JSON.stringify({ primary: '#5865F2' }),
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
   * Récupère une guild par ID (sans créer)
   *
   * @param {string} guildId
   * @returns {Promise<object|null>}
   */
  async get(guildId) {
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();
    return parseGuildRow(guild);
  },

  /**
   * Liste toutes les guilds enregistrées
   * Utile pour le scheduler et les tâches multi-serveur
   *
   * @returns {Promise<object[]>}
   */
  async getAll() {
    const db = getDb();
    const guilds = await db('guilds').select('*');
    return guilds.map(parseGuildRow);
  },

  /**
   * Liste les IDs de toutes les guilds
   * Version légère pour les boucles de maintenance
   *
   * @returns {Promise<string[]>}
   */
  async getAllIds() {
    const db = getDb();
    const rows = await db('guilds').select('id');
    return rows.map((r) => r.id);
  },

  /**
   * Compte le nombre de guilds enregistrées
   *
   * @returns {Promise<number>}
   */
  async count() {
    const db = getDb();
    const row = await db('guilds').count('* as cnt').first();
    return row?.cnt || 0;
  },

  /**
   * Met à jour la config d'une guild (deep merge)
   * Ne remplace pas les sous-objets entiers.
   *
   * @param {string} guildId
   * @param {object} configPatch — Clés à modifier
   * @returns {Promise<object|null>} — Config complète après merge
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
   * Remplace entièrement la config d'une guild
   * ⚠️ Opération destructive — utilisé uniquement pour reset
   *
   * @param {string} guildId
   * @param {object} config — Config complète
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

  /**
   * Met à jour les modules activés (merge simple)
   *
   * @param {string} guildId
   * @param {object} modulesPatch — { moduleName: boolean }
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
   * Vérifie si un module est activé pour une guild
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
   * Met à jour le thème (couleurs embeds) d'une guild
   *
   * @param {string} guildId
   * @param {object} themePatch
   * @returns {Promise<object|null>}
   */
  async updateTheme(guildId, themePatch) {
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();
    if (!guild) return null;

    const current = safeJsonParse(guild.theme, { primary: '#5865F2' });
    const merged = { ...current, ...themePatch };

    await db('guilds').where('id', guildId).update({
      theme: JSON.stringify(merged),
      updated_at: db.fn.now(),
    });

    return merged;
  },

  /**
   * Met à jour la locale d'une guild
   *
   * @param {string} guildId
   * @param {string} locale — 'fr', 'en', etc.
   * @returns {Promise<void>}
   */
  async setLocale(guildId, locale) {
    const db = getDb();
    await db('guilds').where('id', guildId).update({
      locale,
      updated_at: db.fn.now(),
    });
  },

  /**
   * Supprime une guild de la DB
   * Appelé quand le bot quitte un serveur (guildDelete event)
   * Les données liées sont supprimées en CASCADE par MySQL.
   *
   * @param {string} guildId
   * @returns {Promise<boolean>}
   */
  async delete(guildId) {
    const db = getDb();
    const deleted = await db('guilds').where('id', guildId).del();
    if (deleted) {
      log.info(`Guild supprimée de la DB : ${guildId}`);
    }
    return deleted > 0;
  },

  /**
   * Purge les guilds qui ne sont plus dans le cache Discord
   * Utile pour nettoyer les guilds d'où le bot a été retiré
   *
   * @param {Set<string>} activeGuildIds — IDs des guilds actives
   * @returns {Promise<number>} — Nombre de guilds purgées
   */
  async purgeInactive(activeGuildIds) {
    const db = getDb();
    const allGuilds = await db('guilds').select('id');
    let purged = 0;

    for (const guild of allGuilds) {
      if (!activeGuildIds.has(guild.id)) {
        await db('guilds').where('id', guild.id).del();
        purged++;
      }
    }

    if (purged > 0) {
      log.info(`${purged} guild(s) inactive(s) purgée(s) de la DB`);
    }

    return purged;
  },

  /**
   * Export la config d'une guild (pour backup/import)
   *
   * @param {string} guildId
   * @returns {Promise<object|null>}
   */
  async exportConfig(guildId) {
    const guild = await this.get(guildId);
    if (!guild) return null;

    return {
      version: '2.1',
      exported_at: new Date().toISOString(),
      guild_id: guildId,
      config: guild.config,
      modules_enabled: guild.modules_enabled,
      theme: guild.theme,
      locale: guild.locale,
    };
  },

  /**
   * Import une config dans une guild
   *
   * @param {string} guildId
   * @param {object} data — Objet exporté par exportConfig
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
};

module.exports = guildQueries;