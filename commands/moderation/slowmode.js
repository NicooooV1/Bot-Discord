// ===================================
// Ultra Suite — /slowmode
// Définir le mode lent sur un channel
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('slowmode')
    .setDescription('Définir le mode lent sur ce channel')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageChannels)
    .addIntegerOption((opt) =>
      opt.setName('secondes').setDescription('Intervalle en secondes (0 = désactiver)').setRequired(true)
        .addChoices(
          { name: 'Désactivé', value: 0 },
          { name: '5 secondes', value: 5 },
          { name: '10 secondes', value: 10 },
          { name: '15 secondes', value: 15 },
          { name: '30 secondes', value: 30 },
          { name: '1 minute', value: 60 },
          { name: '2 minutes', value: 120 },
          { name: '5 minutes', value: 300 },
          { name: '10 minutes', value: 600 },
          { name: '15 minutes', value: 900 },
          { name: '30 minutes', value: 1800 },
          { name: '1 heure', value: 3600 },
          { name: '2 heures', value: 7200 },
          { name: '6 heures', value: 21600 },
        ))
    .addChannelOption((opt) => opt.setName('channel').setDescription('Channel cible (défaut : actuel)')),

  async execute(interaction) {
    const seconds = interaction.options.getInteger('secondes');
    const channel = interaction.options.getChannel('channel') || interaction.channel;

    try {
      await channel.setRateLimitPerUser(seconds, `Slowmode par ${interaction.user.tag}`);

      if (seconds === 0) {
        return interaction.reply({ content: `✅ Mode lent désactivé dans ${channel}.` });
      }

      const display = seconds < 60 ? `${seconds}s`
        : seconds < 3600 ? `${Math.floor(seconds / 60)}min`
          : `${Math.floor(seconds / 3600)}h`;

      return interaction.reply({ content: `✅ Mode lent défini à **${display}** dans ${channel}.` });
    } catch {
      return interaction.reply({ content: '❌ Impossible de modifier le mode lent sur ce channel.', ephemeral: true });
    }
  },
};