// ===================================
// Ultra Suite — Button: event_join_*
// Inscription à un événement
// ===================================

const { getDb } = require('../../database');

module.exports = {
  id: 'event_join_',

  async execute(interaction) {
    const eventId = interaction.customId.split('_')[2];
    const db = getDb();

    const event = await db('events').where({ id: eventId, guild_id: interaction.guild.id }).first();
    if (!event) return interaction.reply({ content: '❌ Événement introuvable.', ephemeral: true });

    const participants = JSON.parse(event.participants || '[]');

    if (participants.includes(interaction.user.id)) {
      return interaction.reply({ content: '⚠️ Tu es déjà inscrit.', ephemeral: true });
    }

    if (event.max_participants > 0 && participants.length >= event.max_participants) {
      return interaction.reply({ content: '❌ Plus de places disponibles.', ephemeral: true });
    }

    participants.push(interaction.user.id);
    await db('events').where('id', eventId).update({ participants: JSON.stringify(participants) });

    return interaction.reply({ content: `✅ Inscrit à **${event.title}** ! (${participants.length} participants)`, ephemeral: true });
  },
};
