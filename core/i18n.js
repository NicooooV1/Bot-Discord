// ===================================
// Ultra Suite — i18n (Internationalisation)
// Fallback : fr → en → clé brute
// ===================================

const fs = require('fs');
const path = require('path');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('i18n');
const locales = {};
const DEFAULT_LOCALE = process.env.DEFAULT_LOCALE || 'fr';

/**
 * Charge tous les fichiers JSON de src/locales/
 */
function loadLocales() {
  const dir = path.join(__dirname, '..', 'locales');
  if (!fs.existsSync(dir)) {
    log.warn('Locales directory not found, creating...');
    fs.mkdirSync(dir, { recursive: true });
    return;
  }

  const files = fs.readdirSync(dir).filter((f) => f.endsWith('.json'));
  for (const file of files) {
    const locale = path.basename(file, '.json');
    try {
      locales[locale] = JSON.parse(fs.readFileSync(path.join(dir, file), 'utf-8'));
      log.info(`Locale loaded: ${locale} (${Object.keys(locales[locale]).length} keys)`);
    } catch (err) {
      log.error(`Failed to load locale ${locale}:`, err);
    }
  }
}

/**
 * Résout une clé i18n à points : "mod.ban.success"
 * @param {string} key — ex: "mod.ban.success"
 * @param {string} locale — ex: "fr"
 * @param {object} vars — variables de substitution { user, reason, ... }
 * @returns {string}
 */
function t(key, locale = DEFAULT_LOCALE, vars = {}) {
  const resolved = resolve(key, locale) || resolve(key, DEFAULT_LOCALE) || resolve(key, 'en') || key;

  // Remplace {{var}} par la valeur
  return resolved.replace(/\{\{(\w+)\}\}/g, (_, name) => {
    return vars[name] !== undefined ? String(vars[name]) : `{{${name}}}`;
  });
}

/**
 * Résout une clé dans une locale spécifique
 */
function resolve(key, locale) {
  const data = locales[locale];
  if (!data) return null;

  const parts = key.split('.');
  let current = data;
  for (const part of parts) {
    if (current === undefined || current === null) return null;
    current = current[part];
  }
  return typeof current === 'string' ? current : null;
}

/**
 * Raccourci pour créer un traducteur contextuel (guild locale)
 */
function createTranslator(locale) {
  return (key, vars) => t(key, locale, vars);
}

module.exports = { loadLocales, t, createTranslator };
