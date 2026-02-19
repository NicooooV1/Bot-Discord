// ===================================
// Ultra Suite — Ticket Queries
// ===================================

const { getDb } = require('./index');

const ticketQueries = {
  async create({ guildId, channelId, openerId, category, subject, formAnswers }) {
    const db = getDb();
    const [id] = await db('tickets').insert({
      guild_id: guildId,
      channel_id: channelId,
      opener_id: openerId,
      category: category || 'general',
      subject: subject || null,
      form_answers: formAnswers ? JSON.stringify(formAnswers) : null,
    });
    return id;
  },

  async getByChannel(channelId) {
    const db = getDb();
    const t = await db('tickets').where('channel_id', channelId).first();
    if (t?.form_answers) t.form_answers = JSON.parse(t.form_answers);
    return t;
  },

  async updateStatus(ticketId, status, closedBy = null) {
    const db = getDb();
    const patch = { status };
    if (status === 'closed') {
      patch.closed_at = new Date().toISOString();
      patch.closed_by = closedBy;
    }
    return db('tickets').where('id', ticketId).update(patch);
  },

  async setAssignee(ticketId, assigneeId) {
    const db = getDb();
    return db('tickets').where('id', ticketId).update({ assignee_id: assigneeId, status: 'in_progress' });
  },

  async setTranscript(ticketId, transcript) {
    const db = getDb();
    return db('tickets').where('id', ticketId).update({ transcript });
  },

  async rate(ticketId, rating, comment) {
    const db = getDb();
    return db('tickets').where('id', ticketId).update({ rating, rating_comment: comment });
  },

  async listOpen(guildId) {
    const db = getDb();
    return db('tickets').where({ guild_id: guildId }).whereIn('status', ['open', 'in_progress', 'waiting']);
  },

  async countByUser(guildId, userId) {
    const db = getDb();
    const row = await db('tickets')
      .where({ guild_id: guildId, opener_id: userId })
      .whereIn('status', ['open', 'in_progress', 'waiting'])
      .count('* as count')
      .first();
    return row?.count || 0;
  },
};

module.exports = ticketQueries;
