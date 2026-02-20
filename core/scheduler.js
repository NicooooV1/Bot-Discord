// ===================================
// Ultra Suite — Scheduler
// Tâches planifiées multi-serveur
//
// Exécute des tâches récurrentes et ponctuelles :
// - Expiration des tempbans / timeouts
// - Expiration des rôles temporaires
// - Rappels (reminders)
// - Annonces planifiées
// - Collecte des métriques quotidiennes
// - Nettoyage des données obsolètes
//
// Toutes les tâches itèrent sur TOUTES les guilds
// (architecture multi-serveur)
// ===================================

const { createModuleLogger } = require('./logger');
const { getDb } = require('../database');

const log = createModuleLogger('Scheduler');

// Intervalles des tâches (en ms)
const INTERVALS = {
  EXPIRATIONS: 30 * 1000,      // 30s — tempbans, temproles
  REMINDERS: 15 * 1000,         // 15s — rappels utilisateurs
  ANNOUNCEMENTS: 60 * 1000,     // 1min — annonces planifiées
  METRICS: 5 * 60 * 1000,       // 5min — métriques
  CLEANUP: 60 * 60 * 1000,      // 1h — nettoyage vieux logs
};

// Stockage des intervals pour le cleanup
const timers = [];

// Référence au client Discord
let botClient = null;

/**
 * Démarre toutes les tâches planifiées
 *
 * @param {import('discord.js').Client} client
 */
function startScheduler(client) {
  botClient = client;

  log.info('Démarrage des tâches planifiées...');

  // === Expirations (tempbans, temproles) ===
  timers.push(
    setInterval(() => runSafe('Expirations', processExpirations), INTERVALS.EXPIRATIONS)
  );

  // === Rappels utilisateurs ===
  timers.push(
    setInterval(() => runSafe('Reminders', processReminders), INTERVALS.REMINDERS)
  );

  // === Annonces planifiées ===
  timers.push(
    setInterval(() => runSafe('Announcements', processAnnouncements), INTERVALS.ANNOUNCEMENTS)
  );

  // === Métriques quotidiennes ===
  timers.push(
    setInterval(() => runSafe('Metrics', collectMetrics), INTERVALS.METRICS)
  );

  // === Nettoyage des données obsolètes ===
  timers.push(
    setInterval(() => runSafe('Cleanup', cleanupOldData), INTERVALS.CLEANUP)
  );

  log.info(`${timers.length} tâche(s) planifiée(s) démarrée(s)`);
}

/**
 * Arrête toutes les tâches planifiées
 */
function stopScheduler() {
  for (const timer of timers) {
    clearInterval(timer);
  }
  timers.length = 0;
  botClient = null;
  log.info('Tâches planifiées arrêtées');
}

/**
 * Exécute une tâche de manière sécurisée (try/catch)
 *
 * @param {string} name — Nom de la tâche (pour les logs)
 * @param {Function} fn — Fonction à exécuter
 */
async function runSafe(name, fn) {
  try {
    await fn();
  } catch (err) {
    log.error(`Erreur tâche "${name}": ${err.message}`);
    log.error(err.stack);
  }
}

// ===================================
// Tâche : Expirations
// Tempbans, timeouts, rôles temporaires
// ===================================
async function processExpirations() {
  const db = getDb();
  const now = new Date();

  // --- Sanctions expirées (tempbans) ---
  const expiredSanctions = await db('sanctions')
    .where('active', true)
    .whereNotNull('expires_at')
    .where('expires_at', '<=', now)
    .select('id', 'guild_id', 'target_id', 'type');

  for (const sanction of expiredSanctions) {
    try {
      const guild = botClient?.guilds?.cache?.get(sanction.guild_id);
      if (!guild) continue;

      if (sanction.type === 'TEMPBAN' || sanction.type === 'BAN') {
        // Débannir
        await guild.members.unban(sanction.target_id, 'Tempban expiré').catch(() => {});
        log.info(`Tempban expiré : user ${sanction.target_id} dans guild ${sanction.guild_id}`);
      }

      // Marquer comme inactif
      await db('sanctions').where('id', sanction.id).update({
        active: false,
        revoked_at: now,
        revoked_by: botClient.user.id,
      });
    } catch (err) {
      log.error(`Erreur expiration sanction #${sanction.id}: ${err.message}`);
    }
  }

  // --- Rôles temporaires expirés ---
  const expiredRoles = await db('temp_roles')
    .where('expires_at', '<=', now)
    .select('id', 'guild_id', 'user_id', 'role_id');

  for (const tempRole of expiredRoles) {
    try {
      const guild = botClient?.guilds?.cache?.get(tempRole.guild_id);
      if (!guild) continue;

      const member = await guild.members.fetch(tempRole.user_id).catch(() => null);
      if (member) {
        await member.roles.remove(tempRole.role_id, 'Rôle temporaire expiré').catch(() => {});
        log.info(`TempRole expiré : role ${tempRole.role_id} retiré de ${tempRole.user_id}`);
      }

      await db('temp_roles').where('id', tempRole.id).del();
    } catch (err) {
      log.error(`Erreur expiration temp_role #${tempRole.id}: ${err.message}`);
    }
  }
}

// ===================================
// Tâche : Rappels
// ===================================
async function processReminders() {
  const db = getDb();
  const now = new Date();

  const dueReminders = await db('reminders')
    .where('fired', false)
    .where('fire_at', '<=', now)
    .limit(20); // Batch de 20 max par tick

  for (const reminder of dueReminders) {
    try {
      // Marquer comme envoyé immédiatement (évite les doublons)
      await db('reminders').where('id', reminder.id).update({ fired: true });

      // Envoyer en DM ou dans le channel
      if (reminder.channel_id) {
        const channel = botClient?.channels?.cache?.get(reminder.channel_id);
        if (channel) {
          await channel.send({
            content: `⏰ <@${reminder.user_id}> Rappel : ${reminder.message}`,
          }).catch(() => {});
        }
      } else {
        // DM
        const user = await botClient?.users?.fetch(reminder.user_id).catch(() => null);
        if (user) {
          await user.send({
            content: `⏰ Rappel : ${reminder.message}`,
          }).catch(() => {});
        }
      }

      log.debug(`Rappel #${reminder.id} envoyé à ${reminder.user_id}`);
    } catch (err) {
      log.error(`Erreur rappel #${reminder.id}: ${err.message}`);
    }
  }
}

// ===================================
// Tâche : Annonces planifiées
// ===================================
async function processAnnouncements() {
  const db = getDb();
  const now = new Date();

  const dueAnnouncements = await db('announcements')
    .where('enabled', true)
    .whereNotNull('next_run_at')
    .where('next_run_at', '<=', now)
    .limit(10);

  for (const ann of dueAnnouncements) {
    try {
      const channel = botClient?.channels?.cache?.get(ann.channel_id);
      if (!channel) {
        log.warn(`Annonce #${ann.id} : channel ${ann.channel_id} introuvable`);
        continue;
      }

      // Parser le contenu
      let content;
      try {
        content = typeof ann.content === 'string' ? JSON.parse(ann.content) : ann.content;
      } catch {
        content = { content: String(ann.content) };
      }

      await channel.send(content).catch(() => {});
      log.debug(`Annonce #${ann.id} envoyée dans ${ann.channel_id}`);

      // Calculer la prochaine exécution (si cron)
      if (ann.cron_expr) {
        // Pour l'instant, désactiver si pas de lib cron
        // TODO: intégrer node-cron pour les expressions cron
        await db('announcements').where('id', ann.id).update({
          next_run_at: null,
          enabled: false,
        });
      } else {
        // Exécution unique → désactiver
        await db('announcements').where('id', ann.id).update({
          enabled: false,
        });
      }
    } catch (err) {
      log.error(`Erreur annonce #${ann.id}: ${err.message}`);
    }
  }
}

// ===================================
// Tâche : Métriques quotidiennes
// Collecte les stats par guild par jour
// ===================================
async function collectMetrics() {
  // Cette tâche ne s'exécute qu'une fois par jour par guild
  // On vérifie si la ligne du jour existe déjà
  const db = getDb();
  const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD

  const guilds = botClient?.guilds?.cache;
  if (!guilds || guilds.size === 0) return;

  for (const [guildId, guild] of guilds) {
    try {
      const existing = await db('daily_metrics')
        .where('guild_id', guildId)
        .where('date', today)
        .first();

      if (!existing) {
        // Créer l'entrée du jour
        await db('daily_metrics').insert({
          guild_id: guildId,
          date: today,
          messages: 0,
          active_users: 0,
          new_members: 0,
          left_members: 0,
          voice_minutes: 0,
          commands_used: 0,
          tickets_opened: 0,
          sanctions_issued: 0,
        });
      }
    } catch {
      // Ignore les erreurs de métriques (non critique)
    }
  }
}

// ===================================
// Tâche : Nettoyage
// Supprime les données obsolètes
// ===================================
async function cleanupOldData() {
  const db = getDb();

  try {
    // Supprimer les logs de plus de 90 jours
    const logCutoff = new Date();
    logCutoff.setDate(logCutoff.getDate() - 90);
    const deletedLogs = await db('logs')
      .where('timestamp', '<', logCutoff)
      .del();
    if (deletedLogs > 0) {
      log.info(`Nettoyage : ${deletedLogs} log(s) supprimé(s) (> 90 jours)`);
    }

    // Supprimer les rappels déclenchés de plus de 7 jours
    const reminderCutoff = new Date();
    reminderCutoff.setDate(reminderCutoff.getDate() - 7);
    const deletedReminders = await db('reminders')
      .where('fired', true)
      .where('fire_at', '<', reminderCutoff)
      .del();
    if (deletedReminders > 0) {
      log.debug(`Nettoyage : ${deletedReminders} rappel(s) archivé(s)`);
    }

    // Supprimer les sessions vocales non fermées de plus de 24h
    // (le bot a probablement redémarré sans les fermer)
    const voiceCutoff = new Date();
    voiceCutoff.setHours(voiceCutoff.getHours() - 24);
    const staleVoice = await db('voice_sessions')
      .whereNull('left_at')
      .where('joined_at', '<', voiceCutoff)
      .del();
    if (staleVoice > 0) {
      log.info(`Nettoyage : ${staleVoice} session(s) vocale(s) orpheline(s) supprimée(s)`);
    }

    // Supprimer les métriques de plus de 365 jours
    const metricsCutoff = new Date();
    metricsCutoff.setDate(metricsCutoff.getDate() - 365);
    await db('daily_metrics')
      .where('date', '<', metricsCutoff.toISOString().slice(0, 10))
      .del();

  } catch (err) {
    log.error(`Erreur nettoyage : ${err.message}`);
  }
}

module.exports = { startScheduler, stopScheduler };