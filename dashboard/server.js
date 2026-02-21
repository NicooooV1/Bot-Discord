// ===================================
// Ultra Suite — Web Dashboard
// Express + Passport OAuth2 + Static SPA
// ===================================

const express = require('express');
const session = require('express-session');
const passport = require('passport');
const { Strategy: DiscordStrategy } = require('passport-discord');
const cors = require('cors');
const jwt = require('jsonwebtoken');
const path = require('path');
const { createModuleLogger } = require('../core/logger');
const configService = require('../core/configService');
const { getDb } = require('../database');

const log = createModuleLogger('Dashboard');

let app = null;
let server = null;
let botClient = null;

const SCOPES = ['identify', 'guilds'];
const JWT_SECRET = process.env.API_SECRET || 'ultra-suite-secret';
const DASHBOARD_PORT = parseInt(process.env.DASHBOARD_PORT || process.env.API_PORT, 10) || 3000;

// ─── Passport Setup ─────────────────────────
passport.serializeUser((user, done) => done(null, user));
passport.deserializeUser((obj, done) => done(null, obj));

/**
 * Start the dashboard server
 * @param {import('discord.js').Client} client
 */
function startDashboard(client) {
  if (!process.env.OAUTH2_CLIENT_SECRET || !process.env.CLIENT_ID) {
    log.warn('OAUTH2_CLIENT_SECRET ou CLIENT_ID manquant — Dashboard désactivé');
    return;
  }

  botClient = client;
  app = express();

  // ─── Middleware ──────────────────────────
  app.use(cors({
    origin: process.env.DASHBOARD_URL || `http://localhost:${DASHBOARD_PORT}`,
    credentials: true,
  }));
  app.use(express.json());
  app.use(session({
    secret: JWT_SECRET,
    resave: false,
    saveUninitialized: false,
    cookie: { secure: process.env.NODE_ENV === 'production', maxAge: 7 * 24 * 60 * 60 * 1000 },
  }));
  app.use(passport.initialize());
  app.use(passport.session());

  // Passport Discord strategy
  passport.use(new DiscordStrategy({
    clientID: process.env.CLIENT_ID,
    clientSecret: process.env.OAUTH2_CLIENT_SECRET,
    callbackURL: process.env.OAUTH2_REDIRECT_URI || `http://localhost:${DASHBOARD_PORT}/auth/callback`,
    scope: SCOPES,
  }, (accessToken, refreshToken, profile, done) => {
    profile.accessToken = accessToken;
    return done(null, profile);
  }));

  // ─── Static files ──────────────────────
  app.use(express.static(path.join(__dirname, 'public')));

  // ─── Auth Routes ───────────────────────
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

  // ─── API Routes ────────────────────────
  app.get('/api/health', async (req, res) => {
    res.json({
      status: 'healthy',
      uptime: Math.floor(process.uptime()),
      guilds: botClient?.guilds?.cache?.size || 0,
      memory: Math.round(process.memoryUsage().heapUsed / 1024 / 1024),
    });
  });

  app.get('/api/stats', requireAuth, async (req, res) => {
    res.json({
      bot: {
        username: botClient?.user?.tag || 'N/A',
        avatar: botClient?.user?.displayAvatarURL({ size: 128 }),
        guilds: botClient?.guilds?.cache?.size || 0,
        users: botClient?.guilds?.cache?.reduce((s, g) => s + g.memberCount, 0) || 0,
        channels: botClient?.channels?.cache?.size || 0,
        commands: botClient?.commands?.size || 0,
        uptime: Math.floor(process.uptime()),
      },
      memory: {
        heap: Math.round(process.memoryUsage().heapUsed / 1024 / 1024),
        rss: Math.round(process.memoryUsage().rss / 1024 / 1024),
      },
      cache: configService.getCacheStats?.() || {},
    });
  });

  // Guild list (only guilds user can manage AND bot is in)
  app.get('/api/guilds', requireAuth, (req, res) => {
    const managed = filterManagedGuilds(req.user.guilds || []);
    const botGuilds = botClient?.guilds?.cache;

    const guilds = managed.map((g) => {
      const inBot = botGuilds?.has(g.id);
      const botGuild = inBot ? botGuilds.get(g.id) : null;
      return {
        id: g.id, name: g.name, icon: g.icon,
        inBot,
        members: botGuild?.memberCount || 0,
      };
    });

    res.json({ guilds });
  });

  // Guild config
  app.get('/api/guilds/:id', requireAuth, requireGuildAccess, async (req, res) => {
    const guildId = req.params.id;
    const guild = botClient?.guilds?.cache?.get(guildId);

    const config = await configService.get(guildId);
    const modules = await configService.getModules(guildId);

    res.json({
      id: guildId,
      name: guild?.name || 'N/A',
      icon: guild?.iconURL({ size: 128 }),
      members: guild?.memberCount || 0,
      config, modules,
      channels: guild?.channels?.cache?.filter((c) => c.type === 0).map((c) => ({ id: c.id, name: c.name })) || [],
      roles: guild?.roles?.cache?.filter((r) => !r.managed && r.id !== guildId).map((r) => ({ id: r.id, name: r.name, color: r.hexColor })) || [],
    });
  });

  // Update guild config
  app.post('/api/guilds/:id/config', requireAuth, requireGuildAccess, async (req, res) => {
    const guildId = req.params.id;

    if (req.body.config) {
      await configService.set(guildId, req.body.config);
    }
    if (req.body.modules) {
      for (const [name, enabled] of Object.entries(req.body.modules)) {
        await configService.setModule(guildId, name, enabled);
      }
    }

    const config = await configService.get(guildId);
    const modules = await configService.getModules(guildId);
    res.json({ success: true, config, modules });
  });

  // Guild stats
  app.get('/api/guilds/:id/stats', requireAuth, requireGuildAccess, async (req, res) => {
    const guildId = req.params.id;
    const db = getDb();

    const [tickets] = await db('tickets').where('guild_id', guildId).count('id as c').catch(() => [{ c: 0 }]);
    const [warnings] = await db('warnings').where('guild_id', guildId).count('id as c').catch(() => [{ c: 0 }]);
    const [xpUsers] = await db('user_xp').where('guild_id', guildId).count('id as c').catch(() => [{ c: 0 }]);

    res.json({
      tickets: tickets?.c || 0,
      warnings: warnings?.c || 0,
      xpUsers: xpUsers?.c || 0,
    });
  });

  // Leaderboard
  app.get('/api/guilds/:id/leaderboard', requireAuth, requireGuildAccess, async (req, res) => {
    const guildId = req.params.id;
    const db = getDb();
    const type = req.query.type || 'xp';

    let data = [];
    if (type === 'xp') {
      data = await db('user_xp').where('guild_id', guildId).orderBy('xp', 'desc').limit(50);
    } else if (type === 'economy') {
      data = await db('economy').where('guild_id', guildId).orderBy('balance', 'desc').limit(50);
    }

    res.json({ leaderboard: data });
  });

  // ─── SPA fallback ──────────────────────
  app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
  });

  // ─── Error handler ─────────────────────
  app.use((err, req, res, _next) => {
    log.error(`Dashboard error: ${err.message}`);
    res.status(500).json({ error: 'Internal Server Error' });
  });

  // ─── Start ─────────────────────────────
  server = app.listen(DASHBOARD_PORT, () => {
    log.info(`✔ Dashboard démarré sur le port ${DASHBOARD_PORT}`);
  });
}

function stopDashboard() {
  if (server) {
    server.close(() => log.info('Dashboard arrêté'));
    server = null;
  }
}

// ─── Middleware helpers ──────────────────────
function requireAuth(req, res, next) {
  if (req.isAuthenticated()) return next();

  // Also support Bearer token
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
  if (!botClient?.guilds?.cache?.has(guildId)) return res.status(404).json({ error: 'Bot not in this guild' });

  next();
}

function filterManagedGuilds(guilds) {
  return guilds.filter((g) => (parseInt(g.permissions) & 0x20) === 0x20); // MANAGE_GUILD
}

module.exports = { startDashboard, stopDashboard };
