const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { addWarn, getWarns, addModLog } = require('../../utils/database');
const { modLog, COLORS } = require('../../utils/logger');
const { canModerate, errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('warn')
    .setDescription('⚠️ Avertir un utilisateur')
    .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur à avertir').setRequired(true))
    .addStringOption(opt => opt.setName('raison').setDescription('Raison de l\'avertissement').setRequired(true))
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers),

  async execute(interaction) {
    const target = interaction.options.getUser('utilisateur');
    const reason = interaction.options.getString('raison');

    const check = canModerate(interaction, target);
    if (!check.ok) return interaction.reply(errorReply(check.reason));

    try {
      // Ajouter le warn
      addWarn(interaction.guild.id, target.id, interaction.user.id, reason);

      // Compter les warns
      const warns = getWarns(interaction.guild.id, target.id);
      const warnCount = warns.length;

      // Log en base de données
      addModLog(interaction.guild.id, 'WARN', target.id, interaction.user.id, reason);

      // Log dans le salon
      await modLog(interaction.guild, {
        action: `Avertissement (#${warnCount})`,
        moderator: interaction.user,
        target,
        reason,
        color: COLORS.YELLOW,
      });

      // DM à l'utilisateur
      try {
        const dmEmbed = new EmbedBuilder()
          .setTitle('⚠️ Vous avez reçu un avertissement')
          .setColor(COLORS.YELLOW)
          .addFields(
            { name: 'Serveur', value: interaction.guild.name },
            { name: 'Raison', value: reason },
            { name: 'Total d\'avertissements', value: `${warnCount}` },
          )
          .setTimestamp();
        await target.send({ embeds: [dmEmbed] });
      } catch { /* DMs fermés */ }

      // Réponse
      const embed = new EmbedBuilder()
        .setTitle('⚠️ Utilisateur averti')
        .setColor(COLORS.YELLOW)
        .setDescription(`**${target.tag}** a reçu un avertissement.`)
        .addFields(
          { name: '📝 Raison', value: reason },
          { name: '📊 Total', value: `${warnCount} avertissement(s)`, inline: true },
        )
        .setTimestamp();

      // Alerte si beaucoup de warns
      if (warnCount >= 3) {
        embed.addFields({
          name: '🚨 Attention',
          value: `Cet utilisateur a **${warnCount}** avertissements !`,
        });
      }

      await interaction.reply({ embeds: [embed] });
    } catch (error) {
      console.error('[WARN]', error);
      await interaction.reply(errorReply('❌ Impossible d\'avertir cet utilisateur.'));
    }
  },
};
