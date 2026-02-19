const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { getWarns, getModLogs } = require('../../utils/database');
const { COLORS } = require('../../utils/logger');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('userinfo')
    .setDescription('👤 Voir les informations et l\'historique d\'un utilisateur')
    .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur').setRequired(true))
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers),

  async execute(interaction) {
    const target = interaction.options.getUser('utilisateur');
    const member = interaction.guild.members.cache.get(target.id);
    const warns = getWarns(interaction.guild.id, target.id);
    const logs = getModLogs(interaction.guild.id, target.id);

    const embed = new EmbedBuilder()
      .setTitle(`👤 Informations — ${target.tag}`)
      .setColor(COLORS.BLUE)
      .setThumbnail(target.displayAvatarURL({ dynamic: true, size: 256 }))
      .addFields(
        { name: '🆔 ID', value: target.id, inline: true },
        { name: '📅 Compte créé', value: `<t:${Math.floor(target.createdTimestamp / 1000)}:R>`, inline: true },
      );

    if (member) {
      embed.addFields(
        { name: '📥 A rejoint le serveur', value: `<t:${Math.floor(member.joinedTimestamp / 1000)}:R>`, inline: true },
        { name: '🎭 Rôle le plus élevé', value: `${member.roles.highest}`, inline: true },
        { name: '🔇 Muté', value: member.isCommunicationDisabled() ? '✅ Oui' : '❌ Non', inline: true },
      );
    }

    // Résumé de modération
    embed.addFields(
      { name: '⚠️ Avertissements actifs', value: `${warns.length}`, inline: true },
      { name: '📋 Actions de modération', value: `${logs.length}`, inline: true },
    );

    // 5 dernières actions
    if (logs.length > 0) {
      const recentLogs = logs.slice(0, 5).map(log =>
        `\`${log.action}\` — <t:${Math.floor(new Date(log.created_at).getTime() / 1000)}:R> par <@${log.moderator_id}>`
      ).join('\n');
      embed.addFields({ name: '📜 Dernières actions', value: recentLogs });
    }

    await interaction.reply({ embeds: [embed] });
  },
};
