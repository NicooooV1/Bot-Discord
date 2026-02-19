// ===================================
// Ultra Suite — Moderation: /nick
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const { successEmbed, errorEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'moderation',
  data: new SlashCommandBuilder()
    .setName('nick')
    .setDescription('Change le pseudo d\'un membre')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageNicknames)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre').setRequired(true))
    .addStringOption((opt) => opt.setName('pseudo').setDescription('Nouveau pseudo (vide = reset)')),

  async execute(interaction) {
    const target = interaction.options.getMember('membre');
    const nick = interaction.options.getString('pseudo');

    if (!target) {
      return interaction.reply({ embeds: [errorEmbed(t('common.invalid_user'))], ephemeral: true });
    }

    try {
      await target.setNickname(nick || null, `Par ${interaction.user.tag}`);
    } catch {
      return interaction.reply({ embeds: [errorEmbed(t('common.bot_no_permission'))], ephemeral: true });
    }

    const key = nick ? 'mod.nick.success' : 'mod.nick.reset';
    await interaction.reply({
      embeds: [successEmbed(t(key, undefined, { user: target.user.tag, nick }))],
      ephemeral: true,
    });
  },
};
