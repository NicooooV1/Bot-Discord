// ===================================
// Ultra Suite — /roleinfo
// Informations sur un rôle
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

module.exports = {
  module: 'utility',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('roleinfo')
    .setDescription('Afficher les informations d\'un rôle')
    .addRoleOption((o) => o.setName('role').setDescription('Le rôle').setRequired(true)),

  async execute(interaction) {
    const role = interaction.options.getRole('role');
    const members = role.members.size;

    const perms = role.permissions.toArray();
    const keyPerms = perms.filter((p) => ['Administrator', 'ManageGuild', 'ManageRoles', 'ManageChannels', 'BanMembers', 'KickMembers', 'ManageMessages', 'MentionEveryone', 'ManageWebhooks', 'ManageNicknames'].includes(p));

    const embed = new EmbedBuilder()
      .setTitle(`🏷️ Rôle — ${role.name}`)
      .setColor(role.color || 0x95A5A6)
      .addFields(
        { name: '🆔 ID', value: role.id, inline: true },
        { name: '🎨 Couleur', value: role.hexColor, inline: true },
        { name: '👥 Membres', value: String(members), inline: true },
        { name: '📍 Position', value: String(role.position), inline: true },
        { name: '💎 Hoisted', value: role.hoist ? '✅' : '❌', inline: true },
        { name: '🤖 Managed', value: role.managed ? '✅' : '❌', inline: true },
        { name: '📢 Mentionnable', value: role.mentionable ? '✅' : '❌', inline: true },
        { name: '📅 Créé le', value: `<t:${Math.floor(role.createdTimestamp / 1000)}:F>`, inline: true },
        { name: '🔑 Permissions clés', value: keyPerms.length ? keyPerms.join(', ') : 'Aucune', inline: false },
      )
      .setTimestamp();

    if (role.icon) embed.setThumbnail(role.iconURL({ size: 256 }));

    return interaction.reply({ embeds: [embed] });
  },
};
