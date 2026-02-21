// ===================================
// Ultra Suite — /forum
// Gestion des forums
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ChannelType } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'forums',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('forum')
    .setDescription('Gérer les forums')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageChannels)
    .addSubcommand((s) =>
      s.setName('setup').setDescription('Configurer un forum')
        .addChannelOption((o) => o.setName('salon').setDescription('Le forum').setRequired(true))
        .addBooleanOption((o) => o.setName('auto_tag').setDescription('Auto-tag les posts'))
        .addBooleanOption((o) => o.setName('auto_react').setDescription('Réactions auto (👍/👎)'))
        .addBooleanOption((o) => o.setName('auto_thread').setDescription('Transformer en suggestion')),
    )
    .addSubcommand((s) =>
      s.setName('template').setDescription('Définir un template pour les posts')
        .addChannelOption((o) => o.setName('salon').setDescription('Le forum').setRequired(true))
        .addStringOption((o) => o.setName('template').setDescription('Template (variables: {author}, {date})').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('config').setDescription('Voir la config d\'un forum')
        .addChannelOption((o) => o.setName('salon').setDescription('Le forum').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('lock').setDescription('Verrouiller/déverrouiller un post')
        .addStringOption((o) => o.setName('raison').setDescription('Raison')),
    )
    .addSubcommand((s) =>
      s.setName('stats').setDescription('Statistiques du forum')
        .addChannelOption((o) => o.setName('salon').setDescription('Le forum').setRequired(true)),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    switch (sub) {
      case 'setup': {
        const channel = interaction.options.getChannel('salon');
        if (channel.type !== ChannelType.GuildForum) return interaction.reply({ content: '❌ Ce salon n\'est pas un forum.', ephemeral: true });

        const autoTag = interaction.options.getBoolean('auto_tag') ?? false;
        const autoReact = interaction.options.getBoolean('auto_react') ?? false;
        const autoThread = interaction.options.getBoolean('auto_thread') ?? false;

        await db('forum_config').insert({
          guild_id: guildId,
          channel_id: channel.id,
          auto_tag: autoTag,
          auto_react: autoReact,
          auto_thread: autoThread,
          template: null,
        }).onConflict(['guild_id', 'channel_id']).merge();

        return interaction.reply({
          content: `✅ Forum ${channel} configuré !\n• Auto-tag: ${autoTag ? '✅' : '❌'}\n• Auto-react: ${autoReact ? '✅' : '❌'}\n• Auto-thread: ${autoThread ? '✅' : '❌'}`,
          ephemeral: true,
        });
      }

      case 'template': {
        const channel = interaction.options.getChannel('salon');
        const template = interaction.options.getString('template');

        await db('forum_config').insert({
          guild_id: guildId,
          channel_id: channel.id,
          template,
        }).onConflict(['guild_id', 'channel_id']).merge();

        return interaction.reply({ content: `✅ Template défini pour ${channel}.`, ephemeral: true });
      }

      case 'config': {
        const channel = interaction.options.getChannel('salon');
        const config = await db('forum_config').where({ guild_id: guildId, channel_id: channel.id }).first();
        if (!config) return interaction.reply({ content: '❌ Aucune configuration pour ce forum.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle(`📋 Config forum — #${channel.name}`)
          .setColor(0x3498DB)
          .addFields(
            { name: 'Auto-tag', value: config.auto_tag ? '✅' : '❌', inline: true },
            { name: 'Auto-react', value: config.auto_react ? '✅' : '❌', inline: true },
            { name: 'Auto-thread', value: config.auto_thread ? '✅' : '❌', inline: true },
            { name: 'Template', value: config.template || '*Aucun*' },
          );

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'lock': {
        const thread = interaction.channel;
        if (!thread.isThread()) return interaction.reply({ content: '❌ Cette commande doit être utilisée dans un thread/post.', ephemeral: true });

        const reason = interaction.options.getString('raison') || 'Aucune raison';
        const locked = thread.locked;

        await thread.setLocked(!locked, reason);

        return interaction.reply({
          embeds: [new EmbedBuilder()
            .setColor(locked ? 0x2ECC71 : 0xE74C3C)
            .setDescription(`${locked ? '🔓' : '🔒'} Post ${locked ? 'déverrouillé' : 'verrouillé'} — ${reason}`)],
        });
      }

      case 'stats': {
        const channel = interaction.options.getChannel('salon');
        if (channel.type !== ChannelType.GuildForum) return interaction.reply({ content: '❌ Ce n\'est pas un forum.', ephemeral: true });

        const threads = await channel.threads.fetchActive();
        const archived = await channel.threads.fetchArchived().catch(() => ({ threads: new Map() }));

        const embed = new EmbedBuilder()
          .setTitle(`📊 Stats — #${channel.name}`)
          .setColor(0x3498DB)
          .addFields(
            { name: '🟢 Posts actifs', value: String(threads.threads.size), inline: true },
            { name: '📦 Posts archivés', value: String(archived.threads.size), inline: true },
            { name: '📌 Tags', value: String(channel.availableTags?.length || 0), inline: true },
          );

        return interaction.reply({ embeds: [embed] });
      }
    }
  },
};
