// ===================================
// Ultra Suite — i18n (Internationalisation)
// Système de traductions multi-langue par guild
//
// Chaque guild peut choisir sa locale (fr, en, etc.)
// Les traductions sont chargées depuis locales/*.json
//
// Usage :
//   const { t, loadLocales } = require('./core/i18n');
//   const msg = await t(guildId, 'moderation.ban.success', { user: 'John', reason: 'Spam' });
//   // → "John a été banni pour : Spam"
// ===================================

const fs = require('fs');
const path = require('path');
const configService = require('./configService');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('i18n');

// Stockage des traductions : { fr: { ... }, en: { ... } }
const locales = {};

// Locale par défaut
const DEFAULT_LOCALE = process.env.DEFAULT_LOCALE || 'fr';

/**
 * Charge tous les fichiers de traduction depuis locales/
 * Appelé une seule fois au démarrage
 *
 * Structure attendue :
 *   locales/fr.json
 *   locales/en.json
 */
function loadLocales() {
  const localesDir = path.join(__dirname, '..', 'locales');

  if (!fs.existsSync(localesDir)) {
    log.warn(`Répertoire locales/ introuvable — création avec locale par défaut`);
    fs.mkdirSync(localesDir, { recursive: true });

    // Créer un fichier fr.json minimal
    const defaultTranslations = {
      common: {
        error: 'Une erreur est survenue.',
        no_permission: 'Vous n\'avez pas la permission d\'utiliser cette commande.',
        module_disabled: 'Le module **{module}** est désactivé sur ce serveur.',
        cooldown: 'Merci de patienter **{time}s** avant de réutiliser cette commande.',
        success: 'Action effectuée avec succès.',
        cancelled: 'Action annulée.',
      },
      moderation: {
        ban: {
          success: '**{user}** a été banni. Raison : {reason}',
          dm: 'Vous avez été banni de **{guild}**. Raison : {reason}',
          already_banned: 'Cet utilisateur est déjà banni.',
        },
        kick: {
          success: '**{user}** a été expulsé. Raison : {reason}',
          dm: 'Vous avez été expulsé de **{guild}**. Raison : {reason}',
        },
        warn: {
          success: '**{user}** a reçu un avertissement ({count}/{max}). Raison : {reason}',
          dm: 'Vous avez reçu un avertissement sur **{guild}**. Raison : {reason}',
        },
        timeout: {
          success: '**{user}** a été réduit au silence pour **{duration}**. Raison : {reason}',
        },
      },
      tickets: {
        created: 'Ticket #{id} créé. Un membre du staff vous répondra bientôt.',
        closed: 'Ticket #{id} fermé par {user}.',
        max_reached: 'Vous avez atteint le maximum de {max} ticket(s) ouverts.',
      },
      xp: {
        level_up: 'Bravo **{user}** ! Vous êtes passé au niveau **{level}** !',
      },
      economy: {
        daily_claimed: 'Vous avez récupéré **{amount}{currency}** ! Solde : {balance}{currency}',
        daily_cooldown: 'Vous avez déjà récupéré votre récompense aujourd\'hui. Revenez dans **{time}**.',
        insufficient: 'Fonds insuffisants. Vous avez **{balance}{currency}**.',
        transfer_success: 'Vous avez envoyé **{amount}{currency}** à **{target}**.',
      },
      welcome: {
        default_message: 'Bienvenue **{user}** sur **{guild}** ! Vous êtes le membre #{count}.',
      },
      goodbye: {
        default_message: '**{user}** a quitté le serveur. Nous étions {count} membres.',
      },
    };

    fs.writeFileSync(
      path.join(localesDir, `${DEFAULT_LOCALE}.json`),
      JSON.stringify(defaultTranslations, null, 2),
      'utf-8'
    );
    log.info(`Fichier ${DEFAULT_LOCALE}.json créé avec les traductions par défaut`);
  }

  // Charger tous les fichiers .json
  const files = fs.readdirSync(localesDir).filter((f) => f.endsWith('.json'));

  for (const file of files) {
    const locale = path.basename(file, '.json');
    try {
      const content = fs.readFileSync(path.join(localesDir, file), 'utf-8');
      locales[locale] = JSON.parse(content);
      log.info(`Locale chargée : ${locale} (${Object.keys(locales[locale]).length} catégories)`);
    } catch (err) {
      log.error(`Erreur chargement locale ${file}: ${err.message}`);
    }
  }

  if (Object.keys(locales).length === 0) {
    log.warn('Aucune locale chargée — les traductions ne fonctionneront pas');
  }
}

/**
 * Résout une clé à points dans un objet
 * Ex: resolve({ a: { b: 'hello' } }, 'a.b') → 'hello'
 */
function resolveKey(obj, key) {
  if (!obj || !key) return undefined;
  const parts = key.split('.');
  let current = obj;
  for (const part of parts) {
    if (current === undefined || current === null) return undefined;
    current = current[part];
  }
  return current;
}

/**
 * Remplace les placeholders {key} dans un texte
 *
 * @param {string} text
 * @param {object} params — { user: 'John', reason: 'Spam' }
 * @returns {string}
 */
function interpolate(text, params = {}) {
  if (!text || typeof text !== 'string') return text;

  return text.replace(/\{(\w+)\}/g, (match, key) => {
    return params[key] !== undefined ? String(params[key]) : match;
  });
}

/**
 * Traduit une clé pour une guild donnée
 *
 * @param {string} guildId — ID de la guild (pour déterminer la locale)
 * @param {string} key — Clé à points (ex: 'moderation.ban.success')
 * @param {object} params — Placeholders (ex: { user: 'John' })
 * @returns {Promise<string>} — Texte traduit
 *
 * @example
 * await t(guildId, 'moderation.ban.success', { user: 'John', reason: 'Spam' })
 * // → "John a été banni. Raison : Spam"
 */
async function t(guildId, key, params = {}) {
  // Déterminer la locale de la guild
  let locale = DEFAULT_LOCALE;

  if (guildId) {
    try {
      const config = await configService.get(guildId);
      locale = config?.locale || DEFAULT_LOCALE;
    } catch {
      // Fallback sur la locale par défaut
    }
  }

  // Chercher la traduction dans la locale demandée
  let text = resolveKey(locales[locale], key);

  // Fallback sur la locale par défaut si non trouvée
  if (text === undefined && locale !== DEFAULT_LOCALE) {
    text = resolveKey(locales[DEFAULT_LOCALE], key);
  }

  // Si toujours pas trouvée, retourner la clé
  if (text === undefined) {
    log.debug(`Traduction manquante : ${key} (locale: ${locale})`);
    return key;
  }

  // Interpoler les placeholders
  return interpolate(text, params);
}

/**
 * Version synchrone de t() — utilise la locale par défaut
 * Utile quand on ne peut pas faire d'async (rarement)
 *
 * @param {string} key
 * @param {object} params
 * @returns {string}
 */
function tSync(key, params = {}) {
  const text = resolveKey(locales[DEFAULT_LOCALE], key);
  if (text === undefined) return key;
  return interpolate(text, params);
}

/**
 * Retourne toutes les locales disponibles
 * @returns {string[]}
 */
function getAvailableLocales() {
  return Object.keys(locales);
}

/**
 * Vérifie si une locale existe
 * @param {string} locale
 * @returns {boolean}
 */
function isValidLocale(locale) {
  return locale in locales;
}

module.exports = { loadLocales, t, tSync, getAvailableLocales, isValidLocale };