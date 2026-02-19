// ===================================
// Ultra Suite — Event Handler
// Charge récursivement src/events/**
// ===================================

const fs = require('fs');
const path = require('path');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('EventHandler');

/**
 * Charge tous les événements depuis src/events/
 * Chaque fichier exporte { name, once?, execute }
 *
 * @param {import('discord.js').Client} client
 */
function loadEvents(client) {
  const eventsDir = path.join(__dirname, '..', 'events');
  if (!fs.existsSync(eventsDir)) {
    log.warn('Events directory not found');
    return;
  }

  const files = getAllFiles(eventsDir).filter((f) => f.endsWith('.js'));
  let total = 0;

  for (const file of files) {
    try {
      const event = require(file);

      if (!event.name || !event.execute) {
        log.warn(`Skipping ${path.relative(eventsDir, file)}: missing name or execute`);
        continue;
      }

      if (event.once) {
        client.once(event.name, (...args) => event.execute(...args, client));
      } else {
        client.on(event.name, (...args) => event.execute(...args, client));
      }

      total++;
    } catch (err) {
      log.error(`Failed to load event ${path.basename(file)}:`, err);
    }
  }

  log.info(`Loaded ${total} events`);
}

/**
 * Parcours récursif des fichiers
 */
function getAllFiles(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...getAllFiles(fullPath));
    } else {
      files.push(fullPath);
    }
  }
  return files;
}

module.exports = { loadEvents };
