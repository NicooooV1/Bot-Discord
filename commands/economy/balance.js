// ===================================
// Ultra Suite — Economy: /balance
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const userQueries = require('../../database/userQueries');
const configService = require('../../core/configService');
const { createEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'economy',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('balance')
    .setDescription('Affiche votre solde ou celui d\'un membre')
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à consulter')),

  async execute(interaction) {
    const user = interaction.options.getUser('membre') || interaction.user;
    const dbUser = await userQueries.getOrCreate(user.id, interaction.guild.id);
    const config = await configService.get(interaction.guild.id);
    const symbol = config.economy?.currencySymbol || '$';

    const embed = createEmbed('primary')
      .setTitle(`💰 Solde de ${user.username}`)
      .setThumbnail(user.displayAvatarURL({ size: 256 }))
      .addFields(
        { name: '👛 Portefeuille', value: `**${dbUser.balance}** ${symbol}`, inline: true },
        { name: '🏦 Banque', value: `**${dbUser.bank}** ${symbol}`, inline: true },
        { name: '📊 Total', value: `**${dbUser.balance + dbUser.bank}** ${symbol}`, inline: true }
      );

    await interaction.reply({ embeds: [embed] });
  },
};
