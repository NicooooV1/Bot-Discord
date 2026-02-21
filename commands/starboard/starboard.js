// ===================================
// Ultra Suite — /starboard
// Configuration du starboard
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'starboard',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('starboard')
    .setDescription('Configurer le starboard')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addSubcommand((s) =>
      s.setName('setup').setDescription('Configurer un starboard')
        .addChannelOption((o) => o.setName('salon').setDescription('Salon du starboard').setRequired(true))
        .addStringOption((o) => o.setName('emoji').setDescription('Emoji (défaut: ⭐)'))
        .addIntegerOption((o) => o.setName('seuil').setDescription('Seuil de réactions (défaut: 3)').setMinValue(1))
        .addStringOption((o) => o.setName('nom').setDescription('Nom du board (défaut: default)')),
    )
    .addSubcommand((s) =>
      s.setName('config').setDescription('Modifier la configuration')
        .addBooleanOption((o) => o.setName('self_star').setDescription('Autoriser l\'auto-star'))
        .addBooleanOption((o) => o.setName('auto_pin').setDescription('Auto-épingler au-delà du seuil'))
        .addBooleanOption((o) => o.setName('notifier').setDescription('Notifier l\'auteur'))
        .addBooleanOption((o) => o.setName('media_only').setDescription('Uniquement les messages avec médias'))
        .addBooleanOption((o) => o.setName('nsfw_filter').setDescription('Filtrer le NSFW')),
    )
    .addSubcommand((s) =>
      s.setName('blacklist').setDescription('Blacklister un salon')
        .addChannelOption((o) => o.setName('salon').setDescription('Salon à blacklister').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('top').setDescription('Voir les messages les plus étoilés')
        .addIntegerOption((o) => o.setName('nombre').setDescription('Nombre de messages').setMinValue(1).setMaxValue(25)),
    )
    .addSubcommand((s) =>
      s.setName('leaderboard').setDescription('Classement des auteurs les plus étoilés'),
    )
    .addSubcommand((s) =>
      s.setName('random').setDescription('Message étoilé aléatoire'),
    )
    .addSubcommand((s) =>
      s.setName('stats').setDescription('Statistiques du starboard'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();
    const config = await configService.get(guildId);

    switch (sub) {
      case 'setup': {
        const channel = interaction.options.getChannel('salon');
        const emoji = interaction.options.getString('emoji') || '⭐';
        const threshold = interaction.options.getInteger('seuil') || 3;
        const boardName = interaction.options.getString('nom') || 'default';

        await db('starboard_config')
          .insert({ guild_id: guildId, board_name: boardName, channel_id: channel.id, emoji, threshold, enabled: true })
          .onConflict(['guild_id', 'board_name'])
          .merge({ channel_id: channel.id, emoji, threshold });

        return interaction.reply({
          content: `⭐ Starboard **${boardName}** configuré !\n> Salon: ${channel}\n> Emoji: ${emoji}\n> Seuil: ${threshold} réaction(s)`,
          ephemeral: true,
        });
      }

      case 'config': {
        const selfStar = interaction.options.getBoolean('self_star');
        const autoPin = interaction.options.getBoolean('auto_pin');
        const notify = interaction.options.getBoolean('notifier');
        const mediaOnly = interaction.options.getBoolean('media_only');
        const nsfwFilter = interaction.options.getBoolean('nsfw_filter');

        const updates = {};
        if (selfStar !== null) updates.self_star = selfStar;
        if (autoPin !== null) updates.auto_pin = autoPin;
        if (notify !== null) updates.notify_author = notify;

        const starboardConfig = config.starboard || {};
        if (mediaOnly !== null) starboardConfig.mediaOnly = mediaOnly;
        if (nsfwFilter !== null) starboardConfig.nsfwFilter = nsfwFilter;

        if (Object.keys(updates).length) {
          await db('starboard_config').where({ guild_id: guildId, board_name: 'default' }).update(updates);
        }
        await configService.update(guildId, { starboard: starboardConfig });

        return interaction.reply({ content: '✅ Configuration du starboard mise à jour.', ephemeral: true });
      }

      case 'blacklist': {
        const channel = interaction.options.getChannel('salon');
        const board = await db('starboard_config').where({ guild_id: guildId, board_name: 'default' }).first();
        if (!board) return interaction.reply({ content: '❌ Configurez d\'abord le starboard.', ephemeral: true });

        const list = JSON.parse(board.blacklisted_channels || '[]');
        const idx = list.indexOf(channel.id);
        if (idx >= 0) list.splice(idx, 1);
        else list.push(channel.id);

        await db('starboard_config').where({ id: board.id }).update({ blacklisted_channels: JSON.stringify(list) });
        return interaction.reply({ content: `✅ ${channel} ${idx >= 0 ? 'retiré de' : 'ajouté à'} la blacklist.`, ephemeral: true });
      }

      case 'top': {
        const count = interaction.options.getInteger('nombre') || 10;
        const entries = await db('starboard_entries')
          .where({ guild_id: guildId })
          .orderBy('star_count', 'desc')
          .limit(count);

        if (!entries.length) return interaction.reply({ content: '⭐ Aucune entrée starboard.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle('⭐ Top Starboard')
          .setColor(0xFFD700)
          .setDescription(entries.map((e, i) =>
            `**#${i + 1}** — ⭐ **${e.star_count}** | Par <@${e.author_id}> dans <#${e.original_channel_id}>\n> [Lien](https://discord.com/channels/${guildId}/${e.original_channel_id}/${e.original_message_id})`,
          ).join('\n\n'))
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }

      case 'leaderboard': {
        const leaders = await db('starboard_entries')
          .where({ guild_id: guildId })
          .select('author_id')
          .sum('star_count as total')
          .count('id as posts')
          .groupBy('author_id')
          .orderBy('total', 'desc')
          .limit(10);

        if (!leaders.length) return interaction.reply({ content: '⭐ Aucune donnée.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle('⭐ Classement Starboard')
          .setColor(0xFFD700)
          .setDescription(leaders.map((l, i) =>
            `**#${i + 1}** <@${l.author_id}> — ⭐ **${l.total}** étoiles · ${l.posts} message(s)`,
          ).join('\n'))
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }

      case 'random': {
        const entries = await db('starboard_entries').where({ guild_id: guildId }).where('star_count', '>=', 3);
        if (!entries.length) return interaction.reply({ content: '⭐ Aucun message étoilé.', ephemeral: true });

        const entry = entries[Math.floor(Math.random() * entries.length)];
        const embed = new EmbedBuilder()
          .setTitle('🌟 Message étoilé aléatoire')
          .setColor(0xFFD700)
          .setDescription(`⭐ **${entry.star_count}** | Par <@${entry.author_id}>`)
          .addFields({ name: 'Lien', value: `[Aller au message](https://discord.com/channels/${guildId}/${entry.original_channel_id}/${entry.original_message_id})` })
          .setTimestamp(new Date(entry.created_at));

        return interaction.reply({ embeds: [embed] });
      }

      case 'stats': {
        const total = await db('starboard_entries').where({ guild_id: guildId }).count('id as c').first();
        const stars = await db('starboard_entries').where({ guild_id: guildId }).sum('star_count as s').first();
        const topAuthor = await db('starboard_entries').where({ guild_id: guildId })
          .select('author_id').sum('star_count as total').groupBy('author_id').orderBy('total', 'desc').first();

        const embed = new EmbedBuilder()
          .setTitle('📊 Statistiques Starboard')
          .setColor(0xFFD700)
          .addFields(
            { name: '📝 Messages étoilés', value: `${total?.c || 0}`, inline: true },
            { name: '⭐ Total étoiles', value: `${stars?.s || 0}`, inline: true },
            { name: '🏆 Top auteur', value: topAuthor ? `<@${topAuthor.author_id}> (${topAuthor.total}⭐)` : 'N/A', inline: true },
          )
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }
    }
  },
};
