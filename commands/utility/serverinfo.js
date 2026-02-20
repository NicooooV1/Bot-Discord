// ===================================
// Ultra Suite — /serverinfo
// Informations détaillées sur le serveur
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ChannelType } = require('discord.js');
const { getDb } = require('../../database');
const configService = require('../../core/configService');

module.exports = {
  module: 'utility',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('serverinfo')
    .setDescription('Voir les informations du serveur'),

  async execute(interaction) {
    const guild = interaction.guild;
    const guildId = guild.id;
    const db = getDb();

    // Fetch le owner
    const owner = await guild.fetchOwner().catch(() => null);

    // Compteurs de channels
    const channels = guild.channels.cache;
    const textChannels = channels.filter((c) => c.type === ChannelType.GuildText).size;
    const voiceChannels = channels.filter((c) => c.type === ChannelType.GuildVoice).size;
    const categories = channels.filter((c) => c.type === ChannelType.GuildCategory).size;
    const forumChannels = channels.filter((c) => c.type === ChannelType.GuildForum).size;
    const stageChannels = channels.filter((c) => c.type === ChannelType.GuildStageVoice).size;

    // Compteur de rôles
    const roles = guild.roles.cache.size - 1; // -1 pour @everyone

    // Emojis et stickers
    const emojis = guild.emojis.cache;
    const animatedEmojis = emojis.filter((e) => e.animated).size;
    const staticEmojis = emojis.size - animatedEmojis;

    // Niveau de boost
    const boostLevel = guild.premiumTier;
    const boostCount = guild.premiumSubscriptionCount || 0;

    // Vérification
    const verificationLevels = {
      0: 'Aucune',
      1: 'Faible (email vérifié)',
      2: 'Moyenne (inscrit > 5min)',
      3: 'Haute (membre > 10min)',
      4: 'Très haute (téléphone vérifié)',
    };

    const embed = new EmbedBuilder()
      .setTitle(guild.name)
      .setThumbnail(guild.iconURL({ size: 512 }))
      .setColor(0x5865F2)
      .addFields(
        { name: '🆔 ID', value: guild.id, inline: true },
        { name: '👑 Propriétaire', value: owner?.user?.tag || 'Inconnu', inline: true },
        {
          name: '📅 Créé le',
          value: `<t:${Math.floor(guild.createdTimestamp / 1000)}:f>\n(<t:${Math.floor(guild.createdTimestamp / 1000)}:R>)`,
          inline: true,
        },
        {
          name: `👥 Membres (${guild.memberCount})`,
          value: `En ligne estimé : ~${guild.approximatePresenceCount || '?'}`,
          inline: true,
        },
        {
          name: `💬 Channels (${channels.size})`,
          value: [
            `📝 Texte : ${textChannels}`,
            `🔊 Vocal : ${voiceChannels}`,
            `📁 Catégories : ${categories}`,
            forumChannels > 0 ? `💬 Forums : ${forumChannels}` : null,
            stageChannels > 0 ? `🎤 Stage : ${stageChannels}` : null,
          ].filter(Boolean).join('\n'),
          inline: true,
        },
        { name: `🎭 Rôles`, value: String(roles), inline: true },
        {
          name: `😀 Emojis (${emojis.size})`,
          value: `Statiques : ${staticEmojis} | Animés : ${animatedEmojis}`,
          inline: true,
        },
        {
          name: `💎 Boost`,
          value: `Niveau ${boostLevel} (${boostCount} boost${boostCount > 1 ? 's' : ''})`,
          inline: true,
        },
        { name: '🔒 Vérification', value: verificationLevels[guild.verificationLevel] || 'Inconnue', inline: true },
      )
      .setTimestamp();

    // Banner si disponible
    if (guild.bannerURL()) {
      embed.setImage(guild.bannerURL({ size: 1024 }));
    }

    // Stats DB
    try {
      const modules = await configService.getModules(guildId);
      const enabledCount = Object.values(modules).filter(Boolean).length;
      const totalCount = Object.keys(modules).length;

      const totalMessages = await db('users')
        .where('guild_id', guildId)
        .sum('total_messages as total')
        .first();

      const totalSanctions = await db('sanctions')
        .where('guild_id', guildId)
        .count('id as count')
        .first();

      embed.addFields({
        name: '🤖 Ultra Suite',
        value: [
          `Modules : ${enabledCount}/${totalCount} activés`,
          `Messages trackés : ${(totalMessages?.total || 0).toLocaleString('fr-FR')}`,
          `Sanctions : ${totalSanctions?.count || 0}`,
        ].join('\n'),
        inline: false,
      });
    } catch {
      // DB pas dispo — pas grave
    }

    return interaction.reply({ embeds: [embed] });
  },
};