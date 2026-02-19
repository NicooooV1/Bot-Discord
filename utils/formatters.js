// ===================================
// Ultra Suite — Formatters & Helpers
// ===================================

const { time, TimestampStyles } = require('discord.js');

/**
 * Formate une durée en secondes → texte lisible
 * @param {number} seconds
 * @returns {string}
 */
function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '0s';

  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;

  const parts = [];
  if (d) parts.push(`${d}j`);
  if (h) parts.push(`${h}h`);
  if (m) parts.push(`${m}m`);
  if (s) parts.push(`${s}s`);

  return parts.join(' ');
}

/**
 * Parse une durée texte → secondes
 * Supporte : 1d, 2h, 30m, 60s, 1d2h30m
 * @param {string} input
 * @returns {number|null}
 */
function parseDuration(input) {
  if (!input) return null;

  const regex = /(\d+)\s*(d|j|h|m|s)/gi;
  let total = 0;
  let match;

  while ((match = regex.exec(input)) !== null) {
    const value = parseInt(match[1]);
    const unit = match[2].toLowerCase();
    switch (unit) {
      case 'd': case 'j': total += value * 86400; break;
      case 'h': total += value * 3600; break;
      case 'm': total += value * 60; break;
      case 's': total += value; break;
    }
  }

  return total > 0 ? total : null;
}

/**
 * Tronque un texte
 */
function truncate(text, maxLength = 1024) {
  if (!text) return '';
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength - 3) + '...';
}

/**
 * Timestamp Discord relatif
 */
function relativeTime(date) {
  return time(date, TimestampStyles.RelativeTime);
}

/**
 * Timestamp Discord complet
 */
function fullTime(date) {
  return time(date, TimestampStyles.LongDateTime);
}

/**
 * Génère un ID court unique
 */
function shortId() {
  return Math.random().toString(36).substring(2, 9);
}

/**
 * Chunk un array en sous-arrays de taille n
 */
function chunk(arr, size) {
  const result = [];
  for (let i = 0; i < arr.length; i += size) {
    result.push(arr.slice(i, i + size));
  }
  return result;
}

/**
 * Attend ms millisecondes
 */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Calcule le niveau à partir de l'XP
 * Formule : level = floor(0.1 * sqrt(xp))
 */
function xpToLevel(xp) {
  return Math.floor(0.1 * Math.sqrt(xp));
}

/**
 * XP nécessaire pour un niveau
 */
function levelToXp(level) {
  return Math.pow(level / 0.1, 2);
}

/**
 * XP pour le prochain niveau
 */
function xpForNextLevel(currentXp) {
  const currentLevel = xpToLevel(currentXp);
  return Math.ceil(levelToXp(currentLevel + 1));
}

/**
 * Barre de progression
 */
function progressBar(current, max, length = 10) {
  const filled = Math.round((current / max) * length);
  const empty = length - filled;
  return '█'.repeat(filled) + '░'.repeat(empty);
}

module.exports = {
  formatDuration,
  parseDuration,
  truncate,
  relativeTime,
  fullTime,
  shortId,
  chunk,
  sleep,
  xpToLevel,
  levelToXp,
  xpForNextLevel,
  progressBar,
};
