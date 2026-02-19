require('dotenv').config();
const { REST, Routes } = require('discord.js');
const fs = require('fs');
const path = require('path');

const commands = [];

// Charger toutes les commandes récursivement
function loadCommands(dir) {
  const items = fs.readdirSync(dir, { withFileTypes: true });
  for (const item of items) {
    const fullPath = path.join(dir, item.name);
    if (item.isDirectory()) {
      loadCommands(fullPath);
    } else if (item.name.endsWith('.js')) {
      const command = require(fullPath);
      if (command.data) {
        commands.push(command.data.toJSON());
        console.log(`  ✅ ${command.data.name}`);
      }
    }
  }
}

console.log('📋 Chargement des commandes...');
loadCommands(path.join(__dirname, 'commands'));

const rest = new REST({ version: '10' }).setToken(process.env.BOT_TOKEN);

(async () => {
  try {
    console.log(`\n🔄 Enregistrement de ${commands.length} commandes...`);

    if (process.env.GUILD_ID) {
      // Déploiement sur un serveur spécifique (instantané, pour le développement)
      await rest.put(
        Routes.applicationGuildCommands(process.env.CLIENT_ID, process.env.GUILD_ID),
        { body: commands },
      );
      console.log(`✅ Commandes enregistrées sur le serveur ${process.env.GUILD_ID}`);
    } else {
      // Déploiement global (peut prendre jusqu'à 1h)
      await rest.put(
        Routes.applicationCommands(process.env.CLIENT_ID),
        { body: commands },
      );
      console.log('✅ Commandes enregistrées globalement (peut prendre ~1h pour se propager)');
    }
  } catch (error) {
    console.error('❌ Erreur:', error);
  }
})();
