const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { addModLog } = require('../../utils/database');
const { modLog, COLORS } = require('../../utils/logger');
const { canModerate, errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('softban')
    .setDescription('🧹 Softban — Bannir puis débannir pour supprimer les messages')
    .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur à softban').setRequired(true))
    .addStringOption(opt => opt.setName('raison').setDescription('Raison du softban'))
    .addIntegerOption(opt =>
      opt.setName('jours')
        .setDescription('Messages à supprimer (jours, défaut: 7)')
        .setMinValue(1)
        .setMaxValue(7)
    )
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers),

  async execute(interaction) {
    const target = interaction.options.getUser('utilisateur');
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';
    const days = interaction.options.getInteger('jours') || 7;

    const check = canModerate(interaction, target);
    if (!check.ok) return interaction.reply(errorReply(check.reason));

    try {
      // DM à l'utilisateur
      try {
        const dmEmbed = new EmbedBuilder()
          .setTitle('🧹 Vous avez été softban')
          .setColor(COLORS.ORANGE)
          .setDescription('Vous avez été expulsé et vos messages récents ont été supprimés. Vous pouvez rejoindre le serveur à nouveau.')
          .addFields(
            { name: 'Serveur', value: interaction.guild.name },
            { name: 'Raison', value: reason },
          )
          .setTimestamp();
        await target.send({ embeds: [dmEmbed] });
      } catch { /* DMs fermés */ }

      // Ban puis unban
      await interaction.guild.members.ban(target, {
        reason: `[SOFTBAN] ${interaction.user.tag}: ${reason}`,
        deleteMessageSeconds: days * 86400,
      });
      await interaction.guild.members.unban(target, `[SOFTBAN] ${interaction.user.tag}: ${reason}`);

      addModLog(interaction.guild.id, 'SOFTBAN', target.id, interaction.user.id, reason, `${days}j de messages supprimés`);

      await modLog(interaction.guild, {
        action: 'Softban',
        moderator: interaction.user,
        target,
        reason,
        duration: `${days}j de messages supprimés`,
        color: COLORS.ORANGE,
      });

      const embed = new EmbedBuilder()
        .setTitle('🧹 Utilisateur softban')
        .setColor(COLORS.ORANGE)
        .setDescription(`**${target.tag}** a été softban (expulsé + messages supprimés).`)
        .addFields(
          { name: '📝 Raison', value: reason },
          { name: '🗑️ Messages supprimés', value: `${days} jour(s)`, inline: true },
        )
        .setTimestamp();

      await interaction.reply({ embeds: [embed] });
    } catch (error) {
      console.error('[SOFTBAN]', error);
      await interaction.reply(errorReply('❌ Impossible de softban cet utilisateur.'));
    }
  },
};
