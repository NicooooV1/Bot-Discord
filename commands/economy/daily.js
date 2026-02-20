// ===================================
// Ultra Suite — /daily
// Récupérer sa récompense quotidienne avec streak
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { t } = require('../../core/i18n');
const { getDb } = require('../../database');

module.exports = {
  module: 'economy',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('daily')
    .setDescription('Récupérer votre récompense quotidienne'),

  async execute(interaction) {
    const guildId = interaction.guildId;
    const userId = interaction.user.id;
    const db = getDb();
    const config = await configService.get(guildId);
    const eco = config.economy || {};

    const baseAmount = eco.dailyAmount || 100;
    const currencyName = eco.currencyName || 'pièces';
    const currencySymbol = eco.currencySymbol || '🪙';

    // Récupérer ou créer le profil
    let user = await db('users').where('guild_id', guildId).where('user_id', userId).first();
    if (!user) {
      await db('users').insert({ guild_id: guildId, user_id: userId, balance: 0 });
      user = { balance: 0, daily_streak: 0, last_daily: null };
    }

    const now = new Date();
    const todayStr = now.toISOString().slice(0, 10);

    // Vérifier si déjà récupéré aujourd'hui
    if (user.last_daily) {
      const lastDaily = new Date(user.last_daily);
      const lastDailyStr = lastDaily.toISOString().slice(0, 10);

      if (lastDailyStr === todayStr) {
        // Calculer le temps restant jusqu'à minuit
        const tomorrow = new Date(now);
        tomorrow.setDate(tomorrow.getDate() + 1);
        tomorrow.setHours(0, 0, 0, 0);
        const msRemaining = tomorrow - now;
        const hoursRemaining = Math.floor(msRemaining / 3600000);
        const minutesRemaining = Math.floor((msRemaining % 3600000) / 60000);

        const timeStr = hoursRemaining > 0
          ? `${hoursRemaining}h ${minutesRemaining}min`
          : `${minutesRemaining}min`;

        const msg = await t(guildId, 'economy.daily_cooldown', { time: timeStr });
        return interaction.reply({ content: `⏰ ${msg}`, ephemeral: true });
      }
    }

    // Streak : consécutif si le dernier daily était hier
    let streak = user.daily_streak || 0;
    if (user.last_daily) {
      const lastDaily = new Date(user.last_daily);
      const yesterday = new Date(now);
      yesterday.setDate(yesterday.getDate() - 1);

      if (lastDaily.toISOString().slice(0, 10) === yesterday.toISOString().slice(0, 10)) {
        streak += 1; // Consécutif
      } else {
        streak = 1; // Reset
      }
    } else {
      streak = 1;
    }

    // Bonus streak (10% par jour, max 100%)
    const streakBonus = Math.min(streak - 1, 10) * 0.1;
    const bonusAmount = Math.floor(baseAmount * streakBonus);
    const totalAmount = baseAmount + bonusAmount;

    // Mettre à jour
    const newBalance = (user.balance || 0) + totalAmount;
    await db('users')
      .where('guild_id', guildId)
      .where('user_id', userId)
      .update({
        balance: newBalance,
        last_daily: now,
        daily_streak: streak,
      });

    // Transaction
    await db('transactions').insert({
      guild_id: guildId,
      from_id: null,
      to_id: userId,
      amount: totalAmount,
      type: 'DAILY',
      description: `Récompense quotidienne (streak: ${streak})`,
    });

    // Réponse
    const msg = await t(guildId, 'economy.daily_claimed', {
      amount: totalAmount.toLocaleString('fr-FR'),
      currency: currencySymbol,
      balance: newBalance.toLocaleString('fr-FR'),
    });

    const embed = new EmbedBuilder()
      .setDescription(`${currencySymbol} ${msg}`)
      .setColor(0x57F287)
      .setTimestamp();

    if (bonusAmount > 0) {
      embed.addFields({
        name: `🔥 Streak x${streak}`,
        value: `+${bonusAmount.toLocaleString('fr-FR')} ${currencySymbol} bonus`,
        inline: true,
      });
    }

    embed.addFields({ name: '💰 Solde', value: `${newBalance.toLocaleString('fr-FR')} ${currencySymbol}`, inline: true });

    return interaction.reply({ embeds: [embed] });
  },
};