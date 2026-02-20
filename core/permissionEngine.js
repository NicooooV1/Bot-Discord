// ===================================
// Ultra Suite — Permission Engine
// Moteur universel de permissions
//
// Évalue les droits par :
//   - Module (qui peut utiliser un module dans quel salon)
//   - Commande (override par commande)
//   - Rôles autorisés / refusés
//   - Salons autorisés / refusés
//   - Permissions Discord requises
//   - Contraintes contextuelles (DM, threads, cooldown)
//
// Priorité : Deny > Allow > Default
// ===================================

const { PermissionsBitField } = require('discord.js');
const { createModuleLogger } = require('./logger');
const moduleRegistry = require('./moduleRegistry');

const log = createModuleLogger('PermissionEngine');

// ===================================
// Structures de permission
// ===================================

/**
 * Crée une règle de permission par défaut.
 *
 * @returns {object} Permission rule
 */
function createDefaultRule() {
  return {
    allowedRoles: [],      // IDs — si non vide, seuls ces rôles peuvent
    deniedRoles: [],       // IDs — ces rôles ne peuvent jamais
    allowedChannels: [],   // IDs — si non vide, seulement dans ces salons
    deniedChannels: [],    // IDs — jamais dans ces salons
    requiredPermissions: [], // Permissions Discord requises (en plus de celles du manifest)
    allowDM: false,          // Autorisé en DM ?
    allowThreads: true,      // Autorisé dans les threads ?
    cooldown: 0,             // Cooldown custom en secondes (0 = default)
  };
}

// ===================================
// Évaluation des permissions
// ===================================

/**
 * Évalue si un membre peut exécuter une commande d'un module.
 *
 * @param {object} context
 * @param {import('discord.js').GuildMember} context.member - Le membre exécutant
 * @param {import('discord.js').Channel} context.channel - Le salon
 * @param {string} context.moduleId - ID du module
 * @param {string} [context.commandName] - Nom de la commande (pour override)
 * @param {object} [context.permissionRules] - Règles de la guild { module: { ... }, commands: { cmdName: { ... } } }
 * @returns {{ allowed: boolean, reason?: string }}
 */
function evaluate(context) {
  const { member, channel, moduleId, commandName, permissionRules } = context;

  // Bypass total pour admins/owner
  if (member.permissions.has(PermissionsBitField.Flags.Administrator)) {
    return { allowed: true };
  }

  // Le module "admin" n'a pas de restrictions
  if (moduleId === 'admin') {
    return { allowed: true };
  }

  // Récupérer le manifest du module
  const manifest = moduleRegistry.get(moduleId);

  // Vérifier les permissions Discord requises par le manifest
  if (manifest?.requiredPermissions?.length) {
    for (const perm of manifest.requiredPermissions) {
      // On vérifie les perms du BOT, pas du membre
      // Les perms du manifest sont celles que le bot a besoin
    }
  }

  // Règles de la guild
  const rules = permissionRules || {};

  // 1. Vérifier les règles au niveau du module
  const moduleRule = rules.modules?.[moduleId];
  if (moduleRule) {
    const moduleCheck = evaluateRule(moduleRule, member, channel);
    if (!moduleCheck.allowed) return moduleCheck;
  }

  // 2. Vérifier les règles au niveau de la commande (override)
  if (commandName && rules.commands?.[commandName]) {
    const cmdRule = rules.commands[commandName];
    const cmdCheck = evaluateRule(cmdRule, member, channel);
    if (!cmdCheck.allowed) return cmdCheck;
  }

  return { allowed: true };
}

/**
 * Évalue une règle de permission individuelle.
 *
 * @param {object} rule
 * @param {import('discord.js').GuildMember} member
 * @param {import('discord.js').Channel} channel
 * @returns {{ allowed: boolean, reason?: string }}
 */
function evaluateRule(rule, member, channel) {
  // DENY a toujours priorité

  // Rôles refusés
  if (rule.deniedRoles?.length) {
    for (const roleId of rule.deniedRoles) {
      if (member.roles.cache.has(roleId)) {
        return { allowed: false, reason: 'Rôle non autorisé' };
      }
    }
  }

  // Salons refusés
  if (rule.deniedChannels?.length) {
    if (rule.deniedChannels.includes(channel.id)) {
      return { allowed: false, reason: 'Salon non autorisé' };
    }
  }

  // Rôles autorisés (whitelist — si défini, il faut avoir au moins un rôle)
  if (rule.allowedRoles?.length) {
    const hasAllowedRole = rule.allowedRoles.some((roleId) =>
      member.roles.cache.has(roleId)
    );
    if (!hasAllowedRole) {
      return { allowed: false, reason: 'Rôle requis manquant' };
    }
  }

  // Salons autorisés (whitelist — si défini, il faut être dans un salon autorisé)
  if (rule.allowedChannels?.length) {
    if (!rule.allowedChannels.includes(channel.id)) {
      return { allowed: false, reason: 'Commande non autorisée dans ce salon' };
    }
  }

  // Permissions Discord requises (du membre cette fois)
  if (rule.requiredPermissions?.length) {
    for (const perm of rule.requiredPermissions) {
      if (!member.permissions.has(perm)) {
        return { allowed: false, reason: `Permission Discord requise : ${perm}` };
      }
    }
  }

  // Threads
  if (channel.isThread?.() && rule.allowThreads === false) {
    return { allowed: false, reason: 'Non autorisé dans les threads' };
  }

  return { allowed: true };
}

// ===================================
// Helpers pour la config UI
// ===================================

/**
 * Récupère les règles de permission d'une guild.
 *
 * @param {object} guildConfig - Config complète de la guild
 * @returns {object} { modules: { ... }, commands: { ... } }
 */
function getGuildPermissionRules(guildConfig) {
  return guildConfig?.permissions || { modules: {}, commands: {} };
}

/**
 * Met à jour les règles d'un module.
 *
 * @param {object} guildConfig
 * @param {string} moduleId
 * @param {object} rule - Objet partiel de règle
 */
function setModulePermissionRule(guildConfig, moduleId, rule) {
  if (!guildConfig.permissions) guildConfig.permissions = { modules: {}, commands: {} };
  if (!guildConfig.permissions.modules) guildConfig.permissions.modules = {};

  guildConfig.permissions.modules[moduleId] = {
    ...createDefaultRule(),
    ...(guildConfig.permissions.modules[moduleId] || {}),
    ...rule,
  };
}

/**
 * Met à jour les règles d'une commande.
 *
 * @param {object} guildConfig
 * @param {string} commandName
 * @param {object} rule
 */
function setCommandPermissionRule(guildConfig, commandName, rule) {
  if (!guildConfig.permissions) guildConfig.permissions = { modules: {}, commands: {} };
  if (!guildConfig.permissions.commands) guildConfig.permissions.commands = {};

  guildConfig.permissions.commands[commandName] = {
    ...createDefaultRule(),
    ...(guildConfig.permissions.commands[commandName] || {}),
    ...rule,
  };
}

/**
 * Supprime les règles d'un module.
 *
 * @param {object} guildConfig
 * @param {string} moduleId
 */
function resetModulePermissions(guildConfig, moduleId) {
  if (guildConfig.permissions?.modules) {
    delete guildConfig.permissions.modules[moduleId];
  }
}

/**
 * Supprime les règles d'une commande.
 *
 * @param {object} guildConfig
 * @param {string} commandName
 */
function resetCommandPermissions(guildConfig, commandName) {
  if (guildConfig.permissions?.commands) {
    delete guildConfig.permissions.commands[commandName];
  }
}

// ===================================
// Cooldown Manager (in-memory)
// ===================================

/** @type {Map<string, number>} key = `userId:commandName`, value = timestamp */
const cooldowns = new Map();

/**
 * Vérifie et applique le cooldown pour un utilisateur.
 *
 * @param {string} userId
 * @param {string} commandName
 * @param {number} cooldownSeconds
 * @returns {{ allowed: boolean, remaining?: number }}
 */
function checkCooldown(userId, commandName, cooldownSeconds) {
  if (!cooldownSeconds || cooldownSeconds <= 0) return { allowed: true };

  const key = `${userId}:${commandName}`;
  const now = Date.now();
  const expires = cooldowns.get(key);

  if (expires && now < expires) {
    const remaining = Math.ceil((expires - now) / 1000);
    return { allowed: false, remaining };
  }

  cooldowns.set(key, now + cooldownSeconds * 1000);
  return { allowed: true };
}

/**
 * Purge les cooldowns expirés (appeler périodiquement).
 */
function purgeCooldowns() {
  const now = Date.now();
  for (const [key, expires] of cooldowns) {
    if (now >= expires) cooldowns.delete(key);
  }
}

// ===================================
// Exports
// ===================================

module.exports = {
  createDefaultRule,
  evaluate,
  evaluateRule,

  // Guild config helpers
  getGuildPermissionRules,
  setModulePermissionRule,
  setCommandPermissionRule,
  resetModulePermissions,
  resetCommandPermissions,

  // Cooldowns
  checkCooldown,
  purgeCooldowns,
};
