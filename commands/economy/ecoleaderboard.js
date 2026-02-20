// ===================================
// Ultra Suite — /richest
// Classement des plus riches du serveur
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'economy',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('richest')
    .setDescription('Classement des plus riches du serveur')
    .addIntegerOption((opt) => opt.setName('page').setDescription('Page').setMinValue(1)),

  async execute(interaction) {
    const page = (interaction.options.getInteger('page') || 1) - 1;
    const guildId = interaction.guildId;
    const db = getDb();
    const config = await configService.get(guildId);
    const symbol = config.economy?.currencySymbol || '🪙';
    const PAGE_SIZE = 10;

    const total = await db('users').where('guild_id', guildId).where('balance', '>', 0).count('id as count').first();
    const totalUsers = total?.count || 0;
    const totalPages = Math.max(1, Math.ceil(totalUsers / PAGE_SIZE));
    const safePage = Math.min(page, totalPages - 1);

    const users = await db('users')
      .where('guild_id', guildId).where('balance', '>', 0)
      .orderBy('balance', 'desc')
      .limit(PAGE_SIZE).offset(safePage * PAGE_SIZE);

    if (users.length === 0) {
      return interaction.reply({ content: '❌ Aucun membre avec de l\'argent.', ephemeral: true });
    }

    const medals = ['🥇', '🥈', '🥉'];
    const lines = await Promise.all(users.map(async (u, i) => {
      const pos = safePage * PAGE_SIZE + i + 1;
      const icon = pos <= 3 ? medals[pos - 1] : `**${pos}.**`;
      let name;
      try {
        const m = await interaction.guild.members.fetch(u.user_id).catch(() => null);
        name = m?.user?.username || `<@${u.user_id}>`;
      } catch { name = `<@${u.user_id}>`; }
      return `${icon} **${name}** — ${(u.balance || 0).toLocaleString('fr-FR')} ${symbol}`;
    }));

    // Position de l'utilisateur courant
    let userPos = null;
    const userRow = await db('users').where('guild_id', guildId).where('user_id', interaction.user.id).first();
    if (userRow) {
      const above = await db('users').where('guild_id', guildId).where('balance', '>', userRow.balance || 0).count('id as count').first();
      userPos = (above?.count || 0) + 1;
    }

    const embed = new EmbedBuilder()
      .setTitle(`💰 Classement Économie`)
      .setDescription(lines.join('\n'))
      .setColor(0xFEE75C)
      .setFooter({ text: `Page ${safePage + 1}/${totalPages} • ${totalUsers} membre(s)${userPos ? ` • Votre rang : #${userPos}` : ''}` })
      .setTimestamp();

    return interaction.reply({ embeds: [embed] });
  },
};