// ===================================
// Ultra Suite — Point d'entrée principal
// Node.js 20+ / discord.js v14
// Multi-serveur — Single process
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
const guildQueries = require('./database/guildQueries');
const moduleRegistry = require('./core/moduleRegistry');

const log = createModuleLogger('Main');

// ===================================
// Validation des variables d'environnement
// ===================================
function validateEnv() {
  const required = ['BOT_TOKEN', 'CLIENT_ID', 'DB_HOST', 'DB_NAME'];
  const missing = required.filter((key) => !process.env[key]);
  if (missing.length > 0) {
    log.error(`Variables d'environnement manquantes : ${missing.join(', ')}`);
    log.error('Consultez .env.example pour la configuration requise.');
    process.exit(1);
  }
}

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
    GatewayIntentBits.GuildPresences,
  ],
  partials: [
    Partials.Message,
    Partials.Channel,
    Partials.Reaction,
    Partials.GuildMember,
    Partials.User,
  ],
  allowedMentions: { parse: ['users', 'roles'], repliedUser: true },
  // Augmenter le sweep pour les gros bots multi-serveurs
  sweepers: {
    messages: {
      interval: 300, // Toutes les 5 minutes
      lifetime: 600,  // Messages > 10 minutes supprimés du cache
    },
  },
});

// ===================================
// Synchronisation des guilds existantes
// Garantit que toutes les guilds où le bot est présent
// sont initialisées en base de données
// ===================================
async function syncGuilds() {
  const guilds = client.guilds.cache;
  let synced = 0;
  let errors = 0;

  log.info(`Synchronisation de ${guilds.size} serveur(s) en base de données...`);

  for (const [guildId, guild] of guilds) {
    try {
      await guildQueries.getOrCreate(guildId, guild.name, guild.ownerId);
      synced++;
    } catch (err) {
      log.error(`Erreur sync guild ${guild.name} (${guildId}):`, err.message);
      errors++;
    }
  }

  log.info(`Sync terminée : ${synced} serveur(s) synchronisé(s), ${errors} erreur(s)`);
}

// ===================================
// Démarrage séquentiel
// ===================================
async function start() {
  log.info('╔════════════════════════════════════╗');
  log.info('║     Ultra Suite Bot v2.0           ║');
  log.info('║     Multi-serveur · Modulaire      ║');
  log.info('╚════════════════════════════════════╝');
  log.info(`Environnement : ${process.env.NODE_ENV || 'development'}`);
  log.info(`Locale par défaut : ${process.env.DEFAULT_LOCALE || 'fr'}`);

  // 0. Valider l'environnement
  validateEnv();

  // 1. Charger les locales i18n
  loadLocales();
  log.info('✔ Locales i18n chargées');

  // 2. Initialiser la base de données (migrations auto)
  await db.init();
  log.info('✔ Base de données initialisée (MySQL)');

  // 2b. Charger les manifests de modules
  moduleRegistry.loadAll();
  log.info(`✔ Module Registry : ${moduleRegistry.getAll().length} module(s) enregistré(s)`);

  // 3. Charger les commandes (toutes les slash commands)
  loadCommands(client);
  log.info(`✔ Commandes chargées (${client.commands.size})`);

  // 4. Charger les composants (boutons/selects/modals)
  loadComponents(client);
  log.info('✔ Composants interactifs chargés');

  // 5. Charger les événements Discord
  loadEvents(client);
  log.info('✔ Événements chargés');

  // 6. Connexion à Discord
  await client.login(process.env.BOT_TOKEN);
  log.info(`✔ Connecté en tant que ${client.user.tag}`);

  // 7. Synchroniser les guilds existantes en DB
  // (attendre que le cache soit prêt)
  client.once('ready', async () => {
    await syncGuilds();

    // 8. Démarrer le scheduler CRON (après sync)
    startScheduler(client);
    log.info('✔ Tâches planifiées démarrées');

    // 9. Démarrer l'API REST (si activée)
    startApi(client);

    log.info('═══════════════════════════════════');
    log.info(`Bot opérationnel sur ${client.guilds.cache.size} serveur(s) !`);
    log.info('═══════════════════════════════════');
  });
}

// ===================================
// Arrêt propre (SIGINT / SIGTERM)
// ===================================
let isShuttingDown = false;

async function shutdown(signal) {
  if (isShuttingDown) return; // Éviter un double shutdown
  isShuttingDown = true;

  log.info(`Signal ${signal} reçu, arrêt en cours...`);

  try {
    stopScheduler();
    log.info('✔ Tâches planifiées arrêtées');
  } catch (err) {
    log.error('Erreur arrêt scheduler:', err.message);
  }

  try {
    stopApi();
    log.info('✔ API arrêtée');
  } catch (err) {
    log.error('Erreur arrêt API:', err.message);
  }

  try {
    client.destroy();
    log.info('✔ Client Discord déconnecté');
  } catch (err) {
    log.error('Erreur déconnexion Discord:', err.message);
  }

  try {
    await db.close();
    log.info('✔ Connexion DB fermée');
  } catch (err) {
    log.error('Erreur fermeture DB:', err.message);
  }

  log.info('Au revoir !');
  process.exit(0);
}

process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));

// ===================================
// Gestion des erreurs non interceptées
// ===================================
const DISCORD_NON_FATAL_CODES = [
  40060,                        // Interaction has already been acknowledged
  10062,                        // Unknown interaction
  'InteractionAlreadyReplied',  // discord.js error code
  50013,                        // Missing Permissions (non-fatal pour le bot)
  50001,                        // Missing Access
  30007,                        // Maximum number of webhooks reached
];

process.on('unhandledRejection', (err) => {
  // Erreurs Discord non-fatales → log warning seulement
  if (err?.code && DISCORD_NON_FATAL_CODES.includes(err.code)) {
    log.warn(`Erreur Discord non-fatale (code ${err.code}): ${err.message}`);
    return;
  }

  // Erreur réseau Discord (reconnexion auto)
  if (err?.code === 'ECONNRESET' || err?.code === 'ETIMEDOUT') {
    log.warn(`Erreur réseau Discord: ${err.message} — reconnexion automatique`);
    return;
  }

  log.error('Unhandled rejection:', err);
});

process.on('uncaughtException', (err) => {
  log.error('Uncaught exception:', err);

  // Erreurs Discord non-fatales → continuer
  if (err?.code && DISCORD_NON_FATAL_CODES.includes(err.code)) {
    log.warn('Erreur Discord non-fatale, le bot continue...');
    return;
  }

  // Erreur vraiment fatale → shutdown propre
  log.error('Erreur fatale, arrêt du bot...');
  shutdown('uncaughtException').catch(() => process.exit(1));
});

// ===================================
// Gestion de la mémoire (multi-serveur)
// ===================================
setInterval(() => {
  const mem = process.memoryUsage();
  const heapMB = (mem.heapUsed / 1024 / 1024).toFixed(1);
  const rssMB = (mem.rss / 1024 / 1024).toFixed(1);

  // Alerte si > 512 MB heap
  if (mem.heapUsed > 512 * 1024 * 1024) {
    log.warn(`Mémoire élevée — Heap: ${heapMB}MB, RSS: ${rssMB}MB`);
  } else {
    log.debug(`Mémoire — Heap: ${heapMB}MB, RSS: ${rssMB}MB, Guilds: ${client.guilds?.cache?.size || 0}`);
  }
}, 5 * 60 * 1000); // Toutes les 5 minutes

// ===================================
// GO!
// ===================================
start().catch((err) => {
  log.error('Échec du démarrage:', err);
  process.exit(1);
});