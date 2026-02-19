// ===================================
// Ultra Suite — Embed Builders
// Helpers pour créer des embeds uniformes
// ===================================

const { EmbedBuilder, Colors } = require('discord.js');

const COLORS = {
  primary: 0x5865f2,   // Blurple
  success: 0x57f287,   // Vert
  warning: 0xfee75c,   // Jaune
  error: 0xed4245,     // Rouge
  info: 0x5865f2,      // Bleu
  moderation: 0xff6b35, // Orange
  logs: 0x99aab5,      // Gris
};

/**
 * Crée un embed standard
 */
function createEmbed(color = 'primary') {
  return new EmbedBuilder()
    .setColor(COLORS[color] || COLORS.primary)
    .setTimestamp();
}

/**
 * Embed de succès
 */
function successEmbed(description) {
  return createEmbed('success').setDescription(description);
}

/**
 * Embed d'erreur
 */
function errorEmbed(description) {
  return createEmbed('error').setDescription(description);
}

/**
 * Embed d'avertissement
 */
function warnEmbed(description) {
  return createEmbed('warning').setDescription(description);
}

/**
 * Embed d'information
 */
function infoEmbed(description) {
  return createEmbed('info').setDescription(description);
}

/**
 * Embed de modération (case)
 */
function modEmbed({ type, target, moderator, reason, caseNumber, duration }) {
  const embed = createEmbed('moderation')
    .setTitle(`${type} | Case #${caseNumber}`)
    .addFields(
      { name: 'Utilisateur', value: `${target}`, inline: true },
      { name: 'Modérateur', value: `${moderator}`, inline: true },
      { name: 'Raison', value: reason || 'Aucune raison', inline: false }
    );

  if (duration) {
    embed.addFields({ name: 'Durée', value: duration, inline: true });
  }

  return embed;
}

/**
 * Embed de log
 */
function logEmbed({ title, description, fields = [], color = 'logs' }) {
  const embed = createEmbed(color).setTitle(title);
  if (description) embed.setDescription(description);
  if (fields.length) embed.addFields(fields);
  return embed;
}

/**
 * Paginer un tableau en embeds
 * @param {Array} items
 * @param {number} perPage
 * @param {Function} formatter — (item, index) => string
 * @param {object} options — { title, color }
 * @returns {EmbedBuilder[]}
 */
function paginateEmbeds(items, perPage, formatter, { title, color = 'primary' } = {}) {
  const pages = [];
  const totalPages = Math.ceil(items.length / perPage) || 1;

  for (let i = 0; i < totalPages; i++) {
    const slice = items.slice(i * perPage, (i + 1) * perPage);
    const description = slice.map((item, idx) => formatter(item, i * perPage + idx)).join('\n');

    const embed = createEmbed(color)
      .setDescription(description || 'Aucun élément.')
      .setFooter({ text: `Page ${i + 1}/${totalPages}` });

    if (title) embed.setTitle(title);
    pages.push(embed);
  }

  return pages;
}

module.exports = {
  COLORS,
  createEmbed,
  successEmbed,
  errorEmbed,
  warnEmbed,
  infoEmbed,
  modEmbed,
  logEmbed,
  paginateEmbeds,
};
