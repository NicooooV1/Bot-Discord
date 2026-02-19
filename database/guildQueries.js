// ===================================
// Ultra Suite — Guild Queries
// ===================================

const { getDb } = require('./index');

/**
 * Deep merge : fusionne récursivement les objets imbriqués
 * au lieu d'écraser les sous-objets entiers
 */
function deepMerge(target, source) {
  const output = { ...target };
  for (const key of Object.keys(source)) {
    if (
      source[key] &&
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

const guildQueries = {
  /**
   * Récupère ou crée une guild
   */
  async getOrCreate(guildId, name, ownerId) {
    const db = getDb();
    let guild = await db('guilds').where('id', guildId).first();
    if (!guild) {
      await db('guilds').insert({
        id: guildId,
        name,
        owner_id: ownerId,
        config: JSON.stringify({}),
        modules_enabled: JSON.stringify({}),
        theme: JSON.stringify({ primary: '#5865F2' }),
        locale: 'fr',
      });
      guild = await db('guilds').where('id', guildId).first();
    }
    return {
      ...guild,
      config: JSON.parse(guild.config || '{}'),
      modules_enabled: JSON.parse(guild.modules_enabled || '{}'),
      theme: JSON.parse(guild.theme || '{}'),
    };
  },

  /**
   * Met à jour la config d'une guild (merge)
   */
  async updateConfig(guildId, configPatch) {
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();
    if (!guild) return null;
    const current = JSON.parse(guild.config || '{}');
    const merged = deepMerge(current, configPatch);
    await db('guilds').where('id', guildId).update({
      config: JSON.stringify(merged),
      updated_at: db.fn.now(),
    });
    return merged;
  },

  /**
   * Met à jour les modules activés
   */
  async updateModules(guildId, modulesPatch) {
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();
    if (!guild) return null;
    const current = JSON.parse(guild.modules_enabled || '{}');
    const merged = { ...current, ...modulesPatch };
    await db('guilds').where('id', guildId).update({
      modules_enabled: JSON.stringify(merged),
      updated_at: db.fn.now(),
    });
    return merged;
  },

  /**
   * Vérifie si un module est activé
   */
  async isModuleEnabled(guildId, moduleName) {
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();
    if (!guild) return false;
    const modules = JSON.parse(guild.modules_enabled || '{}');
    return modules[moduleName] === true;
  },

  /**
   * Met à jour le thème
   */
  async updateTheme(guildId, themePatch) {
    const db = getDb();
    const guild = await db('guilds').where('id', guildId).first();
    if (!guild) return null;
    const current = JSON.parse(guild.theme || '{}');
    const merged = { ...current, ...themePatch };
    await db('guilds').where('id', guildId).update({
      theme: JSON.stringify(merged),
      updated_at: db.fn.now(),
    });
    return merged;
  },
};

module.exports = guildQueries;
