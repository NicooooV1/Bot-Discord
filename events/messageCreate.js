const { checkSpam } = require('../utils/antispam');
const { getGuildConfig } = require('../utils/database');

module.exports = {
  name: 'messageCreate',
  async execute(message) {
    // Ignorer les bots et DMs
    if (!message.guild) return;
    if (message.author.bot) return;

    // Vérifier si l'anti-spam est activé
    const config = getGuildConfig(message.guild.id);
    if (config?.antispam_enabled) {
      await checkSpam(message);
    }
  },
};
