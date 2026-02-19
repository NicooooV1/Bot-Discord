// ===================================
// Ultra Suite — Express API Skeleton
// Préparé mais inactif (futur dashboard)
// ===================================

const express = require('express');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('API');

let server = null;

/**
 * Démarre le serveur API (uniquement si API_ENABLED=true)
 * @param {import('discord.js').Client} client
 */
function startApi(client) {
  const enabled = process.env.API_ENABLED === 'true';
  const port = parseInt(process.env.API_PORT) || 3000;

  if (!enabled) {
    log.info('API disabled (set API_ENABLED=true to enable)');
    return;
  }

  const app = express();
  app.use(express.json());

  // === Health check ===
  app.get('/health', (req, res) => {
    res.json({
      status: 'ok',
      uptime: process.uptime(),
      guilds: client.guilds.cache.size,
      users: client.users.cache.size,
      ping: client.ws.ping,
    });
  });

  // === Info basique ===
  app.get('/api/info', (req, res) => {
    res.json({
      name: 'Ultra Suite Bot',
      version: require('../../package.json').version,
      guilds: client.guilds.cache.size,
    });
  });

  // === TODO: Routes futures pour le dashboard ===
  // app.get('/api/guilds/:id/config', authMiddleware, ...)
  // app.patch('/api/guilds/:id/config', authMiddleware, ...)
  // app.get('/api/guilds/:id/stats', authMiddleware, ...)
  // app.get('/api/guilds/:id/logs', authMiddleware, ...)

  // === 404 ===
  app.use((req, res) => {
    res.status(404).json({ error: 'Not found' });
  });

  // === Error handler ===
  app.use((err, req, res, _next) => {
    log.error('API error:', err);
    res.status(500).json({ error: 'Internal server error' });
  });

  server = app.listen(port, () => {
    log.info(`API listening on port ${port}`);
  });
}

/**
 * Arrête le serveur proprement
 */
function stopApi() {
  if (server) {
    server.close();
    log.info('API server stopped');
  }
}

module.exports = { startApi, stopApi };
