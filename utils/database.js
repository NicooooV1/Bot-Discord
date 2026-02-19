const Database = require('better-sqlite3');
const path = require('path');

const db = new Database(path.join(__dirname, '..', 'data', 'bot.db'));

// Activer WAL mode pour de meilleures performances
db.pragma('journal_mode = WAL');

// ===================================
// Création des tables
// ===================================
db.exec(`
  -- Table des avertissements
  CREATE TABLE IF NOT EXISTS warns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    moderator_id TEXT NOT NULL,
    reason TEXT DEFAULT 'Aucune raison spécifiée',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
  );

  -- Table de configuration du serveur
  CREATE TABLE IF NOT EXISTS guild_config (
    guild_id TEXT PRIMARY KEY,
    log_channel_id TEXT,
    welcome_channel_id TEXT,
    welcome_message TEXT DEFAULT 'Bienvenue {user} sur **{server}** ! 🎉 Tu es notre **{memberCount}ème** membre !',
    leave_message TEXT DEFAULT '**{user}** a quitté le serveur. Nous sommes maintenant **{memberCount}** membres.',
    ticket_category_id TEXT,
    ticket_log_channel_id TEXT,
    mod_role_id TEXT,
    mute_role_id TEXT,
    antispam_enabled INTEGER DEFAULT 0
  );

  -- Table des tickets
  CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    channel_id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    status TEXT DEFAULT 'open',
    subject TEXT DEFAULT 'Support',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    closed_at DATETIME
  );

  -- Table des logs de modération
  CREATE TABLE IF NOT EXISTS mod_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target_id TEXT NOT NULL,
    moderator_id TEXT NOT NULL,
    reason TEXT,
    duration TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
  );
`);

// ===================================
// Fonctions de configuration serveur
// ===================================
const getGuildConfig = (guildId) => {
  const stmt = db.prepare('SELECT * FROM guild_config WHERE guild_id = ?');
  let config = stmt.get(guildId);
  if (!config) {
    db.prepare('INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)').run(guildId);
    config = stmt.get(guildId);
  }
  return config;
};

const updateGuildConfig = (guildId, key, value) => {
  const allowed = [
    'log_channel_id', 'welcome_channel_id', 'welcome_message', 'leave_message',
    'ticket_category_id', 'ticket_log_channel_id', 'mod_role_id', 'mute_role_id',
    'antispam_enabled'
  ];
  if (!allowed.includes(key)) throw new Error(`Clé de config invalide: ${key}`);
  db.prepare(`UPDATE guild_config SET ${key} = ? WHERE guild_id = ?`).run(value, guildId);
};

// ===================================
// Fonctions d'avertissements
// ===================================
const addWarn = (guildId, userId, moderatorId, reason) => {
  const stmt = db.prepare('INSERT INTO warns (guild_id, user_id, moderator_id, reason) VALUES (?, ?, ?, ?)');
  return stmt.run(guildId, userId, moderatorId, reason);
};

const getWarns = (guildId, userId) => {
  return db.prepare('SELECT * FROM warns WHERE guild_id = ? AND user_id = ? ORDER BY created_at DESC').all(guildId, userId);
};

const removeWarn = (warnId, guildId) => {
  return db.prepare('DELETE FROM warns WHERE id = ? AND guild_id = ?').run(warnId, guildId);
};

const clearWarns = (guildId, userId) => {
  return db.prepare('DELETE FROM warns WHERE guild_id = ? AND user_id = ?').run(guildId, userId);
};

// ===================================
// Fonctions de tickets
// ===================================
const createTicket = (guildId, channelId, userId, subject) => {
  return db.prepare('INSERT INTO tickets (guild_id, channel_id, user_id, subject) VALUES (?, ?, ?, ?)').run(guildId, channelId, userId, subject);
};

const getTicket = (channelId) => {
  return db.prepare('SELECT * FROM tickets WHERE channel_id = ?').get(channelId);
};

const closeTicket = (channelId) => {
  return db.prepare("UPDATE tickets SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE channel_id = ?").run(channelId);
};

const getOpenTickets = (guildId, userId) => {
  return db.prepare("SELECT * FROM tickets WHERE guild_id = ? AND user_id = ? AND status = 'open'").all(guildId, userId);
};

const countTickets = (guildId) => {
  return db.prepare('SELECT COUNT(*) as count FROM tickets WHERE guild_id = ?').get(guildId).count;
};

// ===================================
// Fonctions de logs de modération
// ===================================
const addModLog = (guildId, action, targetId, moderatorId, reason, duration = null) => {
  return db.prepare('INSERT INTO mod_logs (guild_id, action, target_id, moderator_id, reason, duration) VALUES (?, ?, ?, ?, ?, ?)')
    .run(guildId, action, targetId, moderatorId, reason, duration);
};

const getModLogs = (guildId, targetId) => {
  return db.prepare('SELECT * FROM mod_logs WHERE guild_id = ? AND target_id = ? ORDER BY created_at DESC LIMIT 25').all(guildId, targetId);
};

module.exports = {
  db,
  getGuildConfig,
  updateGuildConfig,
  addWarn,
  getWarns,
  removeWarn,
  clearWarns,
  createTicket,
  getTicket,
  closeTicket,
  getOpenTickets,
  countTickets,
  addModLog,
  getModLogs,
};
