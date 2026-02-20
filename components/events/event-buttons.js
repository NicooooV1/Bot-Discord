// ===================================
// Ultra Suite — Composants Event Buttons
// Gère les boutons join/leave des événements
// ===================================

const { EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  prefix: 'event-',
  type: 'button',
  module: 'events',

  async execute(interaction) {
    const customId = interaction.customId;
    const db = getDb();
    const guildId = interaction.guildId;

    const parts = customId.split('-');
    const action = parts[1]; // join or leave
    const eventId = parts[2];

    const event = await db('server_events').where('id', eventId).where('guild_id', guildId).first();
    if (!event || event.status !== 'ACTIVE') {
      return interaction.reply({ content: '❌ Événement introuvable ou terminé.', ephemeral: true });
    }

    const participants = JSON.parse(event.participants || '[]');
    const userId = interaction.user.id;

    if (action === 'join') {
      if (participants.includes(userId)) {
        return interaction.reply({ content: 'ℹ️ Vous êtes déjà inscrit.', ephemeral: true });
      }
      if (event.max_participants && participants.length >= event.max_participants) {
        return interaction.reply({ content: '❌ L\'événement est complet.', ephemeral: true });
      }

      participants.push(userId);
      await db('server_events').where('id', eventId).update({ participants: JSON.stringify(participants) });

      // Mettre à jour l'embed
      await updateEventEmbed(interaction, event, participants);
      return interaction.reply({ content: `✅ Vous êtes inscrit à **${event.title}** !`, ephemeral: true });
    }

    if (action === 'leave') {
      if (!participants.includes(userId)) {
        return interaction.reply({ content: 'ℹ️ Vous n\'êtes pas inscrit.', ephemeral: true });
      }

      const updated = participants.filter((p) => p !== userId);
      await db('server_events').where('id', eventId).update({ participants: JSON.stringify(updated) });

      await updateEventEmbed(interaction, event, updated);
      return interaction.reply({ content: `✅ Vous vous êtes désinscrit de **${event.title}**.`, ephemeral: true });
    }
  },
};

async function updateEventEmbed(interaction, event, participants) {
  try {
    const embed = EmbedBuilder.from(interaction.message.embeds[0]);

    // Mettre à jour le champ participants
    const fields = embed.data.fields || [];
    const participantField = fields.find((f) => f.name === '👥 Participants');
    if (participantField) {
      participantField.value = `${participants.length}${event.max_participants ? `/${event.max_participants}` : ''}`;
    }

    await interaction.message.edit({ embeds: [embed] });
  } catch { /* Le message peut avoir été supprimé */ }
}