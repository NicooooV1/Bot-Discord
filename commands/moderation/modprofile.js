// ===================================
// Ultra Suite — Moderation: /modprofile
// Profil de modération d'un utilisateur
// (renommé pour éviter le doublon avec /userinfo utility)
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const sanctionQueries = require('../../database/sanctionQueries');
const { createEmbed, errorEmbed } = require('../../utils/embeds');
const { relativeTime } = require('../../utils/formatters');

module.exports = {
  module: 'moderation',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('modprofile')
    .setDescription('👤 Voir le profil de modération d\'un utilisateur')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('L\'utilisateur').setRequired(true)),

  async execute(interaction) {
    await interaction.deferReply();
    const user = interaction.options.getUser('membre');
    const member = interaction.guild.members.cache.get(user.id);

    // Compter les sanctions par type
    const counts = await sanctionQueries.countForUser(interaction.guild.id, user.id);
    const activeWarns = await sanctionQueries.activeWarns(interaction.guild.id, user.id);

    // 5 dernières sanctions
    const recent = await sanctionQueries.listForUser(interaction.guild.id, user.id, { limit: 5 });

    const embed = createEmbed('moderation')
      .setTitle(`👤 Profil modération — ${user.tag}`)
      .setThumbnail(user.displayAvatarURL({ size: 256 }))
      .addFields(
        { name: '🆔 ID', value: user.id, inline: true },
        { name: '📅 Compte créé', value: relativeTime(user.createdAt), inline: true },
      );

    if (member) {
      embed.addFields(
        { name: '📥 A rejoint', value: relativeTime(member.joinedAt), inline: true },
        { name: '🎭 Rôle le + élevé', value: `${member.roles.highest}`, inline: true },
        { name: '🔇 Muté', value: member.isCommunicationDisabled() ? '✅ Oui' : '❌ Non', inline: true },
      );
    }

    embed.addFields(
      {
        name: '📊 Sanctions',
        value: [
          `⚠️ Warns actifs: **${activeWarns}**`,
          `🔇 Timeouts: **${counts.TIMEOUT || 0}**`,
          `👢 Kicks: **${counts.KICK || 0}**`,
          `🔨 Bans: **${(counts.BAN || 0) + (counts.TEMPBAN || 0)}**`,
          `🧹 Softbans: **${counts.SOFTBAN || 0}**`,
        ].join('\n'),
        inline: false,
      }
    );

    if (recent.length > 0) {
      const recentList = recent
        .map((s) => `\`#${s.case_number}\` **${s.type}** — <t:${Math.floor(new Date(s.created_at).getTime() / 1000)}:R> par <@${s.moderator_id}>`)
        .join('\n');
      embed.addFields({ name: '📜 Dernières sanctions', value: recentList });
    }

    await interaction.editReply({ embeds: [embed] });
  },
};
