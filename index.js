// ===================================
// Ultra Suite — Point d'entrée principal
// Node.js 20+ / discord.js v14
// ===================================

require('dotenv').config();

const { Client, GatewayIntentBits, Partials } = require('discord.js');
const { logger, createModuleLogger } = require('./core/logger');
const db = require('./database');
const { loadCommands } = require('./core/commandHandler');
const { loadEvents } = require('./core/eventHandler');
const { loadComponents } = require('./core/componentHandler');
const { loadLocales } = require('./core/i18n');
const { startScheduler, stopScheduler } = require('./core/scheduler');
const { startApi, stopApi } = require('./core/api');

const log = createModuleLogger('Main');

// ===================================
// Création du client Discord
// ===================================
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.GuildModeration,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.GuildMessageReactions,
    GatewayIntentBits.GuildVoiceStates,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.DirectMessages,
  ],
  partials: [
    Partials.Message,
    Partials.Channel,
    Partials.Reaction,
    Partials.GuildMember,
    Partials.User,
  ],
  allowedMentions: { parse: ['users', 'roles'], repliedUser: true },
});

// ===================================
// Démarrage séquentiel
// ===================================
async function start() {
  log.info('=== Ultra Suite Bot v2.0 ===');
  log.info(`Environment: ${process.env.NODE_ENV || 'development'}`);

  // 1. Charger les locales i18n
  loadLocales();
  log.info('i18n loaded');

  // 2. Initialiser la base de données
  await db.init();
  log.info('Database initialized');

  // 3. Charger les commandes
  loadCommands(client);

  // 4. Charger les composants (boutons/selects/modals)
  loadComponents(client);

  // 5. Charger les événements
  loadEvents(client);

  // 6. Connexion à Discord
  await client.login(process.env.BOT_TOKEN);
  log.info(`Logged in as ${client.user.tag}`);

  // 7. Démarrer le scheduler CRON
  startScheduler(client);

  // 8. Démarrer l'API (si activée)
  startApi(client);

  log.info('Bot fully started!');
}

// ===================================
// Arrêt propre (SIGINT / SIGTERM)
// ===================================
async function shutdown(signal) {
  log.info(`Received ${signal}, shutting down...`);

  stopScheduler();
  stopApi();
  client.destroy();
  await db.close();

  log.info('Goodbye!');
  process.exit(0);
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));

// ===================================
// Gestion des erreurs non interceptées
// ===================================
process.on('unhandledRejection', (err) => {
  log.error('Unhandled rejection:', err);
});

process.on('uncaughtException', (err) => {
  log.error('Uncaught exception:', err);
  // Ne pas crash pour les erreurs Discord (interaction déjà répondue, etc.)
  if (err.code === 40060 || err.code === 10062 || err.code === 'InteractionAlreadyReplied') {
    log.warn('Non-fatal Discord error, continuing...');
    return;
  }
  // Erreur vraiment fatale → shutdown
  shutdown('uncaughtException');
});

// ===================================
// GO!
// ===================================
start().catch((err) => {
  log.error('Failed to start bot:', err);
  process.exit(1);
});
