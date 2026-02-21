// ===================================
// Ultra Suite — /snipe
// Voir le dernier message supprimé
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

// In-memory cache — populated by messageDelete event
const snipeCache = new Map();

module.exports = {
  module: 'utility',
  cooldown: 5,
  snipeCache,

  data: new SlashCommandBuilder()
    .setName('snipe')
    .setDescription('Voir le dernier message supprimé')
    .addIntegerOption((o) => o.setName('index').setDescription('Index (0 = plus récent)').setMinValue(0).setMaxValue(9)),

  async execute(interaction) {
    const index = interaction.options.getInteger('index') || 0;
    const key = interaction.channelId;
    const cache = snipeCache.get(key) || [];

    if (!cache.length || !cache[index]) {
      return interaction.reply({ content: '❌ Aucun message supprimé à afficher.', ephemeral: true });
    }

    const snipe = cache[index];

    const embed = new EmbedBuilder()
      .setAuthor({ name: snipe.author.tag, iconURL: snipe.author.avatarURL })
      .setColor(0xE74C3C)
      .setDescription(snipe.content || '*[Pas de contenu texte]*')
      .setFooter({ text: `Message #${index + 1}/${cache.length} • Supprimé` })
      .setTimestamp(snipe.deletedAt);

    if (snipe.attachments?.length) {
      embed.addFields({ name: '📎 Pièces jointes', value: snipe.attachments.join('\n').substring(0, 1024) });
      if (snipe.attachments[0]?.match(/\.(png|jpg|gif|webp)/i)) {
        embed.setImage(snipe.attachments[0]);
      }
    }

    if (snipe.stickers?.length) {
      embed.addFields({ name: '🏷️ Stickers', value: snipe.stickers.join(', ') });
    }

    return interaction.reply({ embeds: [embed] });
  },
};
