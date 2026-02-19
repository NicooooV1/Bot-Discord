// ===================================
// Ultra Suite — Economy: /pay
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const userQueries = require('../../database/userQueries');
const configService = require('../../core/configService');
const { getDb } = require('../../database');
const { successEmbed, errorEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'economy',
  cooldown: 5,
  data: new SlashCommandBuilder()
    .setName('pay')
    .setDescription('Envoie de l\'argent à un membre')
    .addUserOption((opt) => opt.setName('membre').setDescription('Destinataire').setRequired(true))
    .addIntegerOption((opt) =>
      opt.setName('montant').setDescription('Montant à envoyer').setMinValue(1).setRequired(true)
    ),

  async execute(interaction) {
    const target = interaction.options.getUser('membre');
    const amount = interaction.options.getInteger('montant');
    const config = await configService.get(interaction.guild.id);
    const symbol = config.economy?.currencySymbol || '$';

    if (target.id === interaction.user.id) {
      return interaction.reply({ embeds: [errorEmbed(t('common.self_action'))], ephemeral: true });
    }

    const sender = await userQueries.getOrCreate(interaction.user.id, interaction.guild.id);
    if (sender.balance < amount) {
      return interaction.reply({ embeds: [errorEmbed(t('economy.insufficient'))], ephemeral: true });
    }

    await userQueries.addBalance(interaction.user.id, interaction.guild.id, -amount, 'balance');
    await userQueries.addBalance(target.id, interaction.guild.id, amount, 'balance');

    const db = getDb();
    await db('transactions').insert({
      guild_id: interaction.guild.id,
      from_id: interaction.user.id,
      to_id: target.id,
      amount,
      type: 'transfer',
      reason: `Transfert de ${interaction.user.tag} à ${target.tag}`,
    });

    await interaction.reply({
      embeds: [successEmbed(t('economy.transfer_success', undefined, { amount, symbol, user: target.tag }))],
    });
  },
};
