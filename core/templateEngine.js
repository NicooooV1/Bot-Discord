// ===================================
// Ultra Suite — Template Engine
// Moteur de templates pour messages et embeds
//
// Variables supportées :
//   {user.mention}      → <@userId>
//   {user.tag}          → Username#0000
//   {user.name}         → Username
//   {user.id}           → ID
//   {user.avatar}       → URL avatar
//   {user.createdAt}    → Date de création
//   {guild.name}        → Nom du serveur
//   {guild.memberCount} → Nombre de membres
//   {guild.icon}        → URL icône
//   {guild.id}          → ID du serveur
//   {channel.name}      → Nom du salon
//   {channel.mention}   → <#channelId>
//   {level}             → Niveau (XP)
//   {xp}                → XP total
//   {date}              → Date actuelle
//   {time}              → Heure actuelle
//   {count}             → Compteur contextuel
//   {reason}            → Raison (modération)
//   {duration}          → Durée (modération)
//   {role.name}         → Nom d'un rôle
//   {role.mention}      → <@&roleId>
//   + variables custom par module
// ===================================

const { createModuleLogger } = require('./logger');

const log = createModuleLogger('TemplateEngine');

// ===================================
// Résolution de variables
// ===================================

/**
 * Construit le contexte de variables standard.
 *
 * @param {object} options
 * @param {import('discord.js').User} [options.user]
 * @param {import('discord.js').Guild} [options.guild]
 * @param {import('discord.js').Channel} [options.channel]
 * @param {import('discord.js').Role} [options.role]
 * @param {object} [options.extra] - Variables custom { key: value }
 * @returns {object} Flat object de variables
 */
function buildContext(options = {}) {
  const ctx = {};

  if (options.user) {
    const u = options.user;
    ctx['user.mention'] = `<@${u.id}>`;
    ctx['user.tag'] = u.tag || `${u.username}#${u.discriminator || '0'}`;
    ctx['user.name'] = u.displayName || u.username;
    ctx['user.username'] = u.username;
    ctx['user.id'] = u.id;
    ctx['user.avatar'] = u.displayAvatarURL({ dynamic: true, size: 256 });
    ctx['user.createdAt'] = u.createdAt
      ? `<t:${Math.floor(u.createdAt.getTime() / 1000)}:R>`
      : 'N/A';
  }

  if (options.guild) {
    const g = options.guild;
    ctx['guild.name'] = g.name;
    ctx['guild.memberCount'] = String(g.memberCount);
    ctx['guild.icon'] = g.iconURL({ dynamic: true, size: 256 }) || '';
    ctx['guild.id'] = g.id;
  }

  if (options.channel) {
    const c = options.channel;
    ctx['channel.name'] = c.name || 'DM';
    ctx['channel.mention'] = c.id ? `<#${c.id}>` : 'DM';
    ctx['channel.id'] = c.id || '';
  }

  if (options.role) {
    const r = options.role;
    ctx['role.name'] = r.name;
    ctx['role.mention'] = `<@&${r.id}>`;
    ctx['role.id'] = r.id;
    ctx['role.color'] = r.hexColor;
  }

  // Date/heure
  const now = new Date();
  ctx['date'] = now.toLocaleDateString('fr-FR');
  ctx['time'] = now.toLocaleTimeString('fr-FR');
  ctx['timestamp'] = `<t:${Math.floor(now.getTime() / 1000)}:F>`;

  // Variables extra (modération, XP, économie, etc.)
  if (options.extra) {
    for (const [key, value] of Object.entries(options.extra)) {
      ctx[key] = String(value ?? '');
    }
  }

  return ctx;
}

// ===================================
// Moteur d'interpolation
// ===================================

/**
 * Interpole les variables {key} dans un template.
 *
 * @param {string} template - Template avec variables {key}
 * @param {object} context - Variables résollues (depuis buildContext)
 * @returns {string} Texte interpolé
 */
function render(template, context) {
  if (!template || typeof template !== 'string') return template || '';

  return template.replace(/\{([^}]+)\}/g, (match, key) => {
    const trimmed = key.trim();
    if (trimmed in context) {
      return context[trimmed];
    }
    // Variable inconnue : laisser tel quel
    return match;
  });
}

/**
 * Interpole un template avec des options directes.
 * Wrapper pratique autour de buildContext + render.
 *
 * @param {string} template
 * @param {object} options - Même format que buildContext
 * @returns {string}
 */
function renderTemplate(template, options) {
  const ctx = buildContext(options);
  return render(template, ctx);
}

// ===================================
// Moteur d'embeds
// ===================================

/**
 * Interpole les variables dans un objet embed.
 *
 * @param {object} embedData - Objet avec title, description, footer, fields, etc.
 * @param {object} context - Variables résollues
 * @returns {object} Embed data interpolé
 */
function renderEmbed(embedData, context) {
  if (!embedData || typeof embedData !== 'object') return embedData;

  const result = { ...embedData };

  // Interpoler les champs texte
  if (result.title) result.title = render(result.title, context);
  if (result.description) result.description = render(result.description, context);

  if (result.footer) {
    if (typeof result.footer === 'string') {
      result.footer = { text: render(result.footer, context) };
    } else if (result.footer.text) {
      result.footer = { ...result.footer, text: render(result.footer.text, context) };
    }
  }

  if (result.author) {
    if (result.author.name) {
      result.author = { ...result.author, name: render(result.author.name, context) };
    }
  }

  // Fields
  if (result.fields && Array.isArray(result.fields)) {
    result.fields = result.fields.map((field) => ({
      ...field,
      name: render(field.name, context),
      value: render(field.value, context),
    }));
  }

  // Interpoler l'image/thumbnail si c'est une variable
  if (result.image?.url) result.image = { ...result.image, url: render(result.image.url, context) };
  if (result.thumbnail?.url) result.thumbnail = { ...result.thumbnail, url: render(result.thumbnail.url, context) };

  return result;
}

/**
 * Interpole un embed avec des options directes.
 *
 * @param {object} embedData
 * @param {object} options - Même format que buildContext
 * @returns {object}
 */
function renderEmbedTemplate(embedData, options) {
  const ctx = buildContext(options);
  return renderEmbed(embedData, ctx);
}

// ===================================
// Templates par défaut
// ===================================

const DEFAULT_TEMPLATES = {
  welcome: {
    content: 'Bienvenue {user.mention} sur **{guild.name}** ! 🎉',
    embed: null,
  },
  goodbye: {
    content: '**{user.tag}** a quitté le serveur. 👋',
    embed: null,
  },
  levelUp: {
    content: '🎉 {user.mention} est passé au **niveau {level}** !',
    embed: null,
  },
  sanction: {
    content: null,
    embed: {
      title: '⚠️ Sanction',
      description: '{user.mention} a reçu une sanction.\n**Type:** {type}\n**Raison:** {reason}',
      color: 0xFF6B6B,
      footer: { text: 'Par {moderator.tag}' },
    },
  },
  ticketOpen: {
    content: 'Ticket ouvert par {user.mention}.\nUn membre du staff va vous répondre rapidement.',
    embed: null,
  },
  ticketClose: {
    content: 'Ce ticket a été fermé par {user.tag}.',
    embed: null,
  },
};

/**
 * Récupère un template (custom de la guild ou défaut).
 *
 * @param {object} guildConfig  - Config de la guild
 * @param {string} templateName - Nom du template (ex: 'welcome', 'levelUp')
 * @returns {object} { content, embed }
 */
function getTemplate(guildConfig, templateName) {
  // Chercher dans les templates custom de la guild
  const custom = guildConfig?.templates?.[templateName];
  if (custom) return custom;

  // Sinon, utiliser le défaut
  return DEFAULT_TEMPLATES[templateName] || { content: '', embed: null };
}

/**
 * Sauvegarde un template custom pour une guild.
 *
 * @param {object} guildConfig
 * @param {string} templateName
 * @param {object} templateData - { content?, embed? }
 */
function setTemplate(guildConfig, templateName, templateData) {
  if (!guildConfig.templates) guildConfig.templates = {};
  guildConfig.templates[templateName] = templateData;
}

/**
 * Supprime un template custom (revient au défaut).
 *
 * @param {object} guildConfig
 * @param {string} templateName
 */
function resetTemplate(guildConfig, templateName) {
  if (guildConfig.templates) {
    delete guildConfig.templates[templateName];
  }
}

// ===================================
// Exports
// ===================================

module.exports = {
  // Contexte
  buildContext,

  // Rendu texte
  render,
  renderTemplate,

  // Rendu embeds
  renderEmbed,
  renderEmbedTemplate,

  // Templates
  DEFAULT_TEMPLATES,
  getTemplate,
  setTemplate,
  resetTemplate,
};
