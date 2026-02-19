// ===================================
// Ultra Suite — XP: /leaderboard
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const userQueries = require('../../database/userQueries');
const { paginateEmbeds } = require('../../utils/embeds');
const { xpToLevel } = require('../../utils/formatters');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'xp',
  cooldown: 10,
  data: new SlashCommandBuilder()
    .setName('leaderboard')
    .setDescription('Affiche le classement XP du serveur')
    .addStringOption((opt) =>
      opt
        .setName('type')
        .setDescription('Type de classement')
        .addChoices(
          { name: 'XP', value: 'xp' },
          { name: 'Messages', value: 'total_messages' },
          { name: 'Vocal', value: 'voice_minutes' }
        )
    ),

  async execute(interaction) {
    const type = interaction.options.getString('type') || 'xp';
    const users = await userQueries.leaderboard(interaction.guild.id, 20);

    if (users.length === 0) {
      return interaction.reply({ content: t('xp.no_xp'), ephemeral: true });
    }

    // Sort by requested type
    users.sort((a, b) => b[type] - a[type]);

    const medals = ['🥇', '🥈', '🥉'];

    const formatter = (u, idx) => {
      const medal = medals[idx] || `**${idx + 1}.**`;
      const level = xpToLevel(u.xp);
      const value = type === 'xp' ? `${u.xp} XP (Niv. ${level})` : type === 'voice_minutes' ? `${u[type]} min` : `${u[type]}`;
      return `${medal} <@${u.user_id}> — ${value}`;
    };

    const titles = { xp: '🏆 Classement XP', total_messages: '💬 Classement Messages', voice_minutes: '🔊 Classement Vocal' };
    const pages = paginateEmbeds(users, 10, formatter, { title: titles[type] });

    await interaction.reply({ embeds: [pages[0]] });
  },
};
