// ===================================
// Ultra Suite — /twitch + /youtube + /rss
// Intégrations tierces
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'integrations',
  cooldown: 10,

  data: new SlashCommandBuilder()
    .setName('integration')
    .setDescription('Gérer les intégrations')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addSubcommandGroup((g) =>
      g.setName('twitch').setDescription('Intégration Twitch')
        .addSubcommand((s) => s.setName('add').setDescription('Suivre un streamer')
          .addStringOption((o) => o.setName('nom').setDescription('Nom du streamer').setRequired(true))
          .addChannelOption((o) => o.setName('salon').setDescription('Salon de notification').setRequired(true))
          .addStringOption((o) => o.setName('message').setDescription('Message personnalisé ({streamer}, {title}, {game}, {url})')))
        .addSubcommand((s) => s.setName('remove').setDescription('Ne plus suivre')
          .addStringOption((o) => o.setName('nom').setDescription('Nom du streamer').setRequired(true)))
        .addSubcommand((s) => s.setName('list').setDescription('Liste des streamers suivis')),
    )
    .addSubcommandGroup((g) =>
      g.setName('youtube').setDescription('Intégration YouTube')
        .addSubcommand((s) => s.setName('add').setDescription('Suivre une chaîne')
          .addStringOption((o) => o.setName('channel_id').setDescription('ID de la chaîne YouTube').setRequired(true))
          .addChannelOption((o) => o.setName('salon').setDescription('Salon de notification').setRequired(true))
          .addStringOption((o) => o.setName('message').setDescription('Message personnalisé ({channel}, {title}, {url})')))
        .addSubcommand((s) => s.setName('remove').setDescription('Ne plus suivre')
          .addStringOption((o) => o.setName('channel_id').setDescription('ID de la chaîne').setRequired(true)))
        .addSubcommand((s) => s.setName('list').setDescription('Liste des chaînes suivies')),
    )
    .addSubcommandGroup((g) =>
      g.setName('rss').setDescription('Flux RSS')
        .addSubcommand((s) => s.setName('add').setDescription('Ajouter un flux RSS')
          .addStringOption((o) => o.setName('url').setDescription('URL du flux RSS').setRequired(true))
          .addChannelOption((o) => o.setName('salon').setDescription('Salon de notification').setRequired(true))
          .addStringOption((o) => o.setName('nom').setDescription('Nom du flux')))
        .addSubcommand((s) => s.setName('remove').setDescription('Retirer un flux')
          .addStringOption((o) => o.setName('url').setDescription('URL du flux').setRequired(true)))
        .addSubcommand((s) => s.setName('list').setDescription('Liste des flux RSS')),
    ),

  async execute(interaction) {
    const group = interaction.options.getSubcommandGroup();
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    // All integrations stored in a generic table
    const tableName = 'guild_integrations';
    const ensureTable = async () => {
      const exists = await db.schema.hasTable(tableName);
      if (!exists) {
        await db.schema.createTable(tableName, (t) => {
          t.increments('id');
          t.string('guild_id');
          t.string('type'); // twitch, youtube, rss
          t.string('identifier'); // streamer name, channel ID, RSS URL
          t.string('channel_id');
          t.string('custom_message');
          t.string('name');
          t.string('last_item_id');
          t.timestamps(true, true);
          t.unique(['guild_id', 'type', 'identifier']);
        });
      }
    };

    await ensureTable();

    switch (group) {
      case 'twitch': {
        if (sub === 'add') {
          const name = interaction.options.getString('nom').toLowerCase();
          const channel = interaction.options.getChannel('salon');
          const message = interaction.options.getString('message') || '🟣 **{streamer}** est en live ! {title}\n{url}';

          await db(tableName).insert({
            guild_id: guildId, type: 'twitch', identifier: name,
            channel_id: channel.id, custom_message: message, name,
          }).onConflict(['guild_id', 'type', 'identifier']).merge();

          return interaction.reply({ content: `✅ Streamer **${name}** ajouté ! Notifications dans ${channel}.`, ephemeral: true });
        }
        if (sub === 'remove') {
          const name = interaction.options.getString('nom').toLowerCase();
          await db(tableName).where({ guild_id: guildId, type: 'twitch', identifier: name }).delete();
          return interaction.reply({ content: `✅ Streamer **${name}** retiré.`, ephemeral: true });
        }
        if (sub === 'list') {
          const items = await db(tableName).where({ guild_id: guildId, type: 'twitch' });
          if (!items.length) return interaction.reply({ content: 'Aucun streamer suivi.', ephemeral: true });
          const embed = new EmbedBuilder()
            .setTitle('🟣 Streamers Twitch suivis')
            .setColor(0x9146FF)
            .setDescription(items.map((i) => `• **${i.name}** → <#${i.channel_id}>`).join('\n'));
          return interaction.reply({ embeds: [embed], ephemeral: true });
        }
        break;
      }

      case 'youtube': {
        if (sub === 'add') {
          const channelId = interaction.options.getString('channel_id');
          const channel = interaction.options.getChannel('salon');
          const message = interaction.options.getString('message') || '🔴 **{channel}** a posté une nouvelle vidéo !\n{title}\n{url}';

          await db(tableName).insert({
            guild_id: guildId, type: 'youtube', identifier: channelId,
            channel_id: channel.id, custom_message: message, name: channelId,
          }).onConflict(['guild_id', 'type', 'identifier']).merge();

          return interaction.reply({ content: `✅ Chaîne YouTube ajoutée ! Notifications dans ${channel}.`, ephemeral: true });
        }
        if (sub === 'remove') {
          const channelId = interaction.options.getString('channel_id');
          await db(tableName).where({ guild_id: guildId, type: 'youtube', identifier: channelId }).delete();
          return interaction.reply({ content: '✅ Chaîne retirée.', ephemeral: true });
        }
        if (sub === 'list') {
          const items = await db(tableName).where({ guild_id: guildId, type: 'youtube' });
          if (!items.length) return interaction.reply({ content: 'Aucune chaîne suivie.', ephemeral: true });
          const embed = new EmbedBuilder()
            .setTitle('🔴 Chaînes YouTube suivies')
            .setColor(0xFF0000)
            .setDescription(items.map((i) => `• **${i.name}** → <#${i.channel_id}>`).join('\n'));
          return interaction.reply({ embeds: [embed], ephemeral: true });
        }
        break;
      }

      case 'rss': {
        if (sub === 'add') {
          const url = interaction.options.getString('url');
          const channel = interaction.options.getChannel('salon');
          const name = interaction.options.getString('nom') || url;

          await db(tableName).insert({
            guild_id: guildId, type: 'rss', identifier: url,
            channel_id: channel.id, name,
          }).onConflict(['guild_id', 'type', 'identifier']).merge();

          // Try to fetch the feed
          try {
            const RssParser = require('rss-parser');
            const parser = new RssParser();
            const feed = await parser.parseURL(url);
            return interaction.reply({ content: `✅ Flux RSS **${feed.title || name}** ajouté ! (${feed.items?.length || 0} articles)`, ephemeral: true });
          } catch (e) {
            return interaction.reply({ content: `✅ Flux RSS ajouté ! (Impossible de vérifier le flux : ${e.message})`, ephemeral: true });
          }
        }
        if (sub === 'remove') {
          const url = interaction.options.getString('url');
          await db(tableName).where({ guild_id: guildId, type: 'rss', identifier: url }).delete();
          return interaction.reply({ content: '✅ Flux retiré.', ephemeral: true });
        }
        if (sub === 'list') {
          const items = await db(tableName).where({ guild_id: guildId, type: 'rss' });
          if (!items.length) return interaction.reply({ content: 'Aucun flux RSS.', ephemeral: true });
          const embed = new EmbedBuilder()
            .setTitle('📰 Flux RSS')
            .setColor(0xFF8C00)
            .setDescription(items.map((i) => `• **${i.name}** → <#${i.channel_id}>`).join('\n'));
          return interaction.reply({ embeds: [embed], ephemeral: true });
        }
        break;
      }
    }
  },
};
