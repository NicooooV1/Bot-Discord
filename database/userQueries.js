// ===================================
// Ultra Suite — User Queries
// ===================================

const { getDb } = require('./index');

const userQueries = {
  /**
   * Récupère ou crée un user dans une guild
   */
  async getOrCreate(userId, guildId) {
    const db = getDb();
    let user = await db('users').where({ user_id: userId, guild_id: guildId }).first();
    if (!user) {
      await db('users').insert({ user_id: userId, guild_id: guildId });
      user = await db('users').where({ user_id: userId, guild_id: guildId }).first();
    }
    return user;
  },

  /**
   * Met à jour un champ
   */
  async update(userId, guildId, data) {
    const db = getDb();
    data.updated_at = db.fn.now();
    return db('users').where({ user_id: userId, guild_id: guildId }).update(data);
  },

  /**
   * Incrémente un compteur (xp, total_messages, etc.)
   */
  async increment(userId, guildId, field, amount = 1) {
    const db = getDb();
    return db('users')
      .where({ user_id: userId, guild_id: guildId })
      .increment(field, amount);
  },

  /**
   * Leaderboard XP d'une guild
   */
  async leaderboard(guildId, limit = 10, offset = 0) {
    const db = getDb();
    return db('users')
      .where('guild_id', guildId)
      .orderBy('xp', 'desc')
      .limit(limit)
      .offset(offset);
  },

  /**
   * Rang d'un user dans sa guild
   */
  async rank(userId, guildId) {
    const db = getDb();
    const result = await db.raw(
      `SELECT COUNT(*) + 1 as rank FROM users WHERE guild_id = ? AND xp > (SELECT xp FROM users WHERE user_id = ? AND guild_id = ?)`,
      [guildId, userId, guildId]
    );
    return result[0]?.rank ?? null;
  },

  /**
   * Ajoute / retire de la balance
   */
  async addBalance(userId, guildId, amount, field = 'balance') {
    const db = getDb();
    if (field !== 'balance' && field !== 'bank') throw new Error('Invalid field');
    await this.getOrCreate(userId, guildId);
    return db('users')
      .where({ user_id: userId, guild_id: guildId })
      .increment(field, amount);
  },

  /**
   * Transfère entre balance et bank
   */
  async transfer(userId, guildId, amount, direction = 'deposit') {
    const db = getDb();
    const user = await this.getOrCreate(userId, guildId);

    if (direction === 'deposit') {
      if (user.balance < amount) return { success: false, reason: 'insufficient_balance' };
      await db('users').where({ user_id: userId, guild_id: guildId }).update({
        balance: user.balance - amount,
        bank: user.bank + amount,
        updated_at: db.fn.now(),
      });
    } else {
      if (user.bank < amount) return { success: false, reason: 'insufficient_bank' };
      await db('users').where({ user_id: userId, guild_id: guildId }).update({
        balance: user.balance + amount,
        bank: user.bank - amount,
        updated_at: db.fn.now(),
      });
    }
    return { success: true };
  },
};

module.exports = userQueries;
