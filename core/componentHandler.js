// ===================================
// Ultra Suite — Component Handler
// Charge et dispatch les interactions de composants
// (boutons, select menus, modals)
//
// Supporte 2 modes de routing :
// 1. Exact match  : customId === handler.customId
// 2. Prefix match : customId.startsWith(handler.prefix)
//    → Utile pour les IDs dynamiques (ticket-close-123, role-menu-456)
//
// Structure d'un handler :
// {
//   customId: 'ticket-close',     // Exact match
//   // OU
//   prefix: 'ticket-',            // Prefix match (priorité plus basse)
//   type: 'button',               // 'button' | 'select' | 'modal' | 'any'
//   module: 'tickets',            // Module requis (optionnel)
//   execute: async (interaction) => { ... }
// }
// ===================================

const { Collection, Events } = require('discord.js');
const fs = require('fs');
const path = require('path');
const configService = require('./configService');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('ComponentHandler');

/**
 * Charge tous les handlers de composants depuis components/
 * Attache client.components (Collection) et client.componentPrefixes (Array)
 *
 * @param {import('discord.js').Client} client
 */
function loadComponents(client) {
  // Exact match handlers
  client.components = new Collection();
  // Prefix match handlers (testés en fallback)
  client.componentPrefixes = [];

  const componentsDir = path.join(__dirname, '..', 'components');

  if (!fs.existsSync(componentsDir)) {
    log.info('Répertoire components/ introuvable — aucun composant chargé');
    registerComponentListener(client);
    return;
  }

  let loaded = 0;
  let errors = 0;

  function loadRecursive(dir) {
    const entries = fs.readdirSync(dir, { withFileTypes: true });

    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);

      if (entry.isDirectory()) {
        loadRecursive(fullPath);
        continue;
      }

      if (!entry.name.endsWith('.js')) continue;
      if (entry.name.includes('.legacy')) continue;

      try {
        delete require.cache[require.resolve(fullPath)];
        const handler = require(fullPath);

        if (typeof handler.execute !== 'function') {
          log.warn(`Ignoré (pas de execute) : ${path.relative(componentsDir, fullPath)}`);
          continue;
        }

        // Stocker le chemin source (debug)
        handler._sourcePath = path.relative(componentsDir, fullPath);

        // Type par défaut
        if (!handler.type) handler.type = 'any';

        if (handler.customId) {
          // Mode exact match
          client.components.set(handler.customId, handler);
          loaded++;
        } else if (handler.prefix) {
          // Mode prefix match
          client.componentPrefixes.push(handler);
          loaded++;
        } else {
          log.warn(`Ignoré (ni customId ni prefix) : ${handler._sourcePath}`);
        }
      } catch (err) {
        errors++;
        log.error(`Erreur chargement ${path.relative(componentsDir, fullPath)}: ${err.message}`);
      }
    }
  }

  loadRecursive(componentsDir);

  // Trier les prefix handlers par longueur décroissante
  // (le plus spécifique d'abord)
  client.componentPrefixes.sort((a, b) => b.prefix.length - a.prefix.length);

  log.info(
    `${loaded} composant(s) chargé(s) ` +
    `(${client.components.size} exact, ${client.componentPrefixes.length} prefix)`
  );

  if (errors > 0) {
    log.error(`${errors} erreur(s) de chargement`);
  }

  registerComponentListener(client);
}

/**
 * Résout le handler approprié pour un customId donné
 *
 * @param {import('discord.js').Client} client
 * @param {string} customId
 * @returns {object|null}
 */
function resolveHandler(client, customId) {
  // 1. Exact match (prioritaire)
  const exact = client.components.get(customId);
  if (exact) return exact;

  // 2. Prefix match (fallback)
  for (const handler of client.componentPrefixes) {
    if (customId.startsWith(handler.prefix)) {
      return handler;
    }
  }

  return null;
}

/**
 * Vérifie que le type d'interaction correspond au handler
 *
 * @param {import('discord.js').BaseInteraction} interaction
 * @param {string} type
 * @returns {boolean}
 */
function matchesType(interaction, type) {
  if (type === 'any') return true;
  if (type === 'button' && interaction.isButton()) return true;
  if (type === 'select' && interaction.isAnySelectMenu()) return true;
  if (type === 'modal' && interaction.isModalSubmit()) return true;
  return false;
}

/**
 * Enregistre le listener pour les interactions de composants
 *
 * @param {import('discord.js').Client} client
 */
function registerComponentListener(client) {
  client.on(Events.InteractionCreate, async (interaction) => {
    // Ne traiter que les composants (pas les slash commands)
    const isComponent =
      interaction.isButton() ||
      interaction.isAnySelectMenu() ||
      interaction.isModalSubmit();

    if (!isComponent) return;

    const customId = interaction.customId;
    if (!customId) return;

    // Résoudre le handler
    const handler = resolveHandler(client, customId);

    if (!handler) {
      // Pas de handler trouvé — c'est normal pour les composants
      // gérés inline par les commandes (collectors)
      log.debug(`Aucun handler pour customId: ${customId}`);
      return;
    }

    // Vérifier le type
    if (!matchesType(interaction, handler.type)) {
      log.debug(
        `Type mismatch pour ${customId}: attendu ${handler.type}, ` +
        `reçu ${interaction.type}`
      );
      return;
    }

    // Vérifier le module
    if (handler.module && handler.module !== 'admin') {
      const guildId = interaction.guildId;
      if (guildId) {
        const enabled = await configService.isModuleEnabled(guildId, handler.module);
        if (!enabled) {
          return interaction.reply({
            content: `❌ Le module **${handler.module}** est désactivé sur ce serveur.`,
            ephemeral: true,
          }).catch(() => {});
        }
      }
    }

    // Exécuter avec try/catch
    try {
      await handler.execute(interaction);
    } catch (err) {
      log.error(
        `Erreur composant "${customId}" (${handler._sourcePath || 'unknown'}): ${err.message}`
      );
      log.error(err.stack);

      // Répondre à l'utilisateur
      const errorMessage = {
        content: '❌ Une erreur est survenue avec cette interaction.',
        ephemeral: true,
      };

      try {
        if (interaction.replied || interaction.deferred) {
          await interaction.followUp(errorMessage);
        } else {
          await interaction.reply(errorMessage);
        }
      } catch {
        // Interaction expirée — rien à faire
      }
    }
  });
}

module.exports = { loadComponents };