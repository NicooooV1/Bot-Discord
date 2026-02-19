// ===================================
// Ultra Suite — Utility: /reminder
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const { getDb } = require('../../database');
const { parseDuration, formatDuration } = require('../../utils/formatters');
const { successEmbed, errorEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'utility',
  cooldown: 5,
  data: new SlashCommandBuilder()
    .setName('reminder')
    .setDescription('Gère tes rappels')
    .addSubcommand((sub) =>
      sub
        .setName('set')
        .setDescription('Crée un rappel')
        .addStringOption((opt) => opt.setName('durée').setDescription('Dans combien de temps (ex: 1h30m)').setRequired(true))
        .addStringOption((opt) => opt.setName('message').setDescription('Ce dont tu veux être rappelé').setRequired(true))
    )
    .addSubcommand((sub) => sub.setName('list').setDescription('Liste tes rappels'))
    .addSubcommand((sub) =>
      sub
        .setName('cancel')
        .setDescription('Annule un rappel')
        .addIntegerOption((opt) => opt.setName('id').setDescription('ID du rappel').setRequired(true))
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();

    switch (sub) {
      case 'set': {
        const durationStr = interaction.options.getString('durée');
        const message = interaction.options.getString('message');
        const seconds = parseDuration(durationStr); // returns seconds

        if (!seconds || seconds < 60) {
          return interaction.reply({ embeds: [errorEmbed('❌ Durée invalide (minimum 1m).')], ephemeral: true });
        }
        if (seconds > 30 * 24 * 60 * 60) {
          return interaction.reply({ embeds: [errorEmbed('❌ Maximum 30 jours.')], ephemeral: true });
        }

        const expiresAt = new Date(Date.now() + seconds * 1000).toISOString();

        const [id] = await db('reminders').insert({
          guild_id: interaction.guild.id,
          user_id: interaction.user.id,
          channel_id: interaction.channel.id,
          message,
          fire_at: expiresAt,
        });

        return interaction.reply({
          embeds: [successEmbed(`⏰ Rappel #${id} dans **${formatDuration(seconds)}**\n> ${message}`)],
          ephemeral: true,
        });
      }

      case 'list': {
        const reminders = await db('reminders')
          .where({ user_id: interaction.user.id, fired: false })
          .orderBy('fire_at');

        if (reminders.length === 0) {
          return interaction.reply({ content: '📭 Aucun rappel actif.', ephemeral: true });
        }

        const list = reminders
          .map((r) => `**#${r.id}** — <t:${Math.floor(new Date(r.fire_at).getTime() / 1000)}:R>\n> ${r.message}`)
          .join('\n\n');

        return interaction.reply({ content: `⏰ **Tes rappels :**\n\n${list}`, ephemeral: true });
      }

      case 'cancel': {
        const id = interaction.options.getInteger('id');
        const deleted = await db('reminders')
          .where({ id, user_id: interaction.user.id, fired: false })
          .delete();

        if (deleted === 0) {
          return interaction.reply({ embeds: [errorEmbed('❌ Rappel introuvable.')], ephemeral: true });
        }

        return interaction.reply({ embeds: [successEmbed(`✅ Rappel #${id} annulé.`)], ephemeral: true });
      }
    }
  },
};
