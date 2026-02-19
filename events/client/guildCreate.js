// ===================================
// Ultra Suite — Event: guildCreate
// Init DB quand le bot rejoint un serveur
// ===================================

const { createModuleLogger } = require('../../core/logger');
const guildQueries = require('../../database/guildQueries');

const log = createModuleLogger('GuildCreate');

module.exports = {
  name: 'guildCreate',

  async execute(guild) {
    log.info(`Joined guild: ${guild.name} (${guild.id}) — ${guild.memberCount} members`);
    await guildQueries.getOrCreate(guild.id, guild.name, guild.ownerId);
  },
};
