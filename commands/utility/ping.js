// ===================================
// Ultra Suite — /ping
// Latence du bot, API Discord et DB
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const db = require('../../database');

module.exports = {
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('ping')
    .setDescription('Voir la latence du bot'),

  async execute(interaction) {
    const start = Date.now();
    await interaction.deferReply();
    const rtt = Date.now() - start;

    // Latence WebSocket
    const ws = interaction.client.ws.ping;

    // Latence DB
    let dbLatency = '❌ Indisponible';
    try {
      const health = await db.healthCheck();
      dbLatency = health.ok ? `${health.latency}ms` : '❌ Erreur';
    } catch {
      dbLatency = '❌ Erreur';
    }

    // Uptime
    const uptime = process.uptime();
    const hours = Math.floor(uptime / 3600);
    const minutes = Math.floor((uptime % 3600) / 60);
    const seconds = Math.floor(uptime % 60);
    const uptimeStr = `${hours}h ${minutes}m ${seconds}s`;

    // Mémoire
    const mem = process.memoryUsage();
    const heapMB = (mem.heapUsed / 1024 / 1024).toFixed(1);

    // Status couleur
    const color = rtt < 200 ? 0x57F287 : rtt < 500 ? 0xFEE75C : 0xED4245;

    const embed = new EmbedBuilder()
      .setTitle('🏓 Pong !')
      .setColor(color)
      .addFields(
        { name: '📡 Latence bot', value: `${rtt}ms`, inline: true },
        { name: '💓 WebSocket', value: `${ws}ms`, inline: true },
        { name: '🗄️ Base de données', value: dbLatency, inline: true },
        { name: '⏱️ Uptime', value: uptimeStr, inline: true },
        { name: '💾 Mémoire', value: `${heapMB} MB`, inline: true },
        { name: '🌐 Serveurs', value: String(interaction.client.guilds.cache.size), inline: true },
      )
      .setTimestamp();

    await interaction.editReply({ embeds: [embed] });
  },
};