// ===================================
// Ultra Suite — Economy: /deposit & /withdraw
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const userQueries = require('../../database/userQueries');
const configService = require('../../core/configService');
const { successEmbed, errorEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'economy',
  data: new SlashCommandBuilder()
    .setName('bank')
    .setDescription('Déposer ou retirer de la banque')
    .addSubcommand((sub) =>
      sub
        .setName('deposit')
        .setDescription('Déposer en banque')
        .addIntegerOption((opt) => opt.setName('montant').setDescription('Montant').setMinValue(1).setRequired(true))
    )
    .addSubcommand((sub) =>
      sub
        .setName('withdraw')
        .setDescription('Retirer de la banque')
        .addIntegerOption((opt) => opt.setName('montant').setDescription('Montant').setMinValue(1).setRequired(true))
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const amount = interaction.options.getInteger('montant');
    const config = await configService.get(interaction.guild.id);
    const symbol = config.economy?.currencySymbol || '$';

    const direction = sub === 'deposit' ? 'deposit' : 'withdraw';
    const result = await userQueries.transfer(interaction.user.id, interaction.guild.id, amount, direction);

    if (!result.success) {
      return interaction.reply({ embeds: [errorEmbed(t('economy.insufficient'))], ephemeral: true });
    }

    const key = direction === 'deposit' ? 'economy.deposit_success' : 'economy.withdraw_success';
    await interaction.reply({ embeds: [successEmbed(t(key, undefined, { amount, symbol }))] });
  },
};
