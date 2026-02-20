// ===================================
// Ultra Suite — /balance
// Voir son solde ou celui d'un membre
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'economy',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('balance')
    .setDescription('Voir votre solde ou celui d\'un membre')
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à consulter')),

  async execute(interaction) {
    const target = interaction.options.getUser('membre') || interaction.user;
    const guildId = interaction.guildId;
    const db = getDb();
    const config = await configService.get(guildId);
    const eco = config.economy || {};
    const symbol = eco.currencySymbol || '🪙';
    const name = eco.currencyName || 'pièces';

    const user = await db('users').where('guild_id', guildId).where('user_id', target.id).first();

    if (!user) {
      return interaction.reply({
        content: target.id === interaction.user.id
          ? `💰 Vous n'avez pas encore de compte. Utilisez \`/daily\` pour commencer !`
          : `💰 **${target.username}** n'a pas encore de compte.`,
        ephemeral: true,
      });
    }

    // Classement
    const rank = await db('users')
      .where('guild_id', guildId)
      .where('balance', '>', user.balance || 0)
      .count('id as count')
      .first();
    const position = (rank?.count || 0) + 1;

    // Total gagné (transactions entrantes)
    const earned = await db('transactions')
      .where('guild_id', guildId)
      .where('to_id', target.id)
      .sum('amount as total')
      .first();

    // Total dépensé (transactions sortantes)
    const spent = await db('transactions')
      .where('guild_id', guildId)
      .where('from_id', target.id)
      .sum('amount as total')
      .first();

    const embed = new EmbedBuilder()
      .setAuthor({ name: target.username, iconURL: target.displayAvatarURL() })
      .setColor(0xFEE75C)
      .addFields(
        { name: `💰 Solde`, value: `**${(user.balance || 0).toLocaleString('fr-FR')}** ${symbol}`, inline: true },
        { name: '🏆 Rang', value: `#${position}`, inline: true },
        { name: `🔥 Streak`, value: `${user.daily_streak || 0} jour(s)`, inline: true },
        { name: '📈 Total gagné', value: `${(earned?.total || 0).toLocaleString('fr-FR')} ${symbol}`, inline: true },
        { name: '📉 Total dépensé', value: `${(spent?.total || 0).toLocaleString('fr-FR')} ${symbol}`, inline: true },
      )
      .setTimestamp();

    return interaction.reply({ embeds: [embed] });
  },
};