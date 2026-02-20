// ===================================
// Ultra Suite — Tâche planifiée : Events
// Marque les événements passés comme terminés
// Exécutée toutes les 5 minutes
// ===================================

const { getDb } = require('../../database');
const { createModuleLogger } = require('../logger');

const log = createModuleLogger('EventCleanup');

module.exports = {
  name: 'event_cleanup',
  interval: 300_000,

  async execute() {
    const db = getDb();

    try {
      const updated = await db('server_events')
        .where('status', 'ACTIVE')
        .where('event_date', '<', new Date())
        .update({ status: 'COMPLETED' });

      if (updated > 0) {
        log.debug(`${updated} événement(s) marqué(s) comme terminé(s)`);
      }
    } catch (err) {
      log.error(`Erreur nettoyage events: ${err.message}`);
    }
  },
};