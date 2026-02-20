// ===================================
// Ultra Suite — deploy-commands.js
// Enregistre les slash commands Discord
//
// Usage :
//   npm run deploy              → Global (multi-serveur, ~1h propagation)
//   npm run deploy:dev          → Dev sur GUILD_ID (instantané)
//   npm run deploy:clean        → Supprime les commandes du GUILD_ID (nettoyage)
//   npm run deploy:clean-global → Supprime les commandes globales
//
// En multi-serveur : TOUJOURS déployer en global (sans GUILD_ID)
// Le GUILD_ID ne sert qu'au développement/test
// ===================================

require('dotenv').config();

const { REST, Routes } = require('discord.js');
const fs = require('fs');
const path = require('path');
const { logger } = require('./core/logger');

// ===================================
// Validation
// ===================================
if (!process.env.BOT_TOKEN) {
  logger.error('[Deploy] BOT_TOKEN manquant dans .env');
  process.exit(1);
}
if (!process.env.CLIENT_ID) {
  logger.error('[Deploy] CLIENT_ID manquant dans .env');
  process.exit(1);
}

// ===================================
// Mode de déploiement
// ===================================
const args = process.argv.slice(2);
const MODE_CLEAN = args.includes('--clean');
const MODE_CLEAN_GLOBAL = args.includes('--clean-global');
const MODE_DEV = args.includes('--dev') || (!!process.env.GUILD_ID && !args.includes('--global'));

// ===================================
// Chargement récursif des commandes
// ===================================
const commandMap = new Map();
const duplicates = [];
let loadErrors = 0;

const commandsPath = path.join(__dirname, 'commands');

function loadCommandsRecursive(dir) {
  if (!fs.existsSync(dir)) {
    logger.error(`[Deploy] Répertoire introuvable : ${dir}`);
    process.exit(1);
  }

  const entries = fs.readdirSync(dir, { withFileTypes: true });

  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);

    if (entry.isDirectory()) {
      loadCommandsRecursive(fullPath);
      continue;
    }

    if (!entry.name.endsWith('.js')) continue;

    try {
      // Nettoyer le cache require pour éviter les problèmes de rechargement
      delete require.cache[require.resolve(fullPath)];
      const cmd = require(fullPath);

      if (!cmd?.data?.toJSON) {
        logger.warn(`[Deploy] Ignoré (pas de data.toJSON) : ${path.relative(commandsPath, fullPath)}`);
        continue;
      }

      const name = cmd.data.name;

      if (commandMap.has(name)) {
        const existingPath = commandMap.get(name)._sourcePath;
        duplicates.push({
          name,
          file1: existingPath,
          file2: path.relative(commandsPath, fullPath),
        });
        logger.warn(`[Deploy] ⚠️ DOUBLON : /${name}`);
        logger.warn(`         → Fichier 1 : ${existingPath}`);
        logger.warn(`         → Fichier 2 : ${path.relative(commandsPath, fullPath)} (ignoré)`);
        continue;
      }

      const json = cmd.data.toJSON();
      json._sourcePath = path.relative(commandsPath, fullPath);
      commandMap.set(name, json);
    } catch (err) {
      loadErrors++;
      logger.error(`[Deploy] Erreur chargement ${path.relative(commandsPath, fullPath)}: ${err.message}`);
    }
  }
}

loadCommandsRecursive(commandsPath);

// Retirer les métadonnées internes avant envoi à Discord
const commands = [...commandMap.values()].map((cmd) => {
  const clean = { ...cmd };
  delete clean._sourcePath;
  return clean;
});

// ===================================
// Résumé du chargement
// ===================================
logger.info('');
logger.info('══════════════════════════════════════');
logger.info(`  Commandes chargées : ${commands.length}`);
if (duplicates.length > 0) {
  logger.warn(`  Doublons ignorés   : ${duplicates.length}`);
}
if (loadErrors > 0) {
  logger.error(`  Erreurs chargement : ${loadErrors}`);
}
logger.info('══════════════════════════════════════');
logger.info('');

// Lister les commandes par module
const byModule = new Map();
for (const [name, json] of commandMap) {
  const modulePath = json._sourcePath?.split(path.sep)[0] || 'unknown';
  if (!byModule.has(modulePath)) byModule.set(modulePath, []);
  byModule.get(modulePath).push(name);
}

for (const [mod, cmds] of byModule) {
  logger.info(`  📦 ${mod}: ${cmds.map((c) => `/${c}`).join(', ')}`);
}
logger.info('');

// ===================================
// Déploiement
// ===================================
const rest = new REST({ version: '10' }).setToken(process.env.BOT_TOKEN);

(async () => {
  try {
    // === Mode nettoyage guild ===
    if (MODE_CLEAN) {
      if (!process.env.GUILD_ID) {
        logger.error('[Deploy] --clean nécessite GUILD_ID dans .env');
        process.exit(1);
      }
      logger.info(`[Deploy] 🧹 Suppression des commandes sur le serveur ${process.env.GUILD_ID}...`);
      await rest.put(
        Routes.applicationGuildCommands(process.env.CLIENT_ID, process.env.GUILD_ID),
        { body: [] }
      );
      logger.info('[Deploy] ✅ Commandes guild supprimées.');
      return;
    }

    // === Mode nettoyage global ===
    if (MODE_CLEAN_GLOBAL) {
      logger.info('[Deploy] 🧹 Suppression des commandes globales...');
      await rest.put(Routes.applicationCommands(process.env.CLIENT_ID), { body: [] });
      logger.info('[Deploy] ✅ Commandes globales supprimées.');
      return;
    }

    // === Mode dev (guild spécifique — instantané) ===
    if (MODE_DEV && process.env.GUILD_ID) {
      logger.info(`[Deploy] 🔧 Mode DEV — Déploiement sur le serveur ${process.env.GUILD_ID}`);
      logger.info(`[Deploy] Enregistrement de ${commands.length} commandes...`);

      const result = await rest.put(
        Routes.applicationGuildCommands(process.env.CLIENT_ID, process.env.GUILD_ID),
        { body: commands }
      );

      logger.info(`[Deploy] ✅ ${result.length} commandes enregistrées sur le serveur de dev.`);
      logger.info('[Deploy] ℹ️  Les commandes sont disponibles immédiatement.');
      logger.info('[Deploy] ⚠️  Pour le multi-serveur, lancez : npm run deploy:global');
      return;
    }

    // === Mode global (multi-serveur — production) ===
    logger.info('[Deploy] 🌍 Mode GLOBAL — Déploiement multi-serveur');
    logger.info(`[Deploy] Enregistrement de ${commands.length} commandes...`);

    const result = await rest.put(
      Routes.applicationCommands(process.env.CLIENT_ID),
      { body: commands }
    );

    logger.info(`[Deploy] ✅ ${result.length} commandes enregistrées globalement.`);
    logger.info('[Deploy] ℹ️  Propagation sur tous les serveurs : ~1 heure.');

    // Conseil : nettoyer les commandes guild si elles existent
    if (process.env.GUILD_ID) {
      logger.info('');
      logger.info('[Deploy] 💡 Conseil : nettoyez les commandes du serveur de dev :');
      logger.info('[Deploy]    npm run deploy:clean');
      logger.info('[Deploy]    (sinon les commandes apparaîtront en double sur ce serveur)');
    }
  } catch (err) {
    logger.error(`[Deploy] ❌ Erreur : ${err.message}`);

    if (err.status === 401) {
      logger.error('[Deploy] Token invalide. Vérifiez BOT_TOKEN dans .env');
    } else if (err.status === 403) {
      logger.error('[Deploy] Permissions insuffisantes. Vérifiez que le bot a le scope "applications.commands".');
    } else if (err.status === 404) {
      logger.error('[Deploy] CLIENT_ID ou GUILD_ID invalide.');
    }

    console.error(err);
    process.exit(1);
  }
})();