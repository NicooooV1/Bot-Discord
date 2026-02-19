const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { COLORS } = require('../../utils/logger');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('banlist')
    .setDescription('📋 Afficher la liste des utilisateurs bannis')
    .addIntegerOption(opt =>
      opt.setName('page')
        .setDescription('Numéro de page')
        .setMinValue(1)
    )
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers),

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

      const embed = new EmbedBuilder()
        .setTitle(`🔨 Utilisateurs bannis (${banArray.length})`)
        .setColor(COLORS.RED)
        .setDescription(
          pageBans.map((ban, i) =>
            `**${start + i + 1}.** ${ban.user.tag} (\`${ban.user.id}\`)\n> ${ban.reason || 'Aucune raison'}`
          ).join('\n\n')
        )
        .setFooter({ text: `Page ${page}/${totalPages} • ${banArray.length} bans au total` })
        .setTimestamp();

      await interaction.editReply({ embeds: [embed] });
    } catch (error) {
      console.error('[BANLIST]', error);
      await interaction.editReply({ content: '❌ Impossible de récupérer la liste des bans.' });
    }
  },
};
