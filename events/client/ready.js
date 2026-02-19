// ===================================
// Ultra Suite — Event: ready
// ===================================

const { ActivityType } = require('discord.js');
const { createModuleLogger } = require('../../core/logger');

const log = createModuleLogger('Ready');

module.exports = {
  name: 'ready',
  once: true,

  async execute(client) {
    log.info(`Ready! Logged in as ${client.user.tag}`);
    log.info(`Serving ${client.guilds.cache.size} guilds, ${client.users.cache.size} cached users`);

    // Statut du bot
    client.user.setPresence({
      activities: [
        {
          name: `${client.guilds.cache.size} serveurs`,
          type: ActivityType.Watching,
        },
      ],
      status: 'online',
    });

    // Rafraîchir le statut toutes les 5 minutes
    setInterval(() => {
      client.user.setActivity(`${client.guilds.cache.size} serveurs`, { type: ActivityType.Watching });
    }, 5 * 60 * 1000);
  },
};
