// ===================================
// Ultra Suite — Utility: /userinfo
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const { createEmbed } = require('../../utils/embeds');
const { getDb } = require('../../database');

module.exports = {
  module: 'utility',
  cooldown: 5,
  data: new SlashCommandBuilder()
    .setName('userinfo')
    .setDescription('Affiche les informations d\'un utilisateur')
    .addUserOption((opt) => opt.setName('user').setDescription('Utilisateur')),

  async execute(interaction) {
    const user = interaction.options.getUser('user') || interaction.user;
    const member = await interaction.guild.members.fetch(user.id).catch(() => null);

    const embed = createEmbed('primary')
      .setTitle(`Profil de ${user.tag}`)
      .setThumbnail(user.displayAvatarURL({ size: 256 }))
      .addFields(
        { name: '🆔 ID', value: user.id, inline: true },
        { name: '📅 Compte créé', value: `<t:${Math.floor(user.createdTimestamp / 1000)}:R>`, inline: true },
        { name: '🤖 Bot', value: user.bot ? 'Oui' : 'Non', inline: true }
      );

    if (member) {
      const roles = member.roles.cache
        .filter((r) => r.id !== interaction.guild.id)
        .sort((a, b) => b.position - a.position)
        .map((r) => `${r}`)
        .slice(0, 15);

      embed.addFields(
        { name: '📥 A rejoint', value: `<t:${Math.floor(member.joinedTimestamp / 1000)}:R>`, inline: true },
        { name: '🎨 Surnom', value: member.nickname || 'Aucun', inline: true },
        { name: '🔝 Rôle le plus haut', value: `${member.roles.highest}`, inline: true },
        { name: `🎭 Rôles (${member.roles.cache.size - 1})`, value: roles.length > 0 ? roles.join(', ') : 'Aucun' }
      );

      // XP data si disponible
      const db = getDb();
      const userData = await db('users').where({ guild_id: interaction.guild.id, user_id: user.id }).first();
      if (userData) {
        embed.addFields(
          { name: '⭐ Niveau', value: `${userData.level}`, inline: true },
          { name: '✨ XP', value: `${userData.xp}`, inline: true },
          { name: '💬 Messages', value: `${userData.total_messages}`, inline: true }
        );
      }
    }

    return interaction.reply({ embeds: [embed] });
  },
};
