// ===================================
// Ultra Suite — Event: messageDelete
// Déclenché quand un message est supprimé
//
// Actions (si module logs activé) :
// 1. Log le contenu du message supprimé
// 2. Inclut les attachments (images, fichiers)
// 3. Tente de trouver qui a supprimé via audit log
// 4. Ignore les bots et les messages système
//
// Remplace messageDelete.legacy
// ===================================

const { EmbedBuilder, AuditLogEvent } = require('discord.js');
const configService = require('../core/configService');
const { getDb } = require('../database');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('MessageDelete');

module.exports = {
  name: 'messageDelete',
  once: false,

  /**
   * @param {import('discord.js').Message} message
   */
  async execute(message) {
    // Ignorer les DMs
    if (!message.guild) return;

    // Ignorer les messages partiels sans contenu (non cachés)
    if (!message.author) return;

    // Ignorer les bots
    if (message.author.bot) return;

    const guildId = message.guild.id;

    // Vérifier que le module logs est activé
    if (!(await configService.isModuleEnabled(guildId, 'logs'))) return;

    const config = await configService.get(guildId);
    const logChannelId = config.logChannel;
    if (!logChannelId) return;

    // Ne pas logger les suppressions dans le channel de logs lui-même
    if (message.channel.id === logChannelId) return;

    const logChannel = message.guild.channels.cache.get(logChannelId);
    if (!logChannel) return;

    try {
      // Tenter de trouver qui a supprimé le message
      const deletedBy = await detectDeletor(message);

      // Construire l'embed
      const embed = new EmbedBuilder()
        .setAuthor({
          name: `Message supprimé — ${message.author.tag}`,
          iconURL: message.author.displayAvatarURL(),
        })
        .setColor(0xED4245)
        .setTimestamp();

      // Contenu du message
      const content = message.content || '*Pas de texte*';
      // Tronquer si trop long (embed limit = 4096)
      embed.setDescription(content.length > 2000
        ? content.slice(0, 2000) + '... (tronqué)'
        : content
      );

      // Champs info
      embed.addFields(
        { name: 'Channel', value: `${message.channel} (${message.channel.name})`, inline: true },
        { name: 'Auteur', value: `${message.author} (${message.author.id})`, inline: true },
      );

      // Qui a supprimé ?
      if (deletedBy) {
        embed.addFields({
          name: 'Supprimé par',
          value: deletedBy,
          inline: true,
        });
      }

      // Message ID et date de création
      embed.addFields({
        name: 'Message ID',
        value: `\`${message.id}\` — envoyé <t:${Math.floor(message.createdTimestamp / 1000)}:R>`,
        inline: false,
      });

      // Attachments (images, fichiers)
      if (message.attachments.size > 0) {
        const attachList = message.attachments.map((a) => {
          const size = a.size ? ` (${(a.size / 1024).toFixed(1)} KB)` : '';
          return `[${a.name || 'fichier'}](${a.proxyURL})${size}`;
        }).join('\n');

        embed.addFields({
          name: `Pièces jointes (${message.attachments.size})`,
          value: attachList.slice(0, 1024),
          inline: false,
        });

        // Afficher la première image en preview
        const firstImage = message.attachments.find((a) =>
          a.contentType?.startsWith('image/')
        );
        if (firstImage) {
          embed.setImage(firstImage.proxyURL);
        }
      }

      // Embeds dans le message original
      if (message.embeds.length > 0) {
        embed.addFields({
          name: 'Embeds',
          value: `${message.embeds.length} embed(s) dans le message original`,
          inline: false,
        });
      }

      // Stickers
      if (message.stickers.size > 0) {
        const stickerNames = message.stickers.map((s) => s.name).join(', ');
        embed.addFields({
          name: 'Stickers',
          value: stickerNames,
          inline: false,
        });
      }

      await logChannel.send({ embeds: [embed] });

      // Sauvegarder en DB
      await saveLog(guildId, message, deletedBy);

    } catch (err) {
      log.error(`Erreur log messageDelete dans ${message.guild.name}: ${err.message}`);
    }
  },
};

/**
 * Tente de détecter qui a supprimé le message via l'audit log
 * @returns {string|null}
 */
async function detectDeletor(message) {
  try {
    const botMember = message.guild.members.me;
    if (!botMember?.permissions?.has('ViewAuditLog')) return null;

    const logs = await message.guild.fetchAuditLogs({
      type: AuditLogEvent.MessageDelete,
      limit: 5,
    });

    const entry = logs.entries.find(
      (e) =>
        e.target?.id === message.author.id &&
        e.extra?.channel?.id === message.channel.id &&
        Date.now() - e.createdTimestamp < 5000
    );

    if (entry && entry.executor) {
      return `${entry.executor.tag} (${entry.executor.id})`;
    }

    return null;
  } catch {
    return null;
  }
}

/**
 * Sauvegarde le log en DB pour historique
 */
async function saveLog(guildId, message, deletedBy) {
  try {
    const db = getDb();
    await db('logs').insert({
      guild_id: guildId,
      type: 'MESSAGE_DELETE',
      actor_id: deletedBy ? null : message.author.id,
      target_id: message.author.id,
      target_type: 'message',
      details: JSON.stringify({
        channel_id: message.channel.id,
        message_id: message.id,
        content: message.content?.slice(0, 500) || null,
        attachments: message.attachments.map((a) => a.url),
        deleted_by: deletedBy || null,
      }),
    });
  } catch {
    // Non critique
  }
}