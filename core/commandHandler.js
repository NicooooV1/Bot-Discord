// ===================================
// Ultra Suite — Command Handler
// Charge et dispatch les slash commands
//
// Responsabilités :
// 1. Charger toutes les commandes depuis commands/
// 2. Vérifier que le module est activé pour la guild
// 3. Gérer les cooldowns par user × commande × guild
// 4. Logger chaque exécution (audit)
// 5. Gérer les erreurs d'exécution (répondre à l'user)
// ===================================

const { Collection, Events } = require('discord.js');
const fs = require('fs');
const path = require('path');
const configService = require('./configService');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('CommandHandler');

// Cooldowns globaux : Map<commandName, Map<guildId:userId, timestamp>>
const cooldowns = new Collection();

/**
 * Charge toutes les commandes récursivement depuis commands/
 * Attache client.commands (Collection<name, command>)
 *
 * Structure attendue d'une commande :
 * {
 *   data: SlashCommandBuilder,
 *   module: 'moderation',        // Module requis (optionnel, 'admin' si absent)
 *   cooldown: 5,                 // Secondes (optionnel, 3 par défaut)
 *   adminOnly: false,            // Réservée aux admins guild (optionnel)
 *   execute: async (interaction) => { ... }
 * }
 *
 * @param {import('discord.js').Client} client
 */
function loadCommands(client) {
  client.commands = new Collection();

  const commandsDir = path.join(__dirname, '..', 'commands');
  if (!fs.existsSync(commandsDir)) {
    log.warn(`Répertoire commands/ introuvable : ${commandsDir}`);
    return;
  }

  const duplicates = [];
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

      // Ignorer les fichiers non-JS et les fichiers .legacy
      if (!entry.name.endsWith('.js')) continue;
      if (entry.name.includes('.legacy')) {
        log.debug(`Ignoré (legacy) : ${entry.name}`);
        continue;
      }

      try {
        // Nettoyer le cache pour le hot-reload
        delete require.cache[require.resolve(fullPath)];
        const cmd = require(fullPath);

        if (!cmd?.data?.toJSON || typeof cmd.execute !== 'function') {
          log.warn(`Ignoré (structure invalide) : ${path.relative(commandsDir, fullPath)}`);
          continue;
        }

        const name = cmd.data.name;

        // Détection des doublons
        if (client.commands.has(name)) {
          duplicates.push(name);
          log.warn(`Doublon ignoré : /${name} dans ${path.relative(commandsDir, fullPath)}`);
          continue;
        }

        // Déduire le module depuis le dossier parent si non spécifié
        if (!cmd.module) {
          const relPath = path.relative(commandsDir, fullPath);
          const parentDir = relPath.split(path.sep)[0];
          cmd.module = parentDir || 'admin';
        }

        // Cooldown par défaut : 3 secondes
        if (cmd.cooldown === undefined) {
          cmd.cooldown = 3;
        }

        // Stocker le chemin source (debug)
        cmd._sourcePath = path.relative(commandsDir, fullPath);

        client.commands.set(name, cmd);
        loaded++;
      } catch (err) {
        errors++;
        log.error(`Erreur chargement ${path.relative(commandsDir, fullPath)}: ${err.message}`);
      }
    }
  }

  loadRecursive(commandsDir);

  // Résumé
  if (duplicates.length > 0) {
    log.warn(`${duplicates.length} doublon(s) ignoré(s) : ${duplicates.join(', ')}`);
  }
  if (errors > 0) {
    log.error(`${errors} erreur(s) de chargement`);
  }
  log.info(`${loaded} commande(s) chargée(s) depuis ${commandsDir}`);

  // Attacher le listener interactionCreate
  registerInteractionListener(client);
}

/**
 * Enregistre le listener qui dispatch les slash commands
 * @param {import('discord.js').Client} client
 */
function registerInteractionListener(client) {
  client.on(Events.InteractionCreate, async (interaction) => {
    // =======================================
    // Autocomplete (dispatch séparé)
    // =======================================
    if (interaction.isAutocomplete()) {
      const command = client.commands.get(interaction.commandName);
      if (!command || typeof command.autocomplete !== 'function') return;

      try {
        await command.autocomplete(interaction);
      } catch (err) {
        log.error(`Erreur autocomplete /${interaction.commandName}: ${err.message}`);
        try { await interaction.respond([]); } catch { /* expired */ }
      }
      return;
    }

    // Ne traiter que les slash commands et le context menu
    if (!interaction.isChatInputCommand() && !interaction.isContextMenuCommand()) return;

    const command = client.commands.get(interaction.commandName);

    if (!command) {
      log.warn(`Commande inconnue : /${interaction.commandName}`);
      return;
    }

    const guildId = interaction.guildId;
    const userId = interaction.user.id;

    // =======================================
    // 1. Vérifier que le module est activé
    // =======================================
    if (command.module && command.module !== 'admin') {
      const enabled = await configService.isModuleEnabled(guildId, command.module);
      if (!enabled) {
        return interaction.reply({
          content: `❌ Le module **${command.module}** est désactivé sur ce serveur.\nUn administrateur peut l'activer avec \`/module enable ${command.module}\`.`,
          ephemeral: true,
        }).catch(() => {});
      }
    }

    // =======================================
    // 2. Vérifier adminOnly
    // =======================================
    if (command.adminOnly) {
      const member = interaction.member;
      const isAdmin = member?.permissions?.has?.('Administrator') ||
                      member?.id === interaction.guild?.ownerId;
      if (!isAdmin) {
        return interaction.reply({
          content: '❌ Cette commande est réservée aux administrateurs.',
          ephemeral: true,
        }).catch(() => {});
      }
    }

    // =======================================
    // 3. Vérifier le cooldown
    // =======================================
    if (command.cooldown > 0) {
      const cooldownKey = `${interaction.commandName}`;
      if (!cooldowns.has(cooldownKey)) {
        cooldowns.set(cooldownKey, new Collection());
      }

      const now = Date.now();
      const timestamps = cooldowns.get(cooldownKey);
      const cooldownAmount = command.cooldown * 1000;
      const userKey = `${guildId}:${userId}`;

      if (timestamps.has(userKey)) {
        const expirationTime = timestamps.get(userKey) + cooldownAmount;
        if (now < expirationTime) {
          const timeLeft = ((expirationTime - now) / 1000).toFixed(1);
          return interaction.reply({
            content: `⏳ Merci de patienter **${timeLeft}s** avant de réutiliser \`/${interaction.commandName}\`.`,
            ephemeral: true,
          }).catch(() => {});
        }
      }

      timestamps.set(userKey, now);

      // Nettoyer après expiration
      setTimeout(() => timestamps.delete(userKey), cooldownAmount);
    }

    // =======================================
    // 4. Exécuter la commande
    // =======================================
    const startTime = Date.now();

    try {
      await command.execute(interaction);

      const elapsed = Date.now() - startTime;

      // Log audit (debug en fonctionnement normal)
      log.debug(
        `/${interaction.commandName} exécutée par ${interaction.user.tag} ` +
        `dans ${interaction.guild?.name || 'DM'} (${elapsed}ms)`
      );

      // Alerte si la commande est lente (> 2s)
      if (elapsed > 2000) {
        log.warn(
          `/${interaction.commandName} lente (${elapsed}ms) — ` +
          `guild: ${guildId}, user: ${userId}`
        );
      }
    } catch (err) {
      const elapsed = Date.now() - startTime;

      log.error(
        `Erreur /${interaction.commandName} (${elapsed}ms) — ` +
        `guild: ${guildId}, user: ${userId}: ${err.message}`
      );
      log.error(err.stack);

      // Répondre à l'utilisateur avec un message d'erreur
      const errorMessage = {
        content: '❌ Une erreur est survenue lors de l\'exécution de cette commande.',
        ephemeral: true,
      };

      try {
        if (interaction.replied || interaction.deferred) {
          await interaction.followUp(errorMessage);
        } else {
          await interaction.reply(errorMessage);
        }
      } catch {
        // Impossible de répondre — interaction probablement expirée
      }
    }
  });
}

/**
 * Recharge une commande spécifique (hot-reload)
 * Utile en développement
 *
 * @param {import('discord.js').Client} client
 * @param {string} commandName
 * @returns {boolean}
 */
function reloadCommand(client, commandName) {
  const command = client.commands.get(commandName);
  if (!command?._sourcePath) return false;

  const fullPath = path.join(__dirname, '..', 'commands', command._sourcePath);

  try {
    delete require.cache[require.resolve(fullPath)];
    const reloaded = require(fullPath);

    if (!reloaded?.data?.toJSON || typeof reloaded.execute !== 'function') {
      log.error(`Rechargement échoué : /${commandName} structure invalide`);
      return false;
    }

    reloaded._sourcePath = command._sourcePath;
    reloaded.module = reloaded.module || command.module;
    reloaded.cooldown = reloaded.cooldown ?? 3;

    client.commands.set(commandName, reloaded);
    log.info(`Commande rechargée : /${commandName}`);
    return true;
  } catch (err) {
    log.error(`Erreur rechargement /${commandName}: ${err.message}`);
    return false;
  }
}

module.exports = { loadCommands, reloadCommand };