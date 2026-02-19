const { SlashCommandBuilder, EmbedBuilder, ChannelType } = require('discord.js');
const { COLORS } = require('../../utils/logger');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('serverinfo')
    .setDescription('📊 Afficher les informations du serveur'),

  async execute(interaction) {
    const guild = interaction.guild;

    await guild.members.fetch();

    const totalMembers = guild.memberCount;
    const humans = guild.members.cache.filter(m => !m.user.bot).size;
    const bots = guild.members.cache.filter(m => m.user.bot).size;
    const online = guild.members.cache.filter(m => m.presence?.status === 'online').size;

    const textChannels = guild.channels.cache.filter(c => c.type === ChannelType.GuildText).size;
    const voiceChannels = guild.channels.cache.filter(c => c.type === ChannelType.GuildVoice).size;
    const categories = guild.channels.cache.filter(c => c.type === ChannelType.GuildCategory).size;

    const roles = guild.roles.cache.size - 1; // Exclure @everyone
    const emojis = guild.emojis.cache.size;
    const boosts = guild.premiumSubscriptionCount || 0;

    const verificationLevels = {
      0: 'Aucune',
      1: 'Faible',
      2: 'Moyenne',
      3: 'Élevée',
      4: 'Très élevée',
    };

    const embed = new EmbedBuilder()
      .setTitle(`📊 ${guild.name}`)
      .setColor(COLORS.BLUE)
      .setThumbnail(guild.iconURL({ dynamic: true, size: 256 }))
      .addFields(
        { name: '👑 Propriétaire', value: `<@${guild.ownerId}>`, inline: true },
        { name: '🆔 ID', value: guild.id, inline: true },
        { name: '📅 Créé le', value: `<t:${Math.floor(guild.createdTimestamp / 1000)}:D>`, inline: true },
        {
          name: `👥 Membres (${totalMembers})`,
          value: `🧑 Humains: **${humans}**\n🤖 Bots: **${bots}**\n🟢 En ligne: **${online}**`,
          inline: true,
        },
        {
          name: `💬 Salons (${guild.channels.cache.size})`,
          value: `📝 Textuels: **${textChannels}**\n🔊 Vocaux: **${voiceChannels}**\n📁 Catégories: **${categories}**`,
          inline: true,
        },
        {
          name: '📋 Divers',
          value: `🎭 Rôles: **${roles}**\n😀 Emojis: **${emojis}**\n💎 Boosts: **${boosts}** (Niveau ${guild.premiumTier})`,
          inline: true,
        },
        { name: '🔒 Vérification', value: verificationLevels[guild.verificationLevel] || 'Inconnue', inline: true },
      )
      .setTimestamp();

    if (guild.bannerURL()) {
      embed.setImage(guild.bannerURL({ dynamic: true, size: 512 }));
    }

    await interaction.reply({ embeds: [embed] });
  },
};
