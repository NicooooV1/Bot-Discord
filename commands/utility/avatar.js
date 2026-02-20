// ===================================
// Ultra Suite — /avatar
// Afficher l'avatar d'un membre en grand
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

module.exports = {
  module: 'utility',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('avatar')
    .setDescription('Afficher l\'avatar d\'un membre')
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à consulter'))
    .addBooleanOption((opt) => opt.setName('serveur').setDescription('Afficher l\'avatar serveur au lieu du global')),

  async execute(interaction) {
    const target = interaction.options.getUser('membre') || interaction.user;
    const useServer = interaction.options.getBoolean('serveur') || false;

    let avatarUrl;
    let title;

    if (useServer) {
      const member = await interaction.guild.members.fetch(target.id).catch(() => null);
      if (member?.avatar) {
        avatarUrl = member.displayAvatarURL({ size: 4096, dynamic: true });
        title = `Avatar serveur — ${target.username}`;
      } else {
        avatarUrl = target.displayAvatarURL({ size: 4096, dynamic: true });
        title = `Avatar — ${target.username} (pas d'avatar serveur)`;
      }
    } else {
      avatarUrl = target.displayAvatarURL({ size: 4096, dynamic: true });
      title = `Avatar — ${target.username}`;
    }

    const embed = new EmbedBuilder()
      .setTitle(title)
      .setImage(avatarUrl)
      .setColor(0x5865F2)
      .setDescription(
        `[PNG](${target.displayAvatarURL({ size: 4096, extension: 'png' })}) • ` +
        `[JPG](${target.displayAvatarURL({ size: 4096, extension: 'jpg' })}) • ` +
        `[WEBP](${target.displayAvatarURL({ size: 4096, extension: 'webp' })})`
      );

    return interaction.reply({ embeds: [embed] });
  },
};