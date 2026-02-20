// ===================================
// Ultra Suite — /ban
// Bannir un membre avec case system
// Supporte les tempbans (durée optionnelle)
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { t } = require('../../core/i18n');
const { getDb } = require('../../database');
const configService = require('../../core/configService');
const { parseDuration } = require('../../utils/formatters');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('ban')
    .setDescription('Bannir un membre du serveur')
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à bannir').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison du bannissement'))
    .addStringOption((opt) => opt.setName('durée').setDescription('Durée du ban temporaire (ex: 7d, 24h)'))
    .addIntegerOption((opt) => opt.setName('delete_messages').setDescription('Supprimer les messages (jours, 0-7)')
      .setMinValue(0).setMaxValue(7)),

  async execute(interaction) {
    const target = interaction.options.getUser('membre');
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';
    const durationStr = interaction.options.getString('durée');
    const deleteMessageDays = interaction.options.getInteger('delete_messages') || 0;
    const guildId = interaction.guildId;

    const member = await interaction.guild.members.fetch(target.id).catch(() => null);

    // Vérifications de sécurité
    if (member) {
      if (member.id === interaction.user.id) {
        return interaction.reply({ content: '❌ Vous ne pouvez pas vous bannir vous-même.', ephemeral: true });
      }
      if (member.id === interaction.guild.ownerId) {
        return interaction.reply({ content: '❌ Impossible de bannir le propriétaire du serveur.', ephemeral: true });
      }
      if (member.roles.highest.position >= interaction.member.roles.highest.position) {
        return interaction.reply({ content: '❌ Ce membre a un rôle supérieur ou égal au vôtre.', ephemeral: true });
      }
      if (!member.bannable) {
        return interaction.reply({ content: '❌ Je ne peux pas bannir ce membre.', ephemeral: true });
      }
    }

    // Durée du tempban
    let expiresAt = null;
    let banType = 'BAN';
    if (durationStr) {
      const seconds = parseDuration(durationStr);
      if (!seconds || seconds < 60) {
        return interaction.reply({ content: '❌ Durée invalide. Exemples : `1h`, `7d`, `30d`', ephemeral: true });
      }
      expiresAt = new Date(Date.now() + seconds * 1000);
      banType = 'TEMPBAN';
    }

    await interaction.deferReply();

    // DM avant ban
    try {
      const dmMsg = await t(guildId, 'moderation.ban.dm', { guild: interaction.guild.name, reason });
      await target.send({ content: dmMsg }).catch(() => {});
    } catch { /* DMs fermés */ }

    // Ban
    await interaction.guild.members.ban(target.id, {
      reason: `${reason} — par ${interaction.user.tag}`,
      deleteMessageSeconds: deleteMessageDays * 86400,
    });

    // Case
    const db = getDb();
    const last = await db('sanctions').where('guild_id', guildId).max('case_number as max').first();
    const caseNumber = (last?.max || 0) + 1;
    await db('sanctions').insert({
      guild_id: guildId, case_number: caseNumber, type: banType,
      target_id: target.id, moderator_id: interaction.user.id,
      reason, active: true, expires_at: expiresAt,
    });

    const durationText = durationStr ? ` (${durationStr})` : ' (permanent)';
    const embed = new EmbedBuilder()
      .setDescription(`🔨 **${target.tag}** a été banni${durationText}.\nRaison : ${reason}`)
      .setColor(0xED4245)
      .addFields({ name: 'Case', value: `#${caseNumber}`, inline: true })
      .setTimestamp();

    if (expiresAt) {
      embed.addFields({ name: 'Expire', value: `<t:${Math.floor(expiresAt.getTime() / 1000)}:R>`, inline: true });
    }

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