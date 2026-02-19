const { ActivityType } = require('discord.js');

module.exports = {
  name: 'ready',
  once: true,
  execute(client) {
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
    console.log(`✅ Bot connecté en tant que ${client.user.tag}`);
    console.log(`📡 ${client.guilds.cache.size} serveur(s)`);
    console.log(`👥 ${client.users.cache.size} utilisateur(s) en cache`);
    console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');

    // Statut du bot
    client.user.setPresence({
      activities: [{
        name: '🛡️ Modération & Support',
        type: ActivityType.Watching,
      }],
      status: 'online',
    });
  },
};
