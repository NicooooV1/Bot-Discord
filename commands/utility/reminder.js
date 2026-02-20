// ===================================
// Ultra Suite — /reminder
// Rappels personnels
// /reminder set | list | delete
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'utility',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('reminder')
    .setDescription('Gérer vos rappels personnels')
    .addSubcommand((sub) =>
      sub.setName('set').setDescription('Créer un rappel')
        .addStringOption((opt) => opt.setName('durée').setDescription('Dans combien de temps ? (ex: 30m, 2h, 1d)').setRequired(true))
        .addStringOption((opt) => opt.setName('message').setDescription('Message du rappel').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('list').setDescription('Voir vos rappels actifs'))
    .addSubcommand((sub) =>
      sub.setName('delete').setDescription('Supprimer un rappel')
        .addIntegerOption((opt) => opt.setName('id').setDescription('ID du rappel').setRequired(true))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const userId = interaction.user.id;
    const db = getDb();

    // === SET ===
    if (sub === 'set') {
      const durationStr = interaction.options.getString('durée');
      const message = interaction.options.getString('message');

      const seconds = parseDuration(durationStr);
      if (!seconds) {
        return interaction.reply({ content: '❌ Durée invalide. Exemples : `30m`, `2h`, `1d`, `1d12h`', ephemeral: true });
      }

      if (seconds < 60) return interaction.reply({ content: '❌ Minimum 1 minute.', ephemeral: true });
      if (seconds > 30 * 86400) return interaction.reply({ content: '❌ Maximum 30 jours.', ephemeral: true });

      const fireAt = new Date(Date.now() + seconds * 1000);

      await db('reminders').insert({
        guild_id: guildId,
        user_id: userId,
        channel_id: interaction.channel.id,
        message,
        fire_at: fireAt,
        fired: false,
      });

      return interaction.reply({
        content: `✅ Rappel créé ! Je vous rappellerai <t:${Math.floor(fireAt.getTime() / 1000)}:R>.\n📝 **${message}**`,
        ephemeral: true,
      });
    }

    // === LIST ===
    if (sub === 'list') {
      const reminders = await db('reminders')
        .where('guild_id', guildId)
        .where('user_id', userId)
        .where('fired', false)
        .orderBy('fire_at', 'asc')
        .limit(10);

      if (reminders.length === 0) {
        return interaction.reply({ content: 'ℹ️ Aucun rappel actif.', ephemeral: true });
      }

      const lines = reminders.map((r) => {
        const ts = Math.floor(new Date(r.fire_at).getTime() / 1000);
        return `**#${r.id}** — <t:${ts}:R>\n   ${r.message.slice(0, 100)}`;
      });

      const embed = new EmbedBuilder()
        .setTitle('⏰ Vos rappels')
        .setDescription(lines.join('\n\n'))
        .setColor(0x5865F2)
        .setFooter({ text: `${reminders.length} rappel(s) actif(s)` });

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    // === DELETE ===
    if (sub === 'delete') {
      const id = interaction.options.getInteger('id');
      const deleted = await db('reminders')
        .where('id', id)
        .where('guild_id', guildId)
        .where('user_id', userId)
        .del();

      if (!deleted) return interaction.reply({ content: '❌ Rappel introuvable ou pas le vôtre.', ephemeral: true });
      return interaction.reply({ content: `✅ Rappel **#${id}** supprimé.`, ephemeral: true });
    }
  },
};

function parseDuration(str) {
  let total = 0;
  const regex = /(\d+)\s*(s|m|h|d|j|min|hour|day|jour)s?/gi;
  let match;
  while ((match = regex.exec(str)) !== null) {
    const num = parseInt(match[1], 10);
    const unit = match[2].toLowerCase();
    if (unit === 's') total += num;
    else if (unit === 'm' || unit === 'min') total += num * 60;
    else if (unit === 'h' || unit === 'hour') total += num * 3600;
    else if (unit === 'd' || unit === 'j' || unit === 'day' || unit === 'jour') total += num * 86400;
  }
  return total || null;
}