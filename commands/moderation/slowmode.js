const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { COLORS } = require('../../utils/logger');
const { errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('slowmode')
    .setDescription('🐌 Définir le mode lent d\'un salon')
    .addIntegerOption(opt =>
      opt.setName('secondes')
        .setDescription('Délai en secondes (0 pour désactiver)')
        .setRequired(true)
        .setMinValue(0)
        .setMaxValue(21600)
    )
    .addStringOption(opt => opt.setName('raison').setDescription('Raison du changement'))
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageChannels),

  async execute(interaction) {
    const seconds = interaction.options.getInteger('secondes');
    const reason = interaction.options.getString('raison') || '';

    try {
      await interaction.channel.setRateLimitPerUser(seconds, reason);

      const embed = new EmbedBuilder()
        .setColor(seconds > 0 ? COLORS.YELLOW : COLORS.GREEN)
        .setTimestamp();

      if (seconds === 0) {
        embed.setTitle('🐌 Mode lent désactivé');
        embed.setDescription('Le mode lent a été désactivé dans ce salon.');
      } else {
        const formatted = seconds >= 3600
          ? `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
          : seconds >= 60
            ? `${Math.floor(seconds / 60)}m ${seconds % 60}s`
            : `${seconds}s`;

        embed.setTitle('🐌 Mode lent activé');
        embed.setDescription(`Le mode lent a été défini à **${formatted}**.`);
      }

      if (reason) embed.addFields({ name: '📝 Raison', value: reason });

      await interaction.reply({ embeds: [embed] });
    } catch (error) {
      console.error('[SLOWMODE]', error);
      await interaction.reply(errorReply('❌ Impossible de modifier le mode lent.'));
    }
  },
};
