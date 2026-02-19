// ===================================
// Ultra Suite — XP: /rank
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const userQueries = require('../../database/userQueries');
const { createEmbed } = require('../../utils/embeds');
const { xpToLevel, xpForNextLevel, progressBar } = require('../../utils/formatters');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'xp',
  cooldown: 5,
  data: new SlashCommandBuilder()
    .setName('rank')
    .setDescription('Affiche votre rang XP ou celui d\'un membre')
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à consulter')),

  async execute(interaction) {
    const user = interaction.options.getUser('membre') || interaction.user;
    const dbUser = await userQueries.getOrCreate(user.id, interaction.guild.id);
    const rank = await userQueries.rank(user.id, interaction.guild.id);
    const needed = xpForNextLevel(dbUser.xp);
    const level = xpToLevel(dbUser.xp);
    const prevLevelXp = Math.pow(level / 0.1, 2);
    const progress = progressBar(dbUser.xp - prevLevelXp, needed - prevLevelXp, 15);

    const embed = createEmbed('primary')
      .setTitle(`📊 Rang de ${user.username}`)
      .setThumbnail(user.displayAvatarURL({ size: 256 }))
      .addFields(
        { name: 'Niveau', value: `**${level}**`, inline: true },
        { name: 'XP', value: `**${dbUser.xp}** / ${needed}`, inline: true },
        { name: 'Rang', value: `#${rank || '?'}`, inline: true },
        { name: 'Progression', value: `${progress}`, inline: false },
        { name: 'Messages', value: `${dbUser.total_messages}`, inline: true },
        { name: 'Vocal', value: `${dbUser.voice_minutes} min`, inline: true }
      );

    await interaction.reply({ embeds: [embed] });
  },
};
