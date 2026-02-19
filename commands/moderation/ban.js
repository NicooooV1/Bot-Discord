const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { addModLog } = require('../../utils/database');
const { modLog, COLORS } = require('../../utils/logger');
const { canModerate, errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('ban')
    .setDescription('🔨 Bannir un utilisateur du serveur')
    .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur à bannir').setRequired(true))
    .addStringOption(opt => opt.setName('raison').setDescription('Raison du ban'))
    .addIntegerOption(opt => opt.setName('supprimer_messages').setDescription('Supprimer les messages des X derniers jours (0-7)').setMinValue(0).setMaxValue(7))
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers),

  async execute(interaction) {
    const target = interaction.options.getUser('utilisateur');
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';
    const deleteMessages = interaction.options.getInteger('supprimer_messages') || 0;

    // Vérifications
    const check = canModerate(interaction, target);
    if (!check.ok) return interaction.reply(errorReply(check.reason));

    try {
      // Envoyer un DM à l'utilisateur avant le ban
      try {
        const dmEmbed = new EmbedBuilder()
          .setTitle('🔨 Vous avez été banni')
          .setColor(COLORS.RED)
          .addFields(
            { name: 'Serveur', value: interaction.guild.name },
            { name: 'Raison', value: reason },
            { name: 'Modérateur', value: interaction.user.tag },
          )
          .setTimestamp();
        await target.send({ embeds: [dmEmbed] });
      } catch {
        // DMs fermés
      }

      // Bannir
      await interaction.guild.members.ban(target, {
        reason: `${interaction.user.tag}: ${reason}`,
        deleteMessageSeconds: deleteMessages * 86400,
      });

      // Log en base de données
      addModLog(interaction.guild.id, 'BAN', target.id, interaction.user.id, reason);

      // Log dans le salon de logs
      await modLog(interaction.guild, {
        action: 'Bannissement',
        moderator: interaction.user,
        target,
        reason,
        color: COLORS.RED,
      });

      // Réponse
      const embed = new EmbedBuilder()
        .setTitle('🔨 Utilisateur banni')
        .setColor(COLORS.RED)
        .setDescription(`**${target.tag}** a été banni du serveur.`)
        .addFields({ name: '📝 Raison', value: reason })
        .setTimestamp();

      await interaction.reply({ embeds: [embed] });
    } catch (error) {
      console.error('[BAN]', error);
      await interaction.reply(errorReply('❌ Impossible de bannir cet utilisateur.'));
    }
  },
};
