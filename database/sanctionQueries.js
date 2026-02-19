// ===================================
// Ultra Suite — Sanction Queries
// ===================================

const { getDb } = require('./index');

const sanctionQueries = {
  /**
   * Prochain numéro de case pour une guild
   */
  async nextCase(guildId) {
    const db = getDb();
    const row = await db('sanctions')
      .where('guild_id', guildId)
      .max('case_number as max')
      .first();
    return (row?.max || 0) + 1;
  },

  /**
   * Crée une sanction
   */
  async create({ guildId, type, targetId, moderatorId, reason, duration, expiresAt, evidence }) {
    const db = getDb();
    const caseNumber = await this.nextCase(guildId);
    const [id] = await db('sanctions').insert({
      guild_id: guildId,
      case_number: caseNumber,
      type,
      target_id: targetId,
      moderator_id: moderatorId,
      reason: reason || 'Aucune raison',
      duration: duration || null,
      expires_at: expiresAt || null,
      evidence: evidence ? JSON.stringify(evidence) : null,
    });
    return { id, caseNumber };
  },

  /**
   * Récupère une sanction par case number
   */
  async getByCase(guildId, caseNumber) {
    const db = getDb();
    const s = await db('sanctions')
      .where({ guild_id: guildId, case_number: caseNumber })
      .first();
    if (s && s.evidence) s.evidence = JSON.parse(s.evidence);
    return s;
  },

  /**
   * Liste les sanctions d'un user
   */
  async listForUser(guildId, targetId, { type, active, limit = 20, offset = 0 } = {}) {
    const db = getDb();
    let q = db('sanctions')
      .where({ guild_id: guildId, target_id: targetId })
      .orderBy('case_number', 'desc')
      .limit(limit)
      .offset(offset);
    if (type) q = q.where('type', type);
    if (active !== undefined) q = q.where('active', active);
    return q;
  },

  /**
   * Nombre de warns actifs d'un user
   */
  async activeWarns(guildId, targetId) {
    const db = getDb();
    const row = await db('sanctions')
      .where({ guild_id: guildId, target_id: targetId, type: 'WARN', active: true })
      .count('* as count')
      .first();
    return row?.count || 0;
  },

  /**
   * Révoque une sanction
   */
  async revoke(guildId, caseNumber, revokedBy) {
    const db = getDb();
    return db('sanctions')
      .where({ guild_id: guildId, case_number: caseNumber })
      .update({
        active: false,
        revoked_at: db.fn.now(),
        revoked_by: revokedBy,
      });
  },

  /**
   * Sanctions expirées à traiter
   */
  async getExpired() {
    const db = getDb();
    return db('sanctions')
      .where('active', true)
      .whereNotNull('expires_at')
      .where('expires_at', '<=', new Date().toISOString());
  },

  /**
   * Compter les sanctions pour modlogs
   */
  async countForUser(guildId, targetId) {
    const db = getDb();
    const rows = await db('sanctions')
      .where({ guild_id: guildId, target_id: targetId })
      .select('type')
      .count('* as count')
      .groupBy('type');
    const result = {};
    for (const r of rows) result[r.type] = r.count;
    return result;
  },
};

module.exports = sanctionQueries;
