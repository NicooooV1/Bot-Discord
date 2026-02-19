// ===================================
// Ultra Suite — Component Handler
// Gère les boutons, selects et modals
// ===================================

const { Collection } = require('discord.js');
const fs = require('fs');
const path = require('path');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('ComponentHandler');

/**
 * Charge les composants depuis src/components/
 * Structure : src/components/<type>/<module>_<action>.js
 * Types : buttons/, selects/, modals/
 *
 * @param {import('discord.js').Client} client
 */
function loadComponents(client) {
  client.buttons = new Collection();
  client.selects = new Collection();
  client.modals = new Collection();

  const componentsDir = path.join(__dirname, '..', 'components');
  if (!fs.existsSync(componentsDir)) {
    fs.mkdirSync(componentsDir, { recursive: true });
    fs.mkdirSync(path.join(componentsDir, 'buttons'), { recursive: true });
    fs.mkdirSync(path.join(componentsDir, 'selects'), { recursive: true });
    fs.mkdirSync(path.join(componentsDir, 'modals'), { recursive: true });
    log.info('Created components directory structure');
    return;
  }

  const types = {
    buttons: client.buttons,
    selects: client.selects,
    modals: client.modals,
  };

  for (const [type, collection] of Object.entries(types)) {
    const dir = path.join(componentsDir, type);
    if (!fs.existsSync(dir)) continue;

    const files = fs.readdirSync(dir).filter((f) => f.endsWith('.js'));
    for (const file of files) {
      try {
        const component = require(path.join(dir, file));
        if (!component.id || !component.execute) {
          log.warn(`Skipping ${type}/${file}: missing id or execute`);
          continue;
        }
        collection.set(component.id, component);
      } catch (err) {
        log.error(`Failed to load component ${type}/${file}:`, err);
      }
    }
    if (files.length) log.info(`Loaded ${files.length} ${type}`);
  }
}

module.exports = { loadComponents };
