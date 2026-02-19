const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { addModLog } = require('../../utils/database');
const { modLog, COLORS } = require('../../utils/logger');
const { errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('unban')
    .setDescription('🔓 Débannir un utilisateur')
    .addStringOption(opt =>
      opt.setName('utilisateur_id')
        .setDescription('L\'ID de l\'utilisateur à débannir')
        .setRequired(true)
    )
    .addStringOption(opt => opt.setName('raison').setDescription('Raison du débannissement'))
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers),

  async execute(interaction) {
    const userId = interaction.options.getString('utilisateur_id');
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';

    // Vérifier que l'ID est valide
    if (!/^\d{17,20}$/.test(userId)) {
      return interaction.reply(errorReply('❌ ID invalide. Un ID Discord est composé de 17 à 20 chiffres.'));
    }

    try {
      // Vérifier que l'utilisateur est bien banni
      const banList = await interaction.guild.bans.fetch();
      const bannedUser = banList.get(userId);

      if (!bannedUser) {
        return interaction.reply(errorReply('❌ Cet utilisateur n\'est pas banni.'));
      }

      // Débannir
      await interaction.guild.members.unban(userId, `${interaction.user.tag}: ${reason}`);

      const target = bannedUser.user;

      addModLog(interaction.guild.id, 'UNBAN', userId, interaction.user.id, reason);

      await modLog(interaction.guild, {
        action: 'Débannissement',
        moderator: interaction.user,
        target,
        reason,
        color: COLORS.GREEN,
      });

      const embed = new EmbedBuilder()
        .setTitle('🔓 Utilisateur débanni')
        .setColor(COLORS.GREEN)
        .setDescription(`**${target.tag}** a été débanni du serveur.`)
        .addFields({ name: '📝 Raison', value: reason })
        .setThumbnail(target.displayAvatarURL({ dynamic: true }))
        .setTimestamp();

      await interaction.reply({ embeds: [embed] });
    } catch (error) {
      console.error('[UNBAN]', error);
      await interaction.reply(errorReply('❌ Impossible de débannir cet utilisateur.'));
    }
  },
};
