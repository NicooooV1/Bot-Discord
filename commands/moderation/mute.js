const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { addModLog } = require('../../utils/database');
const { modLog, COLORS } = require('../../utils/logger');
const { canModerate, parseDuration, formatDuration, errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('mute')
    .setDescription('🔇 Rendre muet un utilisateur (timeout)')
    .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur à mute').setRequired(true))
    .addStringOption(opt => opt.setName('durée').setDescription('Durée du mute (ex: 10m, 1h, 1d, 1w)').setRequired(true))
    .addStringOption(opt => opt.setName('raison').setDescription('Raison du mute'))
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers),

  async execute(interaction) {
    const target = interaction.options.getUser('utilisateur');
    const durationStr = interaction.options.getString('durée');
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';

    const member = interaction.guild.members.cache.get(target.id);
    if (!member) return interaction.reply(errorReply('❌ Cet utilisateur n\'est pas sur le serveur.'));

    const check = canModerate(interaction, target);
    if (!check.ok) return interaction.reply(errorReply(check.reason));

    const duration = parseDuration(durationStr);
    if (!duration) return interaction.reply(errorReply('❌ Durée invalide. Utilisez un format comme: `10m`, `1h`, `1d`, `1w`'));

    // Maximum 28 jours (limitation Discord)
    const maxDuration = 28 * 24 * 60 * 60 * 1000;
    if (duration > maxDuration) return interaction.reply(errorReply('❌ La durée maximale est de 28 jours.'));

    try {
      await member.timeout(duration, `${interaction.user.tag}: ${reason}`);

      const formattedDuration = formatDuration(duration);

      addModLog(interaction.guild.id, 'MUTE', target.id, interaction.user.id, reason, formattedDuration);

      await modLog(interaction.guild, {
        action: 'Mute (Timeout)',
        moderator: interaction.user,
        target,
        reason,
        duration: formattedDuration,
        color: COLORS.YELLOW,
      });

      try {
        const dmEmbed = new EmbedBuilder()
          .setTitle('🔇 Vous avez été rendu muet')
          .setColor(COLORS.YELLOW)
          .addFields(
            { name: 'Serveur', value: interaction.guild.name },
            { name: 'Durée', value: formattedDuration },
            { name: 'Raison', value: reason },
          )
          .setTimestamp();
        await target.send({ embeds: [dmEmbed] });
      } catch { /* DMs fermés */ }

      const embed = new EmbedBuilder()
        .setTitle('🔇 Utilisateur muté')
        .setColor(COLORS.YELLOW)
        .setDescription(`**${target.tag}** a été rendu muet.`)
        .addFields(
          { name: '⏱️ Durée', value: formattedDuration, inline: true },
          { name: '📝 Raison', value: reason, inline: true },
        )
        .setTimestamp();

      await interaction.reply({ embeds: [embed] });
    } catch (error) {
      console.error('[MUTE]', error);
      await interaction.reply(errorReply('❌ Impossible de mute cet utilisateur.'));
    }
  },
};
