const { messageLog, COLORS } = require('../utils/logger');

module.exports = {
  name: 'messageUpdate',
  async execute(oldMessage, newMessage) {
    // Ignorer les bots, DMs, et les updates sans changement de contenu
    if (!newMessage.guild) return;
    if (newMessage.author?.bot) return;
    if (oldMessage.content === newMessage.content) return;
    if (!oldMessage.content && !newMessage.content) return;

    await messageLog(newMessage.guild, {
      action: 'Message modifié',
      author: newMessage.author,
      channel: newMessage.channel,
      oldContent: oldMessage.content || '*Non disponible*',
      newContent: newMessage.content || '*Vide*',
      color: COLORS.ORANGE,
    });
  },
};
