// ===================================
// Ultra Suite — Lavalink Manager
// Shoukaku v4 — Connexion au LXC 114
//
// Gestion de la connexion Lavalink v4
// pour le streaming audio haute qualité.
// ===================================

const { Shoukaku, Connectors } = require('shoukaku');
const { createModuleLogger } = require('./logger');

const log = createModuleLogger('Lavalink');

let shoukaku = null;

// ===================================
// Configuration des nœuds Lavalink
// ===================================
function getNodes() {
  return [
    {
      name: process.env.LAVALINK_NAME || 'main',
      url: `${process.env.LAVALINK_HOST || '192.168.1.218'}:${process.env.LAVALINK_PORT || '2333'}`,
      auth: process.env.LAVALINK_PASSWORD || '',
    },
  ];
}

// ===================================
// Options Shoukaku
// ===================================
const SHOUKAKU_OPTIONS = {
  moveOnDisconnect: false,
  resumable: true,
  resumableTimeout: 60,
  reconnectTries: 5,
  reconnectInterval: 5000,
  restTimeout: 30000,
  userAgent: 'UltraSuite/2.1',
};

// ===================================
// Lifecycle
// ===================================

/**
 * Initialise la connexion Shoukaku/Lavalink.
 * Doit être appelé APRÈS client.login() car Shoukaku
 * a besoin du client Discord connecté.
 *
 * @param {import('discord.js').Client} client - Client Discord connecté
 * @returns {Shoukaku|null}
 */
function init(client) {
  if (!process.env.LAVALINK_HOST && !process.env.LAVALINK_PASSWORD) {
    log.warn('LAVALINK_HOST ou LAVALINK_PASSWORD manquant — Lavalink désactivé');
    return null;
  }

  const nodes = getNodes();

  try {
    shoukaku = new Shoukaku(
      new Connectors.DiscordJS(client),
      nodes,
      SHOUKAKU_OPTIONS,
    );

    // Events
    shoukaku.on('ready', (name) => {
      log.info(`Nœud Lavalink "${name}" connecté`);
    });

    shoukaku.on('error', (name, error) => {
      log.error(`Nœud Lavalink "${name}" erreur: ${error.message}`);
    });

    shoukaku.on('close', (name, code, reason) => {
      log.warn(`Nœud Lavalink "${name}" déconnecté (code: ${code}, raison: ${reason || 'N/A'})`);
    });

    shoukaku.on('disconnect', (name, players, moved) => {
      if (moved) return;
      log.warn(`Nœud Lavalink "${name}" déconnecté avec ${players.size} lecteur(s)`);
      // Nettoyer les lecteurs orphelins
      for (const [guildId, player] of players) {
        player.connection.disconnect();
        shoukaku.players.delete(guildId);
      }
    });

    shoukaku.on('reconnecting', (name, left, timeout) => {
      log.info(`Reconnexion à "${name}" (tentatives restantes: ${left}, timeout: ${timeout}ms)`);
    });

    log.info(`Shoukaku initialisé avec ${nodes.length} nœud(s)`);
    return shoukaku;
  } catch (err) {
    log.error(`Erreur initialisation Lavalink: ${err.message}`);
    return null;
  }
}

/**
 * Retourne l'instance Shoukaku.
 *
 * @returns {Shoukaku|null}
 */
function getShoukaku() {
  return shoukaku;
}

/**
 * Retourne un nœud Lavalink disponible.
 * Sélectionne le nœud avec le moins de joueurs.
 *
 * @returns {import('shoukaku').Node|null}
 */
function getNode() {
  if (!shoukaku) return null;
  return shoukaku.options.nodeResolver(shoukaku.nodes);
}

/**
 * Recherche une piste sur Lavalink.
 *
 * @param {string} query - URL ou terme de recherche
 * @param {string} [source='ytsearch'] - Source de recherche (ytsearch, ytmsearch, scsearch)
 * @returns {Promise<import('shoukaku').LavalinkResponse|null>}
 */
async function search(query, source = 'ytsearch') {
  const node = getNode();
  if (!node) return null;

  // Si c'est une URL directe, pas besoin de préfixe
  const isUrl = /^https?:\/\//.test(query);
  const searchQuery = isUrl ? query : `${source}:${query}`;

  try {
    return await node.rest.resolve(searchQuery);
  } catch (err) {
    log.error(`Erreur recherche Lavalink: ${err.message}`);
    return null;
  }
}

/**
 * Crée un lecteur audio pour une guild.
 *
 * @param {string} guildId - ID de la guild
 * @param {string} channelId - ID du salon vocal
 * @returns {Promise<import('shoukaku').Player|null>}
 */
async function createPlayer(guildId, channelId) {
  const node = getNode();
  if (!node) return null;

  try {
    return await shoukaku.joinVoiceChannel({
      guildId,
      channelId,
      shardId: 0,
      deaf: true,
    });
  } catch (err) {
    log.error(`Erreur création lecteur (guild: ${guildId}): ${err.message}`);
    return null;
  }
}

/**
 * Détruit un lecteur et quitte le salon vocal.
 *
 * @param {string} guildId
 */
async function destroyPlayer(guildId) {
  try {
    await shoukaku?.leaveVoiceChannel(guildId);
  } catch (err) {
    log.warn(`Erreur destruction lecteur (guild: ${guildId}): ${err.message}`);
  }
}

/**
 * Health check Lavalink.
 *
 * @returns {{ ok: boolean, nodes: number, players: number }}
 */
function healthCheck() {
  if (!shoukaku) return { ok: false, nodes: 0, players: 0 };

  const connectedNodes = [...shoukaku.nodes.values()].filter((n) => n.state === 1);
  return {
    ok: connectedNodes.length > 0,
    nodes: connectedNodes.length,
    totalNodes: shoukaku.nodes.size,
    players: shoukaku.players.size,
  };
}

/**
 * Ferme proprement toutes les connexions Lavalink.
 */
async function close() {
  if (!shoukaku) return;

  // Déconnecter tous les lecteurs
  for (const [guildId] of shoukaku.players) {
    try {
      await shoukaku.leaveVoiceChannel(guildId);
    } catch { /* ignore */ }
  }

  // Déconnecter les nœuds
  for (const [, node] of shoukaku.nodes) {
    try {
      node.disconnect();
    } catch { /* ignore */ }
  }

  shoukaku = null;
  log.info('Lavalink fermé');
}

// ===================================
// Exports
// ===================================
module.exports = {
  init,
  close,
  getShoukaku,
  getNode,
  search,
  createPlayer,
  destroyPlayer,
  healthCheck,
};
