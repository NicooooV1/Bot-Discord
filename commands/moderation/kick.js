// ===================================
// Ultra Suite — /kick
// Expulser un membre avec case system
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { t } = require('../../core/i18n');
const { getDb } = require('../../database');
const configService = require('../../core/configService');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('kick')
    .setDescription('Expulser un membre du serveur')
    .setDefaultMemberPermissions(PermissionFlagsBits.KickMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à expulser').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison de l\'expulsion')),

  async execute(interaction) {
    const target = interaction.options.getUser('membre');
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';
    const guildId = interaction.guildId;

    const member = await interaction.guild.members.fetch(target.id).catch(() => null);
    if (!member) {
      return interaction.reply({ content: '❌ Ce membre n\'est pas sur le serveur.', ephemeral: true });
    }
    if (member.id === interaction.user.id) {
      return interaction.reply({ content: '❌ Vous ne pouvez pas vous expulser vous-même.', ephemeral: true });
    }
    if (member.id === interaction.guild.ownerId) {
      return interaction.reply({ content: '❌ Impossible d\'expulser le propriétaire du serveur.', ephemeral: true });
    }
    if (member.roles.highest.position >= interaction.member.roles.highest.position) {
      return interaction.reply({ content: '❌ Ce membre a un rôle supérieur ou égal au vôtre.', ephemeral: true });
    }
    if (!member.kickable) {
      return interaction.reply({ content: '❌ Je ne peux pas expulser ce membre.', ephemeral: true });
    }

    await interaction.deferReply();

    // DM
    try {
      const dmMsg = await t(guildId, 'moderation.kick.dm', { guild: interaction.guild.name, reason });
      await target.send({ content: dmMsg }).catch(() => {});
    } catch { /* DMs fermés */ }

    // Kick
    await member.kick(`${reason} — par ${interaction.user.tag}`);

    // Case
    const db = getDb();
    const last = await db('sanctions').where('guild_id', guildId).max('case_number as max').first();
    const caseNumber = (last?.max || 0) + 1;
    await db('sanctions').insert({
      guild_id: guildId, case_number: caseNumber, type: 'KICK',
      target_id: target.id, moderator_id: interaction.user.id,
      reason, active: false,
    });

    const successMsg = await t(guildId, 'moderation.kick.success', { user: target.tag, reason });
    const embed = new EmbedBuilder()
      .setDescription(`👢 ${successMsg}`)
      .setColor(0xFFA500)
      .addFields({ name: 'Case', value: `#${caseNumber}`, inline: true })
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