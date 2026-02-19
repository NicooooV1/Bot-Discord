const { messageLog, COLORS } = require('../utils/logger');

module.exports = {
  name: 'messageDelete',
  async execute(message) {
    // Ignorer les bots et DMs
    if (!message.guild) return;
    if (message.author?.bot) return;
    if (!message.content && message.embeds.length === 0 && message.attachments.size === 0) return;

    let content = message.content || '';

    // Ajouter les pièces jointes
    if (message.attachments.size > 0) {
      const attachments = message.attachments.map(a => a.url).join('\n');
      content += content ? `\n\n📎 Pièces jointes:\n${attachments}` : `📎 Pièces jointes:\n${attachments}`;
    }

    await messageLog(message.guild, {
      action: 'Message supprimé',
      author: message.author,
      channel: message.channel,
      oldContent: content || '*Contenu non disponible*',
      color: COLORS.RED,
    });
  },
};
