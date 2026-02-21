// ===================================
// Ultra Suite - Web Dashboard
// Projet independant - Express + Passport OAuth2
//
// Se connecte directement a PostgreSQL et Redis.
// Communique avec le bot via API REST interne.
//
// Deploiement : LXC 115 (192.168.1.219)
// Reverse proxy Nginx + SSL (certbot)
// ===================================

require('dotenv').config();

const express = require('express');
const session = require('express-session');
const passport = require('passport');
const { Strategy: DiscordStrategy } = require('passport-discord');
const cors = require('cors');
const jwt = require('jsonwebtoken');
const path = require('path');
const Redis = require('ioredis');
const knex = require('knex');

// ===================================
// Configuration
// ===================================
const DASHBOARD_PORT = parseInt(process.env.DASHBOARD_PORT, 10) || 3000;
const JWT_SECRET = process.env.JWT_SECRET || process.env.OAUTH2_CLIENT_SECRET || 'change-me';
const SCOPES = ['identify', 'guilds'];
const NODE_ENV = process.env.NODE_ENV || 'development';
const BOT_API_URL = process.env.BOT_API_URL || 'http://192.168.1.235:3001';

// ===================================
// Database (PostgreSQL direct)
// ===================================
const db = knex({
  client: 'pg',
  connection: {
    host: process.env.DB_HOST || '192.168.1.216',
    port: parseInt(process.env.DB_PORT, 10) || 5432,
    user: process.env.DB_USER || 'botuser',
    password: process.env.DB_PASSWORD || '',
    database: process.env.DB_NAME || 'discordbot',
  },
  pool: { min: 1, max: 5 },
});

// ===================================
// Redis (DB3 pour sessions)
// ===================================
const redis = new Redis({
  host: process.env.REDIS_HOST || '192.168.1.217',
  port: parseInt(process.env.REDIS_PORT, 10) || 6379,
  password: process.env.REDIS_PASSWORD || '',
  db: 3,
  lazyConnect: true,
});

redis.on('connect', () => console.log('[Dashboard] Redis DB3 connecte'));
redis.on('error', (err) => console.error('[Dashboard] Redis erreur:', err.message));

// ===================================
// Session store Redis
// ===================================
let RedisStore;
try {
  const connectRedis = require('connect-redis');
  RedisStore = connectRedis.default || connectRedis;
} catch {
  RedisStore = null;
}

// ===================================
// Passport Discord
// ===================================
passport.serializeUser((user, done) => done(null, user));
passport.deserializeUser((obj, done) => done(null, obj));

// ===================================
// Helper : appel API vers le bot
// ===================================
async function botApi(endpoint, options = {}) {
  try {
    const url = `${BOT_API_URL}${endpoint}`;
    const res = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${process.env.BOT_API_KEY || ''}`,
        ...(options.headers || {}),
      },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// ===================================
// Helper : parser la config guild
// ===================================
function safeJsonParse(str, fallback = {}) {
  if (!str) return fallback;
  if (typeof str === 'object') return str;
  try { return JSON.parse(str); } catch { return fallback; }
}

// ===================================
// Start
// ===================================
async function startDashboard() {
  // Connexion Redis
  try {
    await redis.connect();
  } catch (err) {
    console.warn('[Dashboard] Redis non disponible:', err.message);
  }

  // Tester la connexion DB
  try {
    await db.raw('SELECT 1');
    console.log('[Dashboard] PostgreSQL connecte');
  } catch (err) {
    console.error('[Dashboard] PostgreSQL erreur:', err.message);
  }

  const app = express();

  // --- Middleware ---
  app.use(cors({
    origin: process.env.DASHBOARD_URL || `http://localhost:${DASHBOARD_PORT}`,
    credentials: true,
  }));
  app.use(express.json());

  // Session (Redis ou memoire)
  const sessionConfig = {
    secret: JWT_SECRET,
    resave: false,
    saveUninitialized: false,
    cookie: {
      secure: NODE_ENV === 'production',
      maxAge: 7 * 24 * 60 * 60 * 1000,
      httpOnly: true,
      sameSite: 'lax',
    },
  };

  if (RedisStore && redis.status === 'ready') {
    sessionConfig.store = new RedisStore({ client: redis, prefix: 'dash:sess:' });
    console.log('[Dashboard] Sessions stockees dans Redis');
  }

  app.use(session(sessionConfig));
  app.use(passport.initialize());
  app.use(passport.session());

  // Passport Discord strategy (seulement si configure)
  if (process.env.CLIENT_ID && process.env.OAUTH2_CLIENT_SECRET) {
    passport.use(new DiscordStrategy({
      clientID: process.env.CLIENT_ID,
      clientSecret: process.env.OAUTH2_CLIENT_SECRET,
      callbackURL: process.env.OAUTH2_REDIRECT_URI || `http://localhost:${DASHBOARD_PORT}/auth/callback`,
      scope: SCOPES,
    }, (accessToken, refreshToken, profile, done) => {
      profile.accessToken = accessToken;
      return done(null, profile);
    }));
  } else {
    console.warn('[Dashboard] CLIENT_ID ou OAUTH2_CLIENT_SECRET manquant - OAuth desactive');
  }

  // --- Static files ---
  app.use(express.static(path.join(__dirname, 'public')));

  // --- Auth Routes ---
  app.get('/auth/login', passport.authenticate('discord'));

  app.get('/auth/callback',
    passport.authenticate('discord', { failureRedirect: '/' }),
    (req, res) => res.redirect('/dashboard'));

  app.get('/auth/logout', (req, res) => {
    req.logout(() => {});
    res.redirect('/');
  });

  app.get('/auth/user', (req, res) => {
    if (!req.isAuthenticated()) return res.status(401).json({ error: 'Not authenticated' });
    res.json({
      id: req.user.id,
      username: req.user.username,
      avatar: req.user.avatar,
      discriminator: req.user.discriminator,
      guilds: filterManagedGuilds(req.user.guilds || []),
    });
  });

  // --- API Routes ---
  app.get('/api/health', async (req, res) => {
    const dbOk = await db.raw('SELECT 1').then(() => true).catch(() => false);
    const redisOk = redis.status === 'ready';
    const botData = await botApi('/health');

    res.json({
      status: dbOk && redisOk ? 'healthy' : 'degraded',
      database: dbOk,
      redis: redisOk,
      bot: botData || { status: 'unreachable' },
    });
  });

  app.get('/api/stats', requireAuth, async (req, res) => {
    // Recuperer les stats du bot via son API
    const botStats = await botApi('/stats');

    res.json({
      bot: botStats || { guilds: 0, users: 0, commands: 0, uptime: 0 },
      dashboard: {
        uptime: Math.floor(process.uptime()),
        memory: Math.round(process.memoryUsage().heapUsed / 1024 / 1024),
      },
    });
  });

  // Guild list (from DB + bot API)
  app.get('/api/guilds', requireAuth, async (req, res) => {
    const managed = filterManagedGuilds(req.user.guilds || []);

    // Recuperer les guilds ou le bot est present via la DB
    const botGuildIds = await db('guilds').select('id', 'name').then((rows) => {
      const map = new Map();
      rows.forEach((r) => map.set(r.id, r));
      return map;
    }).catch(() => new Map());

    const guilds = managed.map((g) => ({
      id: g.id,
      name: g.name,
      icon: g.icon,
      inBot: botGuildIds.has(g.id),
      botName: botGuildIds.get(g.id)?.name || null,
    }));

    res.json({ guilds });
  });

  // Guild config (from DB)
  app.get('/api/guilds/:id', requireAuth, requireGuildAccess, async (req, res) => {
    const guildId = req.params.id;
    const guild = await db('guilds').where('id', guildId).first();

    if (!guild) return res.status(404).json({ error: 'Guild not found in database' });

    const config = safeJsonParse(guild.config, {});
    const modules = safeJsonParse(guild.modules_enabled, {});

    // Recuperer channels et roles via le bot API
    const guildInfo = await botApi(`/guilds/${guildId}`);

    res.json({
      id: guildId,
      name: guild.name,
      config,
      modules,
      channels: guildInfo?.channels || [],
      roles: guildInfo?.roles || [],
      members: guildInfo?.memberCount || 0,
    });
  });

  // Update guild config (write to DB)
  app.post('/api/guilds/:id/config', requireAuth, requireGuildAccess, async (req, res) => {
    const guildId = req.params.id;

    if (req.body.config) {
      const existing = await db('guilds').where('id', guildId).first();
      const currentConfig = safeJsonParse(existing?.config, {});
      const merged = { ...currentConfig, ...req.body.config };
      await db('guilds').where('id', guildId).update({
        config: JSON.stringify(merged),
        updated_at: db.fn.now(),
      });
    }

    if (req.body.modules) {
      const existing = await db('guilds').where('id', guildId).first();
      const currentModules = safeJsonParse(existing?.modules_enabled, {});
      const merged = { ...currentModules, ...req.body.modules };
      await db('guilds').where('id', guildId).update({
        modules_enabled: JSON.stringify(merged),
        updated_at: db.fn.now(),
      });
    }

    // Invalider le cache Redis
    try {
      await redis.del(`cfg:${guildId}`);
      await redis.del(`mod:${guildId}`);
    } catch { /* pas grave */ }

    const updated = await db('guilds').where('id', guildId).first();
    res.json({
      success: true,
      config: safeJsonParse(updated?.config, {}),
      modules: safeJsonParse(updated?.modules_enabled, {}),
    });
  });

  // Guild stats
  app.get('/api/guilds/:id/stats', requireAuth, requireGuildAccess, async (req, res) => {
    const guildId = req.params.id;

    const [tickets] = await db('tickets').where('guild_id', guildId).count('id as c').catch(() => [{ c: 0 }]);
    const [sanctions] = await db('sanctions').where('guild_id', guildId).count('id as c').catch(() => [{ c: 0 }]);
    const [users] = await db('users').where('guild_id', guildId).count('id as c').catch(() => [{ c: 0 }]);

    res.json({
      tickets: tickets?.c || 0,
      sanctions: sanctions?.c || 0,
      users: users?.c || 0,
    });
  });

  // Leaderboard
  app.get('/api/guilds/:id/leaderboard', requireAuth, requireGuildAccess, async (req, res) => {
    const guildId = req.params.id;
    const type = req.query.type || 'xp';

    let data = [];
    if (type === 'xp') {
      data = await db('users').where('guild_id', guildId).orderBy('xp', 'desc').limit(50).catch(() => []);
    } else if (type === 'economy') {
      data = await db('users').where('guild_id', guildId).orderBy('balance', 'desc').limit(50).catch(() => []);
    }

    res.json({ leaderboard: data });
  });

  // --- SPA fallback ---
  app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
  });

  // --- Error handler ---
  app.use((err, req, res, _next) => {
    console.error(`[Dashboard] Error: ${err.message}`);
    res.status(500).json({ error: 'Internal Server Error' });
  });

  // --- Start ---
  app.listen(DASHBOARD_PORT, () => {
    console.log(`[Dashboard] Demarre sur le port ${DASHBOARD_PORT}`);
  });
}

// ===================================
// Middleware helpers
// ===================================
function requireAuth(req, res, next) {
  if (req.isAuthenticated()) return next();

  const auth = req.headers.authorization;
  if (auth?.startsWith('Bearer ')) {
    try {
      const decoded = jwt.verify(auth.slice(7), JWT_SECRET);
      req.user = decoded;
      return next();
    } catch {}
  }

  res.status(401).json({ error: 'Not authenticated' });
}

function requireGuildAccess(req, res, next) {
  const guildId = req.params.id;
  const userGuilds = req.user.guilds || [];
  const hasAccess = userGuilds.some((g) => g.id === guildId && (parseInt(g.permissions) & 0x20) === 0x20);

  if (!hasAccess) return res.status(403).json({ error: 'No access to this guild' });
  next();
}

function filterManagedGuilds(guilds) {
  return guilds.filter((g) => (parseInt(g.permissions) & 0x20) === 0x20);
}

// ===================================
// Export pour usage integre OU standalone
// ===================================
// Mode standalone : node dashboard/server.js
if (require.main === module) {
  startDashboard().catch((err) => {
    console.error('[Dashboard] Erreur de demarrage:', err);
    process.exit(1);
  });
}

// Mode integre (optionnel, pour dev)
module.exports = {
  startDashboard: async function startIntegrated(client) {
    // Si appele depuis le bot, on peut passer le client
    // mais le dashboard standalone est la methode recommandee
    console.warn('[Dashboard] Mode integre - utilisez le dashboard standalone en production');
    await startDashboard();
  },
  stopDashboard: function () {
    // Le dashboard standalone gere son propre lifecycle
  },
};
