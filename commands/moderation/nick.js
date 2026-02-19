const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { addModLog } = require('../../utils/database');
const { modLog, COLORS } = require('../../utils/logger');
const { errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('nick')
    .setDescription('📝 Modifier le surnom d\'un utilisateur')
    .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur').setRequired(true))
    .addStringOption(opt =>
      opt.setName('surnom')
        .setDescription('Le nouveau surnom (vide pour réinitialiser)')
    )
    .addStringOption(opt => opt.setName('raison').setDescription('Raison du changement'))
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageNicknames),

  async execute(interaction) {
    const target = interaction.options.getUser('utilisateur');
    const nickname = interaction.options.getString('surnom') || null;
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';

    const member = interaction.guild.members.cache.get(target.id);
    if (!member) return interaction.reply(errorReply('❌ Cet utilisateur n\'est pas sur le serveur.'));

    // Vérifier la hiérarchie
    if (member.roles.highest.position >= interaction.guild.members.me.roles.highest.position) {
      return interaction.reply(errorReply('❌ Je ne peux pas modifier le surnom de cet utilisateur (rôle trop élevé).'));
    }

    try {
      const oldNick = member.nickname || member.user.username;
      await member.setNickname(nickname, `${interaction.user.tag}: ${reason}`);
      const newNick = nickname || member.user.username;

      addModLog(interaction.guild.id, 'NICKNAME', target.id, interaction.user.id, `${oldNick} → ${newNick}`);

      const embed = new EmbedBuilder()
        .setTitle('📝 Surnom modifié')
        .setColor(COLORS.BLUE)
        .addFields(
          { name: '👤 Utilisateur', value: `${target}`, inline: true },
          { name: '📛 Ancien', value: oldNick, inline: true },
          { name: '📛 Nouveau', value: newNick, inline: true },
          { name: '📝 Raison', value: reason },
        )
        .setTimestamp();

      await interaction.reply({ embeds: [embed] });
    } catch (error) {
      console.error('[NICK]', error);
      await interaction.reply(errorReply('❌ Impossible de modifier le surnom.'));
    }
  },
};
