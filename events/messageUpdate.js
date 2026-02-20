// ===================================
// Ultra Suite — Event: messageUpdate
// Déclenché quand un message est édité
//
// Actions (si modules activés) :
// 1. [logs]    Log l'ancien et nouveau contenu
// 2. [security] Re-vérifier l'automod sur le nouveau contenu
//
// Remplace messageUpdate.legacy
// ===================================

const { EmbedBuilder } = require('discord.js');
const configService = require('../core/configService');
const { getDb } = require('../database');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('MessageUpdate');

module.exports = {
  name: 'messageUpdate',
  once: false,

  /**
   * @param {import('discord.js').Message} oldMessage
   * @param {import('discord.js').Message} newMessage
   */
  async execute(oldMessage, newMessage) {
    // Ignorer les DMs
    if (!newMessage.guild) return;

    // Ignorer les partiels non cachés
    if (!oldMessage.author && !newMessage.author) return;

    // Ignorer les bots
    const author = newMessage.author || oldMessage.author;
    if (!author || author.bot) return;

    // Ignorer si le contenu n'a pas changé
    // (Discord fire aussi pour les embeds/link preview)
    if (oldMessage.content === newMessage.content) return;

    // Ignorer si pas de contenu à comparer
    if (!oldMessage.content && !newMessage.content) return;

    const guildId = newMessage.guild.id;
    const config = await configService.get(guildId);

    // =======================================
    // 1. Log (module: logs)
    // =======================================
    if (await configService.isModuleEnabled(guildId, 'logs')) {
      await logEdit(oldMessage, newMessage, config);
    }

    // =======================================
    // 2. Re-check automod (module: security)
    // =======================================
    if (await configService.isModuleEnabled(guildId, 'security')) {
      await recheckAutomod(newMessage, config);
    }
  },
};

// ===================================
// Fonctions
// ===================================

/**
 * Log l'édition dans le channel de logs
 */
async function logEdit(oldMessage, newMessage, config) {
  const logChannelId = config.logChannel;
  if (!logChannelId) return;

  // Ne pas logger les éditions dans le channel de logs
  if (newMessage.channel.id === logChannelId) return;

  const logChannel = newMessage.guild.channels.cache.get(logChannelId);
  if (!logChannel) return;

  try {
    const author = newMessage.author || oldMessage.author;

    // Tronquer les contenus si nécessaire (embed limit)
    const oldContent = oldMessage.content
      ? (oldMessage.content.length > 900
        ? oldMessage.content.slice(0, 900) + '... (tronqué)'
        : oldMessage.content)
      : '*Non disponible (message non caché)*';

    const newContent = newMessage.content
      ? (newMessage.content.length > 900
        ? newMessage.content.slice(0, 900) + '... (tronqué)'
        : newMessage.content)
      : '*Vide*';

    const embed = new EmbedBuilder()
      .setAuthor({
        name: `Message édité — ${author.tag}`,
        iconURL: author.displayAvatarURL(),
      })
      .setDescription(`[Aller au message](${newMessage.url})`)
      .addFields(
        {
          name: 'Avant',
          value: oldContent,
          inline: false,
        },
        {
          name: 'Après',
          value: newContent,
          inline: false,
        },
        {
          name: 'Channel',
          value: `${newMessage.channel} (${newMessage.channel.name})`,
          inline: true,
        },
        {
          name: 'Auteur',
          value: `${author} (${author.id})`,
          inline: true,
        },
      )
      .setColor(0xFEE75C)
      .setFooter({ text: `Message ID: ${newMessage.id}` })
      .setTimestamp();

    await logChannel.send({ embeds: [embed] });

    // Sauvegarder en DB
    await saveLog(newMessage.guild.id, author, oldMessage, newMessage);

  } catch (err) {
    log.error(`Erreur log messageUpdate dans ${newMessage.guild.name}: ${err.message}`);
  }
}

/**
 * Re-vérifie l'automod sur le contenu édité
 * (un user pourrait poster un message clean puis éditer pour mettre un lien interdit)
 */
async function recheckAutomod(newMessage, config) {
  if (!config.automod?.enabled) return;
  if (!newMessage.content) return;

  const member = newMessage.member;
  if (!member) return;

  // Ne pas vérifier les admins/modérateurs
  if (member.permissions.has('ManageMessages')) return;

  const content = newMessage.content.toLowerCase();

  try {
    // Anti-link
    if (config.automod.antiLink) {
      const urlRegex = /https?:\/\/[^\s]+/gi;
      if (urlRegex.test(content)) {
        // Vérifier les filtres automod personnalisés
        const db = getDb();
        const filters = await db('automod_filters')
          .where('guild_id', newMessage.guild.id)
          .where('type', 'domain')
          .where('enabled', true);

        for (const filter of filters) {
          if (content.includes(filter.pattern.toLowerCase())) {
            await newMessage.delete().catch(() => {});
            log.info(
              `Automod: message édité supprimé (lien interdit) — ` +
              `${member.user.tag} dans ${newMessage.guild.name}`
            );
            return;
          }
        }
      }
    }

    // Anti-mention mass (dans le contenu édité)
    if (config.automod.antiMention) {
      const mentionCount = (newMessage.content.match(/<@!?\d+>/g) || []).length;
      if (mentionCount >= 5) {
        await newMessage.delete().catch(() => {});
        log.info(
          `Automod: message édité supprimé (${mentionCount} mentions) — ` +
          `${member.user.tag} dans ${newMessage.guild.name}`
        );
        return;
      }
    }

    // Filtres mot/regex personnalisés
    const db = getDb();
    const wordFilters = await db('automod_filters')
      .where('guild_id', newMessage.guild.id)
      .whereIn('type', ['word', 'regex'])
      .where('enabled', true);

    for (const filter of wordFilters) {
      let matches = false;

      if (filter.type === 'word') {
        matches = content.includes(filter.pattern.toLowerCase());
      } else if (filter.type === 'regex') {
        try {
          const regex = new RegExp(filter.pattern, 'i');
          matches = regex.test(content);
        } catch {
          // Regex invalide — ignorer
        }
      }

      if (matches) {
        if (filter.action === 'delete' || filter.action === 'warn') {
          await newMessage.delete().catch(() => {});
        }
        log.info(
          `Automod: message édité filtré (${filter.type}: ${filter.pattern}) — ` +
          `${member.user.tag} dans ${newMessage.guild.name}`
        );
        return;
      }
    }
  } catch (err) {
    log.error(`Erreur automod re-check dans ${newMessage.guild.name}: ${err.message}`);
  }
}

/**
 * Sauvegarde le log d'édition en DB
 */
async function saveLog(guildId, author, oldMessage, newMessage) {
  try {
    const db = getDb();
    await db('logs').insert({
      guild_id: guildId,
      type: 'MESSAGE_EDIT',
      actor_id: author.id,
      target_id: newMessage.id,
      target_type: 'message',
      details: JSON.stringify({
        channel_id: newMessage.channel.id,
        old_content: oldMessage.content?.slice(0, 500) || null,
        new_content: newMessage.content?.slice(0, 500) || null,
      }),
    });
  } catch {
    // Non critique
  }
}