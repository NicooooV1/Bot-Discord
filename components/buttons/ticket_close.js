// ===================================
// Ultra Suite — Button: ticket_close
// Ferme un ticket via le bouton
// ===================================

const configService = require('../../core/configService');
const ticketQueries = require('../../database/ticketQueries');
const { createEmbed, errorEmbed, successEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  id: 'ticket_close',

  async execute(interaction) {
    const ticket = await ticketQueries.getByChannel(interaction.channel.id);
    if (!ticket) {
      return interaction.reply({ embeds: [errorEmbed(t('tickets.not_ticket'))], ephemeral: true });
    }

    await ticketQueries.updateStatus(ticket.id, 'closed', interaction.user.id);

    // Transcript
    const messages = await interaction.channel.messages.fetch({ limit: 100 });
    const transcript = messages
      .reverse()
      .map((m) => `[${m.createdAt.toISOString()}] ${m.author.tag}: ${m.content}`)
      .join('\n');
    await ticketQueries.setTranscript(ticket.id, transcript);

    const config = await configService.get(interaction.guild.id);
    if (config.ticketLogChannel) {
      const logChannel = interaction.guild.channels.cache.get(config.ticketLogChannel);
      if (logChannel) {
        const logEmbed = createEmbed('logs')
          .setTitle(`🎫 Ticket fermé — #${ticket.id}`)
          .addFields(
            { name: 'Ouvert par', value: `<@${ticket.opener_id}>`, inline: true },
            { name: 'Fermé par', value: interaction.user.tag, inline: true }
          );
        logChannel.send({ embeds: [logEmbed] }).catch(() => {});
      }
    }

    await interaction.reply({ embeds: [successEmbed(t('tickets.close', undefined, { user: interaction.user.tag }))] });
    setTimeout(() => interaction.channel.delete('Ticket fermé').catch(() => {}), 5000);
  },
};
