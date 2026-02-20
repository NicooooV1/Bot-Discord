// ===================================
// Ultra Suite — Tâche planifiée : Tempbans expirés
// Déban automatiquement les tempbans arrivés à expiration
// Exécutée toutes les 60 secondes
// ===================================

const { getDb } = require('../../database');
const { createModuleLogger } = require('../logger');

const log = createModuleLogger('TempbanTask');

module.exports = {
  name: 'tempbans',
  interval: 60_000,

  async execute(client) {
    const db = getDb();

    try {
      const expired = await db('sanctions')
        .where('type', 'TEMPBAN')
        .where('active', true)
        .whereNotNull('expires_at')
        .where('expires_at', '<=', new Date())
        .limit(10);

      if (expired.length === 0) return;

      for (const sanction of expired) {
        try {
          const guild = await client.guilds.fetch(sanction.guild_id).catch(() => null);
          if (!guild) continue;

          await guild.members.unban(sanction.user_id, 'Tempban expiré — automatique').catch(() => {});
          await db('sanctions').where('id', sanction.id).update({ active: false });

          log.info(`Tempban expiré: ${sanction.user_id} dans ${guild.name}`);
        } catch (err) {
          log.warn(`Erreur unban auto #${sanction.id}: ${err.message}`);
        }
      }
    } catch (err) {
      log.error(`Erreur tâche tempbans: ${err.message}`);
    }
  },
};