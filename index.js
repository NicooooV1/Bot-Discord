require('dotenv').config();
const { Client, GatewayIntentBits, Collection, Partials } = require('discord.js');
const fs = require('fs');
const path = require('path');

// ===================================
// Création du client Discord
// ===================================
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.GuildModeration,
  ],
  partials: [
    Partials.Message,
    Partials.Channel,
    Partials.GuildMember,
  ],
});

// ===================================
// Chargement des commandes
// ===================================
client.commands = new Collection();

function loadCommands(dir) {
  const items = fs.readdirSync(dir, { withFileTypes: true });
  for (const item of items) {
    const fullPath = path.join(dir, item.name);
    if (item.isDirectory()) {
      loadCommands(fullPath);
    } else if (item.name.endsWith('.js')) {
      const command = require(fullPath);
      if (command.data && command.execute) {
        client.commands.set(command.data.name, command);
        console.log(`  📌 Commande chargée: /${command.data.name}`);
      }
    }
  }
}

console.log('\n📋 Chargement des commandes...');
loadCommands(path.join(__dirname, 'commands'));

// ===================================
// Chargement des événements
// ===================================
console.log('\n📡 Chargement des événements...');
const eventsPath = path.join(__dirname, 'events');
const eventFiles = fs.readdirSync(eventsPath).filter(f => f.endsWith('.js'));

for (const file of eventFiles) {
  const event = require(path.join(eventsPath, file));
  if (event.once) {
    client.once(event.name, (...args) => event.execute(...args));
  } else {
    client.on(event.name, (...args) => event.execute(...args));
  }
  console.log(`  📡 Événement chargé: ${event.name}`);
}

// ===================================
// Gestion des erreurs
// ===================================
client.on('error', (error) => {
  console.error('[CLIENT ERROR]', error);
});

process.on('unhandledRejection', (error) => {
  console.error('[UNHANDLED REJECTION]', error);
});

process.on('uncaughtException', (error) => {
  console.error('[UNCAUGHT EXCEPTION]', error);
});

// ===================================
// Connexion
// ===================================
if (!process.env.BOT_TOKEN) {
  console.error('❌ BOT_TOKEN manquant dans le fichier .env');
  console.error('   Copiez .env.example en .env et ajoutez votre token.');
  process.exit(1);
}

client.login(process.env.BOT_TOKEN);
