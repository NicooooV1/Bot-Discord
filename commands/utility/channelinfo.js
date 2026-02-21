// ===================================
// Ultra Suite — /channelinfo
// Informations sur un salon
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ChannelType } = require('discord.js');

const channelTypes = {
  [ChannelType.GuildText]: '💬 Salon Texte',
  [ChannelType.GuildVoice]: '🔊 Salon Vocal',
  [ChannelType.GuildCategory]: '📁 Catégorie',
  [ChannelType.GuildAnnouncement]: '📢 Annonces',
  [ChannelType.GuildStageVoice]: '🎙️ Stage',
  [ChannelType.GuildForum]: '📋 Forum',
  [ChannelType.PublicThread]: '🧵 Thread Public',
  [ChannelType.PrivateThread]: '🔒 Thread Privé',
  [ChannelType.GuildMedia]: '🎬 Media',
};

module.exports = {
  module: 'utility',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('channelinfo')
    .setDescription('Afficher les informations d\'un salon')
    .addChannelOption((o) => o.setName('salon').setDescription('Le salon')),

  async execute(interaction) {
    const channel = interaction.options.getChannel('salon') || interaction.channel;

    const embed = new EmbedBuilder()
      .setTitle(`📺 Salon — #${channel.name}`)
      .setColor(0x3498DB)
      .addFields(
        { name: '🆔 ID', value: channel.id, inline: true },
        { name: '📝 Type', value: channelTypes[channel.type] || 'Inconnu', inline: true },
        { name: '📅 Créé le', value: `<t:${Math.floor(channel.createdTimestamp / 1000)}:F>`, inline: true },
      );

    if (channel.topic) embed.addFields({ name: '📌 Sujet', value: channel.topic.substring(0, 1024) });
    if (channel.rateLimitPerUser) embed.addFields({ name: '🐌 Slowmode', value: `${channel.rateLimitPerUser}s`, inline: true });
    if (channel.nsfw !== undefined) embed.addFields({ name: '🔞 NSFW', value: channel.nsfw ? '✅' : '❌', inline: true });
    if (channel.parentId) embed.addFields({ name: '📁 Catégorie', value: `<#${channel.parentId}>`, inline: true });
    if (channel.bitrate) embed.addFields({ name: '🎵 Bitrate', value: `${channel.bitrate / 1000}kbps`, inline: true });
    if (channel.userLimit) embed.addFields({ name: '👥 Limite', value: String(channel.userLimit), inline: true });

    const overwrites = channel.permissionOverwrites?.cache;
    if (overwrites?.size) {
      embed.addFields({ name: '🔐 Overrides', value: `${overwrites.size} permission override(s)`, inline: true });
    }

    return interaction.reply({ embeds: [embed] });
  },
};
