// ===================================
// Ultra Suite — Log Queries
// ===================================

const { getDb } = require('./index');

const logQueries = {
  /**
   * Enregistre un log
   */
  async create({ guildId, type, actorId, targetId, targetType, details }) {
    const db = getDb();
    return db('logs').insert({
      guild_id: guildId,
      type,
      actor_id: actorId || null,
      target_id: targetId || null,
      target_type: targetType || null,
      details: details ? JSON.stringify(details) : '{}',
    });
  },

  /**
   * Recherche de logs avec filtres
   */
  async search(guildId, { type, actorId, targetId, limit = 25, offset = 0, since } = {}) {
    const db = getDb();
    let q = db('logs')
      .where('guild_id', guildId)
      .orderBy('timestamp', 'desc')
      .limit(limit)
      .offset(offset);

    if (type) q = q.where('type', type);
    if (actorId) q = q.where('actor_id', actorId);
    if (targetId) q = q.where('target_id', targetId);
    if (since) q = q.where('timestamp', '>=', since);

    const rows = await q;
    return rows.map((r) => ({ ...r, details: JSON.parse(r.details || '{}') }));
  },

  /**
   * Purge les logs plus vieux que X jours
   */
  async purgeOlderThan(guildId, days) {
    const db = getDb();
    const cutoff = new Date(Date.now() - days * 86400000).toISOString();
    return db('logs').where('guild_id', guildId).where('timestamp', '<', cutoff).del();
  },
};

module.exports = logQueries;
