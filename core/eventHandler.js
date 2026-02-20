// ===================================
// Ultra Suite — Event Handler
// Charge et enregistre les événements Discord
//
// Responsabilités :
// 1. Charger tous les events depuis events/
// 2. Filtrer les fichiers .legacy (non chargés)
// 3. Enregistrer avec client.on() ou client.once()
// 4. Wrapper chaque event dans un try/catch
//    (un event crashé ne tue pas le bot)
// ===================================

const fs = require('fs');
const path = require('path');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('EventHandler');

/**
 * Charge tous les événements Discord depuis events/
 *
 * Structure attendue d'un event :
 * {
 *   name: 'guildMemberAdd',           // Nom de l'événement Discord
 *   once: false,                       // true = client.once, false = client.on
 *   module: 'onboarding',             // Module requis (optionnel, pour filtrer)
 *   execute: async (client, ...args) => { ... }
 *                  OU
 *   execute: async (...args) => { ... }  // Le client sera passé en bind
 * }
 *
 * @param {import('discord.js').Client} client
 */
function loadEvents(client) {
  const eventsDir = path.join(__dirname, '..', 'events');

  if (!fs.existsSync(eventsDir)) {
    log.warn(`Répertoire events/ introuvable : ${eventsDir}`);
    return;
  }

  let loaded = 0;
  let skipped = 0;
  let errors = 0;
  const eventMap = new Map(); // name → [fichiers] pour détecter les conflits

  function loadRecursive(dir) {
    const entries = fs.readdirSync(dir, { withFileTypes: true });

    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);

      if (entry.isDirectory()) {
        loadRecursive(fullPath);
        continue;
      }

      // Ignorer les fichiers non-JS
      if (!entry.name.endsWith('.js')) continue;

      // Ignorer les fichiers .legacy
      if (entry.name.includes('.legacy')) {
        skipped++;
        log.debug(`Ignoré (legacy) : ${path.relative(eventsDir, fullPath)}`);
        continue;
      }

      try {
        // Nettoyer le cache pour le hot-reload
        delete require.cache[require.resolve(fullPath)];
        const event = require(fullPath);

        if (!event?.name || typeof event.execute !== 'function') {
          log.warn(`Ignoré (structure invalide) : ${path.relative(eventsDir, fullPath)}`);
          skipped++;
          continue;
        }

        const relativePath = path.relative(eventsDir, fullPath);

        // Tracking des fichiers par événement (info)
        if (!eventMap.has(event.name)) {
          eventMap.set(event.name, []);
        }
        eventMap.get(event.name).push(relativePath);

        // Wrapper l'exécution dans un try/catch
        // pour qu'un event crashé ne tue pas le bot
        const wrappedExecute = async (...args) => {
          try {
            await event.execute(...args);
          } catch (err) {
            log.error(
              `Erreur dans event "${event.name}" (${relativePath}): ${err.message}`
            );
            log.error(err.stack);
          }
        };

        // Enregistrer l'événement
        if (event.once) {
          client.once(event.name, wrappedExecute);
        } else {
          client.on(event.name, wrappedExecute);
        }

        loaded++;
      } catch (err) {
        errors++;
        log.error(
          `Erreur chargement ${path.relative(eventsDir, fullPath)}: ${err.message}`
        );
      }
    }
  }

  loadRecursive(eventsDir);

  // Résumé par événement
  const eventNames = [...eventMap.keys()].sort();
  log.info(
    `${loaded} événement(s) chargé(s) : ${eventNames.join(', ')}`
  );

  // Avertir si plusieurs fichiers écoutent le même événement
  for (const [eventName, files] of eventMap) {
    if (files.length > 1) {
      log.info(
        `Event "${eventName}" a ${files.length} listeners : ${files.join(', ')}`
      );
    }
  }

  if (skipped > 0) {
    log.info(`${skipped} fichier(s) ignoré(s) (legacy/invalide)`);
  }
  if (errors > 0) {
    log.error(`${errors} erreur(s) de chargement`);
  }

  // Vérifier les événements critiques manquants
  const criticalEvents = [
    'interactionCreate', // Dispatch des commandes (géré par commandHandler)
    'guildCreate',       // Nouvelle guild → init en DB
    'guildDelete',       // Guild quittée → cleanup
  ];

  // Note: interactionCreate est géré par commandHandler,
  // donc il ne sera peut-être pas dans events/
  const missingCritical = criticalEvents.filter(
    (e) => !eventMap.has(e) && e !== 'interactionCreate'
  );

  if (missingCritical.length > 0) {
    log.warn(
      `Événements critiques manquants : ${missingCritical.join(', ')} — ` +
      'Créez-les dans events/ pour le multi-serveur'
    );
  }
}

module.exports = { loadEvents };