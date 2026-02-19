// ===================================
// Ultra Suite — Economy: /daily
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const userQueries = require('../../database/userQueries');
const configService = require('../../core/configService');
const { getDb } = require('../../database');
const { successEmbed, errorEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');
const { formatDuration } = require('../../utils/formatters');

module.exports = {
  module: 'economy',
  cooldown: 5,
  data: new SlashCommandBuilder()
    .setName('daily')
    .setDescription('Récupère votre récompense quotidienne'),

  async execute(interaction) {
    const config = await configService.get(interaction.guild.id);
    const amount = config.economy?.dailyAmount || 100;
    const symbol = config.economy?.currencySymbol || '$';

    const dbUser = await userQueries.getOrCreate(interaction.user.id, interaction.guild.id);

    // Vérifier cooldown (24h)
    if (dbUser.last_daily) {
      const lastClaim = new Date(dbUser.last_daily).getTime();
      const nextClaim = lastClaim + 86400000;
      if (Date.now() < nextClaim) {
        const remaining = formatDuration(Math.ceil((nextClaim - Date.now()) / 1000));
        return interaction.reply({
          embeds: [errorEmbed(t('economy.daily_cooldown', undefined, { remaining }))],
          ephemeral: true,
        });
      }
    }

    await userQueries.addBalance(interaction.user.id, interaction.guild.id, amount, 'balance');
    await userQueries.update(interaction.user.id, interaction.guild.id, {
      last_daily: new Date().toISOString(),
    });

    // Transaction
    const db = getDb();
    await db('transactions').insert({
      guild_id: interaction.guild.id,
      to_id: interaction.user.id,
      amount,
      type: 'daily',
      reason: 'Récompense quotidienne',
    });

    await interaction.reply({
      embeds: [successEmbed(t('economy.daily', undefined, { amount, symbol }))],
    });
  },
};
