// ===================================
// Ultra Suite — /unban
// Débannir un utilisateur avec case system
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');
const configService = require('../../core/configService');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('unban')
    .setDescription('Débannir un utilisateur')
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .addStringOption((opt) => opt.setName('user_id').setDescription('ID Discord de l\'utilisateur à débannir').setRequired(true))
    .addStringOption((opt) => opt.setName('raison').setDescription('Raison du débannissement')),

  async execute(interaction) {
    const targetId = interaction.options.getString('user_id').trim();
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';
    const guildId = interaction.guildId;

    if (!/^\d{17,20}$/.test(targetId)) {
      return interaction.reply({ content: '❌ ID utilisateur invalide. Exemple : `123456789012345678`', ephemeral: true });
    }

    await interaction.deferReply();

    const ban = await interaction.guild.bans.fetch(targetId).catch(() => null);
    if (!ban) {
      return interaction.editReply({ content: '❌ Cet utilisateur n\'est pas banni sur ce serveur.' });
    }

    await interaction.guild.members.unban(targetId, `${reason} — par ${interaction.user.tag}`);

    const db = getDb();
    const last = await db('sanctions').where('guild_id', guildId).max('case_number as max').first();
    const caseNumber = (last?.max || 0) + 1;
    await db('sanctions').insert({
      guild_id: guildId, case_number: caseNumber, type: 'UNBAN',
      target_id: targetId, moderator_id: interaction.user.id,
      reason, active: false,
    });

    await db('sanctions')
      .where('guild_id', guildId).where('target_id', targetId)
      .whereIn('type', ['BAN', 'TEMPBAN']).where('active', true)
      .update({ active: false, revoked_at: new Date(), revoked_by: interaction.user.id });

    const username = ban.user?.tag || targetId;
    const embed = new EmbedBuilder()
      .setDescription(`✅ **${username}** a été débanni.\nRaison : ${reason}`)
      .setColor(0x57F287)
      .addFields({ name: 'Case', value: `#${caseNumber}`, inline: true })
      .setTimestamp();

    await interaction.editReply({ embeds: [embed] });

    try {
      const config = await configService.get(guildId);
      if (config.modLogChannel) {
        const ch = interaction.guild.channels.cache.get(config.modLogChannel);
        if (ch) await ch.send({ embeds: [embed] });
      }
    } catch { /* Non critique */ }
  },
};