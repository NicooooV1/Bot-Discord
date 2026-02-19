// ===================================
// Ultra Suite — Cron Scheduler
// Tâches planifiées via node-cron
// ===================================

const cron = require('node-cron');
const { createModuleLogger } = require('./logger');
const sanctionQueries = require('../database/sanctionQueries');
const { getDb } = require('../database');

const log = createModuleLogger('Cron');

const jobs = new Map();

/**
 * Démarre les tâches cron récurrentes
 * @param {import('discord.js').Client} client
 */
function startScheduler(client) {
  log.info('Starting cron scheduler...');

  // === Toutes les minutes : vérifier les sanctions expirées ===
  jobs.set(
    'expired_sanctions',
    cron.schedule('* * * * *', async () => {
      try {
        const expired = await sanctionQueries.getExpired();
        for (const sanction of expired) {
          await handleExpiredSanction(client, sanction);
        }
      } catch (err) {
        log.error('Error processing expired sanctions:', err);
      }
    })
  );

  // === Toutes les minutes : vérifier les rappels ===
  jobs.set(
    'reminders',
    cron.schedule('* * * * *', async () => {
      try {
        const db = getDb();
        const now = new Date().toISOString();
        const reminders = await db('reminders')
          .where('fired', false)
          .where('fire_at', '<=', now)
          .limit(20);

        for (const reminder of reminders) {
          await fireReminder(client, reminder);
          await db('reminders').where('id', reminder.id).update({ fired: true });
        }
      } catch (err) {
        log.error('Error processing reminders:', err);
      }
    })
  );

  // === Toutes les minutes : rôles temporaires expirés ===
  jobs.set(
    'temp_roles',
    cron.schedule('* * * * *', async () => {
      try {
        const db = getDb();
        const now = new Date().toISOString();
        const expired = await db('temp_roles').where('expires_at', '<=', now);

        for (const tr of expired) {
          try {
            const guild = await client.guilds.fetch(tr.guild_id);
            const member = await guild.members.fetch(tr.user_id).catch(() => null);
            if (member) {
              await member.roles.remove(tr.role_id, 'Rôle temporaire expiré');
              log.info(`Removed temp role ${tr.role_id} from ${tr.user_id} in ${tr.guild_id}`);
            }
            await db('temp_roles').where('id', tr.id).del();
          } catch (err) {
            log.error(`Error removing temp role ${tr.id}:`, err);
          }
        }
      } catch (err) {
        log.error('Error processing temp roles:', err);
      }
    })
  );

  // === Toutes les 5 minutes : nettoyage channels vocaux temp ===
  jobs.set(
    'temp_voice_cleanup',
    cron.schedule('*/5 * * * *', async () => {
      try {
        const db = getDb();
        const channels = await db('temp_voice_channels').select('*');

        for (const entry of channels) {
          try {
            const guild = await client.guilds.fetch(entry.guild_id).catch(() => null);
            if (!guild) { await db('temp_voice_channels').where('id', entry.id).del(); continue; }

            const channel = guild.channels.cache.get(entry.channel_id);
            if (!channel || channel.members.size === 0) {
              if (channel) await channel.delete('Salon vocal temporaire vide').catch(() => {});
              await db('temp_voice_channels').where('id', entry.id).del();
              log.debug(`Cleaned up temp voice channel ${entry.channel_id}`);
            }
          } catch (err) {
            log.error(`Error cleaning temp voice ${entry.id}:`, err);
          }
        }
      } catch (err) {
        log.error('Error cleaning temp voice channels:', err);
      }
    })
  );

  // === Tous les jours à minuit : purge des logs anciens ===
  jobs.set(
    'log_purge',
    cron.schedule('0 0 * * *', async () => {
      try {
        const db = getDb();
        const cutoff = new Date(Date.now() - 90 * 86400000).toISOString(); // 90 jours
        const deleted = await db('logs').where('timestamp', '<', cutoff).del();
        if (deleted > 0) log.info(`Purged ${deleted} old log entries`);
      } catch (err) {
        log.error('Error purging old logs:', err);
      }
    })
  );

  // === Tous les jours à minuit : snapshot daily_metrics ===
  jobs.set(
    'daily_metrics',
    cron.schedule('1 0 * * *', async () => {
      try {
        const db = getDb();
        const yesterday = new Date(Date.now() - 86400000).toISOString().split('T')[0];
        const guilds = await db('guilds').select('id');

        for (const g of guilds) {
          const exists = await db('daily_metrics').where({ guild_id: g.id, date: yesterday }).first();
          if (!exists) {
            await db('daily_metrics').insert({ guild_id: g.id, date: yesterday });
          }
        }
        log.debug('Daily metrics snapshot created');
      } catch (err) {
        log.error('Error creating daily metrics:', err);
      }
    })
  );

  log.info(`Started ${jobs.size} cron jobs`);
}

/**
 * Gère une sanction expirée (unban / unmute)
 */
async function handleExpiredSanction(client, sanction) {
  try {
    const guild = await client.guilds.fetch(sanction.guild_id).catch(() => null);
    if (!guild) return;

    switch (sanction.type) {
      case 'TEMPBAN': {
        await guild.bans.remove(sanction.target_id, 'Tempban expiré').catch(() => {});
        log.info(`Unbanned ${sanction.target_id} (tempban expired) in ${guild.id}`);
        break;
      }
      case 'TIMEOUT': {
        const member = await guild.members.fetch(sanction.target_id).catch(() => null);
        if (member) await member.timeout(null, 'Timeout expiré').catch(() => {});
        break;
      }
    }

    // Marque comme inactif
    await sanctionQueries.revoke(sanction.guild_id, sanction.case_number, client.user.id);
  } catch (err) {
    log.error(`Error handling expired sanction #${sanction.case_number}:`, err);
  }
}

/**
 * Envoie un rappel
 */
async function fireReminder(client, reminder) {
  try {
    if (reminder.channel_id) {
      const channel = await client.channels.fetch(reminder.channel_id).catch(() => null);
      if (channel) {
        await channel.send(`⏰ <@${reminder.user_id}> **Rappel** : ${reminder.message}`);
      }
    } else {
      const user = await client.users.fetch(reminder.user_id).catch(() => null);
      if (user) {
        await user.send(`⏰ **Rappel** : ${reminder.message}`).catch(() => {});
      }
    }
    log.debug(`Fired reminder ${reminder.id} for ${reminder.user_id}`);
  } catch (err) {
    log.error(`Error firing reminder ${reminder.id}:`, err);
  }
}

/**
 * Arrête tous les crons
 */
function stopScheduler() {
  for (const [name, job] of jobs) {
    job.stop();
    log.debug(`Stopped cron: ${name}`);
  }
  jobs.clear();
  log.info('All cron jobs stopped');
}

module.exports = { startScheduler, stopScheduler };
