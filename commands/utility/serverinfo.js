// ===================================
// Ultra Suite — Utility: /serverinfo
// ===================================

const { SlashCommandBuilder, ChannelType } = require('discord.js');
const { createEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'utility',
  data: new SlashCommandBuilder()
    .setName('serverinfo')
    .setDescription('Affiche les informations du serveur'),

  async execute(interaction) {
    const { guild } = interaction;
    await guild.members.fetch().catch(() => {});

    const online = guild.members.cache.filter((m) => m.presence?.status && m.presence.status !== 'offline').size;
    const bots = guild.members.cache.filter((m) => m.user.bot).size;
    const humans = guild.memberCount - bots;
    const textChannels = guild.channels.cache.filter((c) => c.type === ChannelType.GuildText).size;
    const voiceChannels = guild.channels.cache.filter((c) => c.type === ChannelType.GuildVoice).size;
    const categories = guild.channels.cache.filter((c) => c.type === ChannelType.GuildCategory).size;
    const roles = guild.roles.cache.size - 1; // exclude @everyone
    const emojis = guild.emojis.cache.size;
    const boostLevel = guild.premiumTier;
    const boosts = guild.premiumSubscriptionCount || 0;

    const verificationLevels = { 0: 'Aucun', 1: 'Faible', 2: 'Moyen', 3: 'Élevé', 4: 'Très élevé' };

    const embed = createEmbed('primary')
      .setTitle(guild.name)
      .setThumbnail(guild.iconURL({ dynamic: true, size: 256 }))
      .addFields(
        { name: '👑 Propriétaire', value: `<@${guild.ownerId}>`, inline: true },
        { name: '📅 Création', value: `<t:${Math.floor(guild.createdTimestamp / 1000)}:R>`, inline: true },
        { name: '🆔 ID', value: guild.id, inline: true },
        { name: `👥 Membres (${guild.memberCount})`, value: `Humains: ${humans}\nBots: ${bots}\nEn ligne: ${online}`, inline: true },
        { name: `💬 Salons (${guild.channels.cache.size})`, value: `Texte: ${textChannels}\nVocal: ${voiceChannels}\nCatégories: ${categories}`, inline: true },
        { name: `🎭 Rôles`, value: `${roles}`, inline: true },
        { name: '😀 Emojis', value: `${emojis}`, inline: true },
        { name: '🔒 Vérification', value: verificationLevels[guild.verificationLevel] || 'N/A', inline: true },
        { name: '🚀 Boosts', value: `Niveau ${boostLevel} (${boosts} boosts)`, inline: true }
      );

    if (guild.bannerURL()) embed.setImage(guild.bannerURL({ size: 512 }));

    return interaction.reply({ embeds: [embed] });
  },
};
