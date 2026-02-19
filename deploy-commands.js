// ===================================
// Ultra Suite — deploy-commands.js
// Enregistre les slash commands Discord
// ===================================

require('dotenv').config();

const { REST, Routes } = require('discord.js');
const fs = require('fs');
const path = require('path');
const logger = require('./core/logger');

const commands = [];
const commandsPath = path.join(__dirname, 'commands');

function loadCommandsRecursive(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      loadCommandsRecursive(fullPath);
    } else if (entry.name.endsWith('.js')) {
      try {
        const cmd = require(fullPath);
        if (cmd?.data?.toJSON) {
          commands.push(cmd.data.toJSON());
          logger.info(`[Deploy] Chargé : ${cmd.data.name}`);
        }
      } catch (err) {
        logger.error(`[Deploy] Erreur chargement ${fullPath}: ${err.message}`);
      }
    }
  }
}

loadCommandsRecursive(commandsPath);

const rest = new REST({ version: '10' }).setToken(process.env.BOT_TOKEN);

(async () => {
  try {
    logger.info(`[Deploy] Enregistrement de ${commands.length} commandes...`);

    if (process.env.GUILD_ID) {
      // Deploy sur un serveur spécifique (instantané, pour le dev)
      await rest.put(Routes.applicationGuildCommands(process.env.CLIENT_ID, process.env.GUILD_ID), {
        body: commands,
      });
      logger.info(`[Deploy] ✅ ${commands.length} commandes enregistrées sur le serveur ${process.env.GUILD_ID}`);
    } else {
      // Deploy global (peut prendre ~1h pour se propager)
      await rest.put(Routes.applicationCommands(process.env.CLIENT_ID), {
        body: commands,
      });
      logger.info(`[Deploy] ✅ ${commands.length} commandes enregistrées globalement`);
    }
  } catch (err) {
    logger.error(`[Deploy] Erreur : ${err.message}`);
    console.error(err);
    process.exit(1);
  }
})();
