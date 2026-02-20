// ===================================
// Ultra Suite — /leaderboard
// Classement XP, messages ou vocal du serveur
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const { getDb } = require('../../database');

const PAGE_SIZE = 10;

module.exports = {
  module: 'xp',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('leaderboard')
    .setDescription('Voir le classement du serveur')
    .addStringOption((opt) =>
      opt.setName('type').setDescription('Type de classement')
        .addChoices(
          { name: '⭐ XP / Niveaux', value: 'xp' },
          { name: '💬 Messages', value: 'messages' },
          { name: '🎙️ Vocal', value: 'voice' },
        ))
    .addIntegerOption((opt) =>
      opt.setName('page').setDescription('Page du classement').setMinValue(1)),

  async execute(interaction) {
    const type = interaction.options.getString('type') || 'xp';
    const page = (interaction.options.getInteger('page') || 1) - 1; // 0-indexed
    const guildId = interaction.guildId;
    const db = getDb();

    // Colonne de tri
    const sortColumn = type === 'messages' ? 'total_messages'
      : type === 'voice' ? 'voice_minutes' : 'xp';
    const typeLabel = type === 'messages' ? '💬 Messages'
      : type === 'voice' ? '🎙️ Vocal' : '⭐ XP';

    // Total d'utilisateurs
    const total = await db('users').where('guild_id', guildId)
      .where(sortColumn, '>', 0).count('id as count').first();
    const totalUsers = total?.count || 0;
    const totalPages = Math.max(1, Math.ceil(totalUsers / PAGE_SIZE));
    const safePage = Math.min(page, totalPages - 1);

    // Récupérer la page
    const users = await db('users')
      .where('guild_id', guildId)
      .where(sortColumn, '>', 0)
      .orderBy(sortColumn, 'desc')
      .limit(PAGE_SIZE)
      .offset(safePage * PAGE_SIZE);

    if (users.length === 0) {
      return interaction.reply({
        content: '❌ Aucune donnée pour ce classement. Les membres doivent commencer à participer !',
        ephemeral: true,
      });
    }

    // Construire le classement
    const medals = ['🥇', '🥈', '🥉'];
    const lines = await Promise.all(users.map(async (u, i) => {
      const pos = safePage * PAGE_SIZE + i + 1;
      const icon = pos <= 3 ? medals[pos - 1] : `**${pos}.**`;

      // Essayer de résoudre le username
      let name;
      try {
        const member = await interaction.guild.members.fetch(u.user_id).catch(() => null);
        name = member?.user?.username || `<@${u.user_id}>`;
      } catch {
        name = `<@${u.user_id}>`;
      }

      let value;
      if (type === 'xp') value = `Niv. ${u.level || 0} — ${(u.xp || 0).toLocaleString('fr-FR')} XP`;
      else if (type === 'messages') value = `${(u.total_messages || 0).toLocaleString('fr-FR')} messages`;
      else value = `${(u.voice_minutes || 0).toLocaleString('fr-FR')} minutes`;

      return `${icon} **${name}** — ${value}`;
    }));

    // Position du user courant
    let userPos = null;
    const userRow = await db('users').where('guild_id', guildId).where('user_id', interaction.user.id).first();
    if (userRow) {
      const above = await db('users').where('guild_id', guildId).where(sortColumn, '>', userRow[sortColumn] || 0).count('id as count').first();
      userPos = (above?.count || 0) + 1;
    }

    const embed = new EmbedBuilder()
      .setTitle(`${typeLabel} — Classement`)
      .setDescription(lines.join('\n'))
      .setColor(0xFEE75C)
      .setFooter({ text: `Page ${safePage + 1}/${totalPages} • ${totalUsers} membre(s)${userPos ? ` • Votre rang : #${userPos}` : ''}` })
      .setTimestamp();

    // Boutons de pagination
    const row = new ActionRowBuilder().addComponents(
      new ButtonBuilder()
        .setCustomId(`lb-${type}-${Math.max(0, safePage - 1)}`)
        .setLabel('◀ Précédent')
        .setStyle(ButtonStyle.Secondary)
        .setDisabled(safePage === 0),
      new ButtonBuilder()
        .setCustomId(`lb-${type}-${safePage + 1}`)
        .setLabel('Suivant ▶')
        .setStyle(ButtonStyle.Secondary)
        .setDisabled(safePage >= totalPages - 1),
    );

    return interaction.reply({ embeds: [embed], components: totalPages > 1 ? [row] : [] });
  },
};