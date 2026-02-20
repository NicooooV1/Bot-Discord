// ===================================
// Ultra Suite — Tâche planifiée : Rappels
// Vérifie et envoie les rappels arrivés à échéance
// Exécutée toutes les 30 secondes par le scheduler
// ===================================

const { EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');
const { createModuleLogger } = require('../logger');

const log = createModuleLogger('ReminderTask');

module.exports = {
  name: 'reminders',
  interval: 30_000, // 30 secondes

  async execute(client) {
    const db = getDb();

    try {
      const due = await db('reminders')
        .where('fired', false)
        .where('fire_at', '<=', new Date())
        .limit(20);

      if (due.length === 0) return;

      for (const reminder of due) {
        try {
          const channel = await client.channels.fetch(reminder.channel_id).catch(() => null);
          if (!channel) {
            await db('reminders').where('id', reminder.id).update({ fired: true });
            continue;
          }

          const embed = new EmbedBuilder()
            .setTitle('⏰ Rappel !')
            .setDescription(reminder.message)
            .setColor(0x5865F2)
            .setTimestamp();

          await channel.send({
            content: `<@${reminder.user_id}>`,
            embeds: [embed],
          });

          await db('reminders').where('id', reminder.id).update({ fired: true });
        } catch (err) {
          log.warn(`Erreur rappel #${reminder.id}: ${err.message}`);
          await db('reminders').where('id', reminder.id).update({ fired: true });
        }
      }
    } catch (err) {
      log.error(`Erreur tâche rappels: ${err.message}`);
    }
  },
};