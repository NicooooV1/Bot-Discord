// ===================================
// Ultra Suite — Moderation: /banlist
// Liste les utilisateurs bannis
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const { createEmbed, errorEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'moderation',
  cooldown: 5,
  data: new SlashCommandBuilder()
    .setName('banlist')
    .setDescription('📋 Afficher la liste des utilisateurs bannis')
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .addIntegerOption((opt) => opt.setName('page').setDescription('Numéro de page').setMinValue(1)),

  async execute(interaction) {
    await interaction.deferReply();

    try {
      const bans = await interaction.guild.bans.fetch();
      const banArray = [...bans.values()];

      if (banArray.length === 0) {
        return interaction.editReply({ content: '✅ Aucun utilisateur banni sur ce serveur.' });
      }

      const perPage = 10;
      const totalPages = Math.ceil(banArray.length / perPage);
      const page = Math.min(interaction.options.getInteger('page') || 1, totalPages);
      const start = (page - 1) * perPage;
      const pageBans = banArray.slice(start, start + perPage);

      const embed = createEmbed('moderation')
        .setTitle(`🔨 Utilisateurs bannis (${banArray.length})`)
        .setDescription(
          pageBans
            .map(
              (ban, i) =>
                `**${start + i + 1}.** ${ban.user.tag} (\`${ban.user.id}\`)\n> ${ban.reason || 'Aucune raison'}`
            )
            .join('\n\n')
        )
        .setFooter({ text: `Page ${page}/${totalPages} • ${banArray.length} bans au total` });

      await interaction.editReply({ embeds: [embed] });
    } catch (err) {
      await interaction.editReply({ embeds: [errorEmbed('❌ Impossible de récupérer la liste des bans.')] });
    }
  },
};
