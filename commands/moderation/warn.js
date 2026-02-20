// ===================================
// Ultra Suite — /warn
// Avertissement avec case system
// Auto-action si maxWarns atteint (timeout/kick/ban)
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { t } = require('../../core/i18n');
const { getDb } = require('../../database');
const configService = require('../../core/configService');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('warn')
    .setDescription('Donner un avertissement à un membre')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à avertir').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison de l\'avertissement').setRequired(false)),

  async execute(interaction) {
    const target = interaction.options.getUser('membre');
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';
    const guildId = interaction.guildId;

    const member = await interaction.guild.members.fetch(target.id).catch(() => null);
    if (!member) {
      return interaction.reply({ content: '❌ Ce membre n\'est pas sur le serveur.', ephemeral: true });
    }
    if (member.user.bot) {
      return interaction.reply({ content: '❌ Impossible d\'avertir un bot.', ephemeral: true });
    }
    if (member.id === interaction.user.id) {
      return interaction.reply({ content: '❌ Vous ne pouvez pas vous avertir vous-même.', ephemeral: true });
    }

    await interaction.deferReply();

    const db = getDb();
    const config = await configService.get(guildId);

    // Créer le warn
    const last = await db('sanctions').where('guild_id', guildId).max('case_number as max').first();
    const caseNumber = (last?.max || 0) + 1;
    await db('sanctions').insert({
      guild_id: guildId, case_number: caseNumber, type: 'WARN',
      target_id: target.id, moderator_id: interaction.user.id,
      reason, active: true,
    });

    // Compter les warns actifs
    const activeWarns = await db('sanctions')
      .where('guild_id', guildId)
      .where('target_id', target.id)
      .where('type', 'WARN')
      .where('active', true)
      .count('id as count')
      .first();

    const warnCount = activeWarns?.count || 1;
    const maxWarns = config.automod?.maxWarns || 5;

    // DM
    try {
      const dmMsg = await t(guildId, 'moderation.warn.dm', { guild: interaction.guild.name, reason });
      await target.send({ content: `${dmMsg}\nAvertissements : **${warnCount}/${maxWarns}**` }).catch(() => {});
    } catch { /* DMs fermés */ }

    // Réponse
    const successMsg = await t(guildId, 'moderation.warn.success', {
      user: target.tag, reason, count: String(warnCount), max: String(maxWarns),
    });

    const embed = new EmbedBuilder()
      .setDescription(`⚠️ ${successMsg}`)
      .setColor(0xFEE75C)
      .addFields(
        { name: 'Case', value: `#${caseNumber}`, inline: true },
        { name: 'Warns', value: `${warnCount}/${maxWarns}`, inline: true },
      )
      .setTimestamp();

    // Auto-action si seuil atteint
    if (warnCount >= maxWarns) {
      const action = config.automod?.warnAction || 'TIMEOUT';
      const actionDuration = config.automod?.warnActionDuration || 3600;
      let actionMsg = '';

      try {
        if (action === 'TIMEOUT' && member.moderatable) {
          await member.timeout(actionDuration * 1000, `Auto-sanction : ${maxWarns} avertissements`);
          actionMsg = `🔇 Timeout automatique (${formatDuration(actionDuration)})`;
        } else if (action === 'KICK' && member.kickable) {
          await member.kick(`Auto-sanction : ${maxWarns} avertissements`);
          actionMsg = '👢 Expulsé automatiquement';
        } else if (action === 'BAN' && member.bannable) {
          await interaction.guild.members.ban(target.id, { reason: `Auto-sanction : ${maxWarns} avertissements` });
          actionMsg = '🔨 Banni automatiquement';
        }
      } catch { /* Permissions insuffisantes */ }

      if (actionMsg) {
        embed.addFields({ name: '🚨 Auto-sanction', value: `${actionMsg} (${maxWarns} warns atteints)`, inline: false });

        // Réinitialiser les warns actifs
        await db('sanctions')
          .where('guild_id', guildId)
          .where('target_id', target.id)
          .where('type', 'WARN')
          .where('active', true)
          .update({ active: false });
      }
    }

    await interaction.editReply({ embeds: [embed] });

    // Mod log
    try {
      if (config.modLogChannel) {
        const ch = interaction.guild.channels.cache.get(config.modLogChannel);
        if (ch) await ch.send({ embeds: [embed] });
      }
    } catch { /* Non critique */ }
  },
};

function formatDuration(seconds) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}min`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}j`;
}