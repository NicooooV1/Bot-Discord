// ===================================
// Ultra Suite — Tâche planifiée : Vocaux temporaires
// Supprime les salons vocaux vides
// Exécutée toutes les 30 secondes
// ===================================

const { getDb } = require('../../database');
const { createModuleLogger } = require('../logger');

const log = createModuleLogger('TempVoiceTask');

module.exports = {
  name: 'tempvoice_cleanup',
  interval: 30_000,

  async execute(client) {
    const db = getDb();

    try {
      const channels = await db('temp_voice_channels').select('*');

      for (const record of channels) {
        try {
          const channel = await client.channels.fetch(record.channel_id).catch(() => null);

          // Channel supprimé ou introuvable
          if (!channel) {
            await db('temp_voice_channels').where('id', record.id).del();
            continue;
          }

          // Salon vide → supprimer
          if (channel.members.size === 0) {
            await channel.delete('Salon vocal temporaire vide').catch(() => {});
            await db('temp_voice_channels').where('id', record.id).del();
            log.debug(`Supprimé tempvoice vide: ${channel.name}`);
          }
        } catch { /* Ignorer */ }
      }
    } catch (err) {
      log.error(`Erreur tâche tempvoice: ${err.message}`);
    }
  },
};