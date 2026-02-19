// ===================================
// Ultra Suite — Stats: /stats
// Statistiques du bot et du serveur
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const { createEmbed } = require('../../utils/embeds');
const { getDb } = require('../../database');
const { formatDuration } = require('../../utils/formatters');

module.exports = {
  module: 'stats',
  data: new SlashCommandBuilder().setName('stats').setDescription('Affiche les statistiques du bot et du serveur'),

  async execute(interaction) {
    const db = getDb();
    const guildId = interaction.guild.id;

    const totalUsers = await db('users').where('guild_id', guildId).count('* as cnt').first();
    const totalSanctions = await db('sanctions').where('guild_id', guildId).count('* as cnt').first();
    const totalTickets = await db('tickets').where('guild_id', guildId).count('* as cnt').first();
    const totalMessages = await db('users').where('guild_id', guildId).sum('messages as total').first();

    // Today's metrics
    const today = new Date().toISOString().split('T')[0];
    const dailyMetrics = await db('daily_metrics').where({ guild_id: guildId, date: today }).first();

    const embed = createEmbed('primary')
      .setTitle('📊 Statistiques')
      .addFields(
        {
          name: '🤖 Bot',
          value: [
            `Serveurs: **${interaction.client.guilds.cache.size}**`,
            `Uptime: **${formatDuration(interaction.client.uptime)}**`,
            `Ping: **${interaction.client.ws.ping}ms**`,
            `RAM: **${(process.memoryUsage().heapUsed / 1024 / 1024).toFixed(1)} MB**`,
          ].join('\n'),
          inline: true,
        },
        {
          name: '📈 Serveur',
          value: [
            `Utilisateurs DB: **${totalUsers?.cnt || 0}**`,
            `Messages totaux: **${totalMessages?.total || 0}**`,
            `Sanctions: **${totalSanctions?.cnt || 0}**`,
            `Tickets: **${totalTickets?.cnt || 0}**`,
          ].join('\n'),
          inline: true,
        }
      );

    if (dailyMetrics) {
      embed.addFields({
        name: `📅 Aujourd'hui (${today})`,
        value: [
          `Messages: **${dailyMetrics.messages}**`,
          `Nouveaux membres: **${dailyMetrics.joins}**`,
          `Départs: **${dailyMetrics.leaves}**`,
          `Sanctions: **${dailyMetrics.sanctions}**`,
        ].join('\n'),
        inline: true,
      });
    }

    return interaction.reply({ embeds: [embed] });
  },
};
