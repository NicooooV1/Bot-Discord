const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { addModLog } = require('../../utils/database');
const { modLog, COLORS } = require('../../utils/logger');
const { canModerate, errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('kick')
    .setDescription('👢 Expulser un utilisateur du serveur')
    .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur à expulser').setRequired(true))
    .addStringOption(opt => opt.setName('raison').setDescription('Raison de l\'expulsion'))
    .setDefaultMemberPermissions(PermissionFlagsBits.KickMembers),

  async execute(interaction) {
    const target = interaction.options.getUser('utilisateur');
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';

    const member = interaction.guild.members.cache.get(target.id);
    if (!member) return interaction.reply(errorReply('❌ Cet utilisateur n\'est pas sur le serveur.'));

    const check = canModerate(interaction, target);
    if (!check.ok) return interaction.reply(errorReply(check.reason));

    try {
      try {
        const dmEmbed = new EmbedBuilder()
          .setTitle('👢 Vous avez été expulsé')
          .setColor(COLORS.ORANGE)
          .addFields(
            { name: 'Serveur', value: interaction.guild.name },
            { name: 'Raison', value: reason },
            { name: 'Modérateur', value: interaction.user.tag },
          )
          .setTimestamp();
        await target.send({ embeds: [dmEmbed] });
      } catch { /* DMs fermés */ }

      await member.kick(`${interaction.user.tag}: ${reason}`);

      addModLog(interaction.guild.id, 'KICK', target.id, interaction.user.id, reason);

      await modLog(interaction.guild, {
        action: 'Expulsion',
        moderator: interaction.user,
        target,
        reason,
        color: COLORS.ORANGE,
      });

      const embed = new EmbedBuilder()
        .setTitle('👢 Utilisateur expulsé')
        .setColor(COLORS.ORANGE)
        .setDescription(`**${target.tag}** a été expulsé du serveur.`)
        .addFields({ name: '📝 Raison', value: reason })
        .setTimestamp();

      await interaction.reply({ embeds: [embed] });
    } catch (error) {
      console.error('[KICK]', error);
      await interaction.reply(errorReply('❌ Impossible d\'expulser cet utilisateur.'));
    }
  },
};
