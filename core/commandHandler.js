// ===================================
// Ultra Suite — Command Handler
// Charge récursivement src/commands/**
// ===================================

const fs = require('fs');
const path = require('path');
const { Collection } = require('discord.js');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('CommandHandler');

/**
 * Charge toutes les commandes depuis src/commands/
 * Structure attendue : src/commands/<module>/<command>.js
 * Chaque fichier exporte { data, module, execute }
 *
 * @param {import('discord.js').Client} client
 * @returns {Collection<string, object>}
 */
function loadCommands(client) {
  client.commands = new Collection();
  client.cooldowns = new Collection();

  const commandsDir = path.join(__dirname, '..', 'commands');
  if (!fs.existsSync(commandsDir)) {
    log.warn('Commands directory not found');
    return client.commands;
  }

  const modules = fs.readdirSync(commandsDir).filter((f) =>
    fs.statSync(path.join(commandsDir, f)).isDirectory()
  );

  let total = 0;

  for (const moduleName of modules) {
    const moduleDir = path.join(commandsDir, moduleName);
    const files = fs.readdirSync(moduleDir).filter((f) => f.endsWith('.js'));

    for (const file of files) {
      try {
        const command = require(path.join(moduleDir, file));

        if (!command.data || !command.execute) {
          log.warn(`Skipping ${moduleName}/${file}: missing data or execute`);
          continue;
        }

        // Attache le nom du module à la commande
        command.module = command.module || moduleName;

        client.commands.set(command.data.name, command);
        total++;
      } catch (err) {
        log.error(`Failed to load command ${moduleName}/${file}:`, err);
      }
    }
  }

  log.info(`Loaded ${total} commands from ${modules.length} modules`);
  return client.commands;
}

module.exports = { loadCommands };
