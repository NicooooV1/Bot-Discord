// ===================================
// Ultra Suite — Button: event_leave_*
// Désinscription d'un événement
// ===================================

const { getDb } = require('../../database');

module.exports = {
  id: 'event_leave_',
  module: 'events',

  async execute(interaction) {
    try {
      const eventId = interaction.customId.split('_')[2];
      const db = getDb();

      const event = await db('events').where({ id: eventId, guild_id: interaction.guild.id }).first();
      if (!event) return interaction.reply({ content: '❌ Événement introuvable.', ephemeral: true });

      let participants = JSON.parse(event.participants || '[]');

      if (!participants.includes(interaction.user.id)) {
        return interaction.reply({ content: '⚠️ Tu n\'es pas inscrit.', ephemeral: true });
      }

      participants = participants.filter((id) => id !== interaction.user.id);
      await db('events').where('id', eventId).update({ participants: JSON.stringify(participants) });

      return interaction.reply({ content: `✅ Désinscrit de **${event.title}**.`, ephemeral: true });
    } catch (err) {
      return interaction.reply({ content: '❌ Une erreur est survenue.', ephemeral: true }).catch(() => {});
    }
  },
};
