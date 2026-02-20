// ===================================
// Ultra Suite — API REST (optionnelle)
// Serveur HTTP léger pour dashboard et monitoring
//
// Activé si API_PORT est défini dans .env
// Authentification par API_SECRET (header Authorization)
//
// Endpoints :
//   GET  /health        — Health check (pas d'auth)
//   GET  /stats         — Stats globales du bot
//   GET  /guilds        — Liste des guilds
//   GET  /guilds/:id    — Config d'une guild
//   POST /guilds/:id/config — Modifier la config
//   POST /cache/flush   — Vider le cache
// ===================================

const http = require('http');
const { createModuleLogger } = require('./logger');
const configService = require('./configService');
const db = require('../database');

const log = createModuleLogger('API');

let server = null;
let botClient = null;

/**
 * Démarre le serveur API si API_PORT est défini
 *
 * @param {import('discord.js').Client} client
 */
function startApi(client) {
  const port = parseInt(process.env.API_PORT, 10);
  if (!port) {
    log.debug('API_PORT non défini — API REST désactivée');
    return;
  }

  botClient = client;

  server = http.createServer(async (req, res) => {
    // CORS headers
    res.setHeader('Access-Control-Allow-Origin', process.env.API_CORS_ORIGIN || '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    // Router
    try {
      const url = new URL(req.url, `http://localhost:${port}`);
      const pathname = url.pathname;

      // Health check (pas d'auth)
      if (pathname === '/health' && req.method === 'GET') {
        return await handleHealth(req, res);
      }

      // Authentification pour tous les autres endpoints
      if (!authenticate(req)) {
        return sendJson(res, 401, { error: 'Unauthorized', message: 'API_SECRET invalide ou manquant' });
      }

      // Routes authentifiées
      if (pathname === '/stats' && req.method === 'GET') {
        return await handleStats(req, res);
      }

      if (pathname === '/guilds' && req.method === 'GET') {
        return await handleGuildsList(req, res);
      }

      const guildMatch = pathname.match(/^\/guilds\/(\d+)$/);
      if (guildMatch && req.method === 'GET') {
        return await handleGuildGet(req, res, guildMatch[1]);
      }

      const guildConfigMatch = pathname.match(/^\/guilds\/(\d+)\/config$/);
      if (guildConfigMatch && req.method === 'POST') {
        return await handleGuildConfigUpdate(req, res, guildConfigMatch[1]);
      }

      if (pathname === '/cache/flush' && req.method === 'POST') {
        return await handleCacheFlush(req, res);
      }

      sendJson(res, 404, { error: 'Not Found' });
    } catch (err) {
      log.error(`Erreur API ${req.method} ${req.url}: ${err.message}`);
      sendJson(res, 500, { error: 'Internal Server Error' });
    }
  });

  server.listen(port, () => {
    log.info(`✔ API REST démarrée sur le port ${port}`);
  });

  server.on('error', (err) => {
    if (err.code === 'EADDRINUSE') {
      log.error(`Port ${port} déjà utilisé — API non démarrée`);
    } else {
      log.error(`Erreur serveur API: ${err.message}`);
    }
  });
}

/**
 * Arrête le serveur API
 */
function stopApi() {
  if (server) {
    server.close(() => {
      log.info('API REST arrêtée');
    });
    server = null;
  }
  botClient = null;
}

// ===================================
// Helpers
// ===================================

/**
 * Vérifie l'authentification par API_SECRET
 */
function authenticate(req) {
  const secret = process.env.API_SECRET;
  if (!secret) {
    log.warn('API_SECRET non défini — toutes les requêtes sont refusées');
    return false;
  }

  const auth = req.headers.authorization;
  return auth === `Bearer ${secret}`;
}

/**
 * Envoie une réponse JSON
 */
function sendJson(res, status, data) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(data));
}

/**
 * Lit le body JSON d'une requête POST
 */
function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
      // Limite à 1MB
      if (body.length > 1024 * 1024) {
        reject(new Error('Body too large'));
      }
    });
    req.on('end', () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (err) {
        reject(new Error('Invalid JSON'));
      }
    });
    req.on('error', reject);
  });
}

// ===================================
// Handlers
// ===================================

/**
 * GET /health — Health check
 */
async function handleHealth(req, res) {
  const dbHealth = await db.healthCheck();

  const status = dbHealth.ok ? 200 : 503;
  sendJson(res, status, {
    status: dbHealth.ok ? 'healthy' : 'degraded',
    uptime: Math.floor(process.uptime()),
    memory: {
      heap: Math.round(process.memoryUsage().heapUsed / 1024 / 1024),
      rss: Math.round(process.memoryUsage().rss / 1024 / 1024),
    },
    database: {
      connected: dbHealth.ok,
      latency: dbHealth.latency,
    },
    guilds: botClient?.guilds?.cache?.size || 0,
  });
}

/**
 * GET /stats — Stats globales du bot
 */
async function handleStats(req, res) {
  const dbStats = await db.getStats();
  const cacheStats = configService.getCacheStats();

  sendJson(res, 200, {
    bot: {
      username: botClient?.user?.tag || 'N/A',
      guilds: botClient?.guilds?.cache?.size || 0,
      users: botClient?.guilds?.cache?.reduce((sum, g) => sum + g.memberCount, 0) || 0,
      channels: botClient?.channels?.cache?.size || 0,
      uptime: Math.floor(process.uptime()),
    },
    memory: {
      heap: Math.round(process.memoryUsage().heapUsed / 1024 / 1024),
      rss: Math.round(process.memoryUsage().rss / 1024 / 1024),
    },
    database: dbStats,
    cache: cacheStats,
    commands: botClient?.commands?.size || 0,
  });
}

/**
 * GET /guilds — Liste des guilds
 */
async function handleGuildsList(req, res) {
  const guilds = botClient?.guilds?.cache?.map((g) => ({
    id: g.id,
    name: g.name,
    members: g.memberCount,
    icon: g.iconURL({ size: 64 }),
  })) || [];

  sendJson(res, 200, { guilds, total: guilds.length });
}

/**
 * GET /guilds/:id — Config d'une guild
 */
async function handleGuildGet(req, res, guildId) {
  const guild = botClient?.guilds?.cache?.get(guildId);
  if (!guild) {
    return sendJson(res, 404, { error: 'Guild not found' });
  }

  const config = await configService.get(guildId);
  const modules = await configService.getModules(guildId);

  sendJson(res, 200, {
    id: guild.id,
    name: guild.name,
    members: guild.memberCount,
    owner: guild.ownerId,
    config,
    modules,
  });
}

/**
 * POST /guilds/:id/config — Modifier la config
 */
async function handleGuildConfigUpdate(req, res, guildId) {
  const guild = botClient?.guilds?.cache?.get(guildId);
  if (!guild) {
    return sendJson(res, 404, { error: 'Guild not found' });
  }

  const body = await readBody(req);

  if (body.config) {
    await configService.set(guildId, body.config);
  }

  if (body.modules) {
    for (const [name, enabled] of Object.entries(body.modules)) {
      await configService.setModule(guildId, name, enabled);
    }
  }

  const config = await configService.get(guildId);
  const modules = await configService.getModules(guildId);

  sendJson(res, 200, { config, modules });
}

/**
 * POST /cache/flush — Vider le cache
 */
async function handleCacheFlush(req, res) {
  configService.flushAll();
  sendJson(res, 200, { message: 'Cache vidé' });
}

module.exports = { startApi, stopApi };