// ===================================
// Ultra Suite — /weekly
// Récompense hebdomadaire
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'economy',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('weekly')
    .setDescription('Récupérer votre récompense hebdomadaire'),

  async execute(interaction) {
    const guildId = interaction.guildId;
    const userId = interaction.user.id;
    const db = getDb();
    const config = await configService.get(guildId);
    const eco = config.economy || {};
    const symbol = eco.currencySymbol || '🪙';
    const weeklyAmount = eco.weeklyAmount || 1000;
    const cooldownMs = 7 * 24 * 60 * 60 * 1000; // 7 days

    let user = await db('users').where({ guild_id: guildId, user_id: userId }).first();
    if (!user) {
      await db('users').insert({ guild_id: guildId, user_id: userId, balance: 0 });
      user = { balance: 0, last_weekly: null };
    }

    const now = Date.now();
    const lastWeekly = user.last_weekly ? new Date(user.last_weekly).getTime() : 0;
    const remaining = cooldownMs - (now - lastWeekly);

    if (remaining > 0) {
      const days = Math.floor(remaining / 86400000);
      const hours = Math.floor((remaining % 86400000) / 3600000);
      const mins = Math.floor((remaining % 3600000) / 60000);
      return interaction.reply({
        content: `⏳ Vous avez déjà récupéré votre récompense hebdomadaire. Revenez dans **${days}j ${hours}h ${mins}m**.`,
        ephemeral: true,
      });
    }

    // Bonus streak (weekly)
    const streak = (user.weekly_streak || 0) + 1;
    const streakBonus = Math.min(streak * 50, 500);
    const total = weeklyAmount + streakBonus;

    await db('users').where({ guild_id: guildId, user_id: userId }).update({
      balance: db.raw('balance + ?', [total]),
      last_weekly: new Date(),
      weekly_streak: streak,
      total_earned: db.raw('COALESCE(total_earned, 0) + ?', [total]),
    });

    await db('transactions').insert({
      guild_id: guildId,
      from_id: 'SYSTEM',
      to_id: userId,
      amount: total,
      type: 'weekly',
      note: `Weekly #${streak}`,
    });

    const embed = new EmbedBuilder()
      .setColor(0x2ECC71)
      .setTitle('📅 Récompense hebdomadaire')
      .setDescription([
        `Vous avez reçu **${weeklyAmount.toLocaleString('fr-FR')}** ${symbol}`,
        streakBonus > 0 ? `🔥 Bonus streak (x${streak}) : **+${streakBonus}** ${symbol}` : '',
        `\n💰 Total reçu : **${total.toLocaleString('fr-FR')}** ${symbol}`,
      ].filter(Boolean).join('\n'))
      .setFooter({ text: `Streak : ${streak} semaine(s)` })
      .setTimestamp();

    return interaction.reply({ embeds: [embed] });
  },
};
