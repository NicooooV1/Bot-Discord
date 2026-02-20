// ===================================
// Ultra Suite — /timeout
// Réduire au silence un membre (timeout Discord natif)
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { t } = require('../../core/i18n');
const { getDb } = require('../../database');
const configService = require('../../core/configService');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('timeout')
    .setDescription('Réduire au silence un membre')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à timeout').setRequired(true))
    .addStringOption((opt) => opt.setName('durée').setDescription('Durée (ex: 5m, 1h, 1d, max 28d)').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison du timeout')),

  async execute(interaction) {
    const target = interaction.options.getUser('membre');
    const durationStr = interaction.options.getString('durée');
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';
    const guildId = interaction.guildId;

    const member = await interaction.guild.members.fetch(target.id).catch(() => null);
    if (!member) {
      return interaction.reply({ content: '❌ Ce membre n\'est pas sur le serveur.', ephemeral: true });
    }
    if (member.user.bot) {
      return interaction.reply({ content: '❌ Impossible de timeout un bot.', ephemeral: true });
    }
    if (member.id === interaction.user.id) {
      return interaction.reply({ content: '❌ Vous ne pouvez pas vous timeout vous-même.', ephemeral: true });
    }
    if (member.roles.highest.position >= interaction.member.roles.highest.position) {
      return interaction.reply({ content: '❌ Ce membre a un rôle supérieur ou égal au vôtre.', ephemeral: true });
    }
    if (!member.moderatable) {
      return interaction.reply({ content: '❌ Je ne peux pas timeout ce membre.', ephemeral: true });
    }

    // Parser la durée
    const durationSec = parseDuration(durationStr);
    if (!durationSec) {
      return interaction.reply({ content: '❌ Durée invalide. Exemples : `5m`, `1h`, `7d` (max 28d)', ephemeral: true });
    }

    // Discord limite le timeout à 28 jours
    const maxTimeout = 28 * 24 * 60 * 60;
    if (durationSec > maxTimeout) {
      return interaction.reply({ content: '❌ La durée maximale d\'un timeout est de **28 jours**.', ephemeral: true });
    }

    await interaction.deferReply();

    const durationMs = durationSec * 1000;
    const expiresAt = new Date(Date.now() + durationMs);

    // DM
    try {
      const dmMsg = await t(guildId, 'moderation.timeout.success', {
        user: target.tag,
        duration: durationStr,
        reason,
      });
      await target.send({
        content: `🔇 Vous avez été réduit au silence sur **${interaction.guild.name}** pour **${durationStr}**.\nRaison : ${reason}`,
      }).catch(() => {});
    } catch { /* DMs fermés */ }

    // Appliquer le timeout
    await member.timeout(durationMs, `${reason} — par ${interaction.user.tag}`);

    // Case en DB
    const db = getDb();
    const last = await db('sanctions').where('guild_id', guildId).max('case_number as max').first();
    const caseNumber = (last?.max || 0) + 1;
    await db('sanctions').insert({
      guild_id: guildId, case_number: caseNumber, type: 'TIMEOUT',
      target_id: target.id, moderator_id: interaction.user.id,
      reason, duration: durationSec, active: true, expires_at: expiresAt,
    });

    // Réponse
    const embed = new EmbedBuilder()
      .setDescription(`🔇 **${target.tag}** a été réduit au silence pour **${durationStr}**.\nRaison : ${reason}`)
      .setColor(0xEB459E)
      .addFields(
        { name: 'Case', value: `#${caseNumber}`, inline: true },
        { name: 'Expire', value: `<t:${Math.floor(expiresAt.getTime() / 1000)}:R>`, inline: true },
      )
      .setTimestamp();

    await interaction.editReply({ embeds: [embed] });

    // Mod log
    try {
      const config = await configService.get(guildId);
      if (config.modLogChannel) {
        const ch = interaction.guild.channels.cache.get(config.modLogChannel);
        if (ch) await ch.send({ embeds: [embed] });
      }
    } catch { /* Non critique */ }
  },
};

function parseDuration(str) {
  const match = str.match(/^(\d+)\s*(s|m|h|d|j|sec|min|hour|day|jour)s?$/i);
  if (!match) return null;
  const num = parseInt(match[1], 10);
  const unit = match[2].toLowerCase();
  if (unit === 's' || unit === 'sec') return num;
  if (unit === 'm' || unit === 'min') return num * 60;
  if (unit === 'h' || unit === 'hour') return num * 3600;
  if (unit === 'd' || unit === 'j' || unit === 'day' || unit === 'jour') return num * 86400;
  return null;
}