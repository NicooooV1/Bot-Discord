const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { getWarns, removeWarn, clearWarns } = require('../../utils/database');
const { COLORS } = require('../../utils/logger');
const { errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('warnings')
    .setDescription('📋 Gérer les avertissements d\'un utilisateur')
    .addSubcommand(sub =>
      sub.setName('list')
        .setDescription('Voir les avertissements d\'un utilisateur')
        .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur').setRequired(true))
    )
    .addSubcommand(sub =>
      sub.setName('remove')
        .setDescription('Retirer un avertissement par son ID')
        .addIntegerOption(opt => opt.setName('id').setDescription('ID de l\'avertissement').setRequired(true))
    )
    .addSubcommand(sub =>
      sub.setName('clear')
        .setDescription('Supprimer tous les avertissements d\'un utilisateur')
        .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur').setRequired(true))
    )
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers),

  async execute(interaction) {
    const subcommand = interaction.options.getSubcommand();

    if (subcommand === 'list') {
      const target = interaction.options.getUser('utilisateur');
      const warns = getWarns(interaction.guild.id, target.id);

      if (warns.length === 0) {
        return interaction.reply({
          content: `✅ **${target.tag}** n'a aucun avertissement.`,
          ephemeral: true,
        });
      }

      const embed = new EmbedBuilder()
        .setTitle(`⚠️ Avertissements de ${target.tag}`)
        .setColor(COLORS.YELLOW)
        .setThumbnail(target.displayAvatarURL({ dynamic: true }))
        .setDescription(
          warns.map((w, i) =>
            `**#${w.id}** — <t:${Math.floor(new Date(w.created_at).getTime() / 1000)}:R>\n` +
            `> 📝 ${w.reason}\n> 🛡️ <@${w.moderator_id}>`
          ).join('\n\n')
        )
        .setFooter({ text: `Total: ${warns.length} avertissement(s)` })
        .setTimestamp();

      await interaction.reply({ embeds: [embed] });

    } else if (subcommand === 'remove') {
      const warnId = interaction.options.getInteger('id');
      const result = removeWarn(warnId, interaction.guild.id);

      if (result.changes === 0) {
        return interaction.reply(errorReply('❌ Avertissement introuvable avec cet ID.'));
      }

      await interaction.reply({
        content: `✅ Avertissement **#${warnId}** supprimé.`,
        ephemeral: true,
      });

    } else if (subcommand === 'clear') {
      const target = interaction.options.getUser('utilisateur');
      const result = clearWarns(interaction.guild.id, target.id);

      await interaction.reply({
        content: `✅ **${result.changes}** avertissement(s) supprimé(s) pour **${target.tag}**.`,
        ephemeral: true,
      });
    }
  },
};
