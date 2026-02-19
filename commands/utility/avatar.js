// ===================================
// Ultra Suite — Utility: /avatar
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const { createEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'utility',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('avatar')
    .setDescription('Affiche l\'avatar d\'un utilisateur')
    .addUserOption((opt) => opt.setName('user').setDescription('Utilisateur'))
    .addBooleanOption((opt) => opt.setName('server').setDescription('Avatar serveur ?')),

  async execute(interaction) {
    const user = interaction.options.getUser('user') || interaction.user;
    const serverAvatar = interaction.options.getBoolean('server');

    const member = await interaction.guild.members.fetch(user.id).catch(() => null);
    let avatarUrl;

    if (serverAvatar && member?.avatar) {
      avatarUrl = member.displayAvatarURL({ size: 1024 });
    } else {
      avatarUrl = user.displayAvatarURL({ size: 1024 });
    }

    const embed = createEmbed('primary')
      .setTitle(`Avatar de ${user.tag}`)
      .setImage(avatarUrl)
      .setDescription(
        `[PNG](${user.displayAvatarURL({ extension: 'png', size: 1024 })}) · ` +
          `[JPG](${user.displayAvatarURL({ extension: 'jpg', size: 1024 })}) · ` +
          `[WEBP](${user.displayAvatarURL({ extension: 'webp', size: 1024 })})`
      );

    return interaction.reply({ embeds: [embed] });
  },
};
