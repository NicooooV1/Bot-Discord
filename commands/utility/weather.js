// ===================================
// Ultra Suite — /weather
// Météo
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

module.exports = {
  module: 'utility',
  cooldown: 10,

  data: new SlashCommandBuilder()
    .setName('weather')
    .setDescription('Voir la météo d\'une ville')
    .addStringOption((o) => o.setName('ville').setDescription('Nom de la ville').setRequired(true)),

  async execute(interaction) {
    await interaction.deferReply();
    const city = interaction.options.getString('ville');

    try {
      const apiKey = process.env.WEATHER_API_KEY;
      if (!apiKey) {
        return interaction.editReply({ content: '❌ WEATHER_API_KEY non configurée.' });
      }

      const res = await fetch(`https://api.openweathermap.org/data/2.5/weather?q=${encodeURIComponent(city)}&appid=${apiKey}&units=metric&lang=fr`);
      if (!res.ok) throw new Error('Ville introuvable');

      const data = await res.json();
      const weatherEmoji = { Clear: '☀️', Clouds: '☁️', Rain: '🌧️', Snow: '❄️', Thunderstorm: '⛈️', Drizzle: '🌦️', Mist: '🌫️', Fog: '🌫️' };
      const emoji = weatherEmoji[data.weather[0]?.main] || '🌡️';

      const embed = new EmbedBuilder()
        .setTitle(`${emoji} Météo — ${data.name}, ${data.sys?.country}`)
        .setColor(0x3498DB)
        .addFields(
          { name: '🌡️ Température', value: `${data.main.temp}°C (Ressenti: ${data.main.feels_like}°C)`, inline: true },
          { name: '🔻 Min / 🔺 Max', value: `${data.main.temp_min}°C / ${data.main.temp_max}°C`, inline: true },
          { name: '💨 Vent', value: `${data.wind.speed} m/s`, inline: true },
          { name: '💧 Humidité', value: `${data.main.humidity}%`, inline: true },
          { name: '🌤 Description', value: data.weather[0]?.description || 'N/A', inline: true },
          { name: '👁 Visibilité', value: `${(data.visibility / 1000).toFixed(1)} km`, inline: true },
        )
        .setThumbnail(`https://openweathermap.org/img/wn/${data.weather[0]?.icon}@2x.png`)
        .setTimestamp();

      return interaction.editReply({ embeds: [embed] });
    } catch (e) {
      return interaction.editReply({ content: `❌ Erreur : ${e.message}` });
    }
  },
};
