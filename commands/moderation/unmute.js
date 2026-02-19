const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { addModLog } = require('../../utils/database');
const { modLog, COLORS } = require('../../utils/logger');
const { errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('unmute')
    .setDescription('🔊 Retirer le mute d\'un utilisateur')
    .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur à unmute').setRequired(true))
    .addStringOption(opt => opt.setName('raison').setDescription('Raison du unmute'))
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers),

  async execute(interaction) {
    const target = interaction.options.getUser('utilisateur');
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';

    const member = interaction.guild.members.cache.get(target.id);
    if (!member) return interaction.reply(errorReply('❌ Cet utilisateur n\'est pas sur le serveur.'));

    if (!member.isCommunicationDisabled()) {
      return interaction.reply(errorReply('❌ Cet utilisateur n\'est pas muté.'));
    }

    try {
      await member.timeout(null, `${interaction.user.tag}: ${reason}`);

      addModLog(interaction.guild.id, 'UNMUTE', target.id, interaction.user.id, reason);

      await modLog(interaction.guild, {
        action: 'Unmute',
        moderator: interaction.user,
        target,
        reason,
        color: COLORS.GREEN,
      });

      const embed = new EmbedBuilder()
        .setTitle('🔊 Utilisateur unmuté')
        .setColor(COLORS.GREEN)
        .setDescription(`**${target.tag}** n'est plus muté.`)
        .addFields({ name: '📝 Raison', value: reason })
        .setTimestamp();

      await interaction.reply({ embeds: [embed] });
    } catch (error) {
      console.error('[UNMUTE]', error);
      await interaction.reply(errorReply('❌ Impossible d\'unmute cet utilisateur.'));
    }
  },
};
