// ===================================
// Ultra Suite — /rank
// Voir son rang XP ou celui d'un membre
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'xp',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('rank')
    .setDescription('Voir votre rang XP ou celui d\'un membre')
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à consulter')),

  async execute(interaction) {
    const target = interaction.options.getUser('membre') || interaction.user;
    const guildId = interaction.guildId;
    const db = getDb();

    const user = await db('users')
      .where('guild_id', guildId)
      .where('user_id', target.id)
      .first();

    if (!user) {
      return interaction.reply({
        content: target.id === interaction.user.id
          ? '❌ Vous n\'avez pas encore d\'XP. Envoyez des messages pour en gagner !'
          : `❌ **${target.username}** n'a pas encore d'XP.`,
        ephemeral: true,
      });
    }

    // Calculer le classement
    const rank = await db('users')
      .where('guild_id', guildId)
      .where('xp', '>', user.xp || 0)
      .count('id as count')
      .first();
    const position = (rank?.count || 0) + 1;

    // Calcul niveau et progression
    const level = user.level || 0;
    const currentXp = user.xp || 0;
    const xpForCurrentLevel = Math.pow(level / 0.1, 2);
    const xpForNextLevel = Math.pow((level + 1) / 0.1, 2);
    const xpNeeded = Math.floor(xpForNextLevel - xpForCurrentLevel);
    const xpProgress = Math.floor(currentXp - xpForCurrentLevel);
    const progressPercent = Math.min(100, Math.max(0, Math.round((xpProgress / xpNeeded) * 100)));

    // Barre de progression visuelle
    const barLength = 20;
    const filled = Math.round((progressPercent / 100) * barLength);
    const bar = '█'.repeat(filled) + '░'.repeat(barLength - filled);

    // Total membres avec XP
    const totalUsers = await db('users').where('guild_id', guildId).count('id as count').first();

    const embed = new EmbedBuilder()
      .setAuthor({ name: `${target.username}`, iconURL: target.displayAvatarURL() })
      .setThumbnail(target.displayAvatarURL({ size: 256 }))
      .setColor(0x5865F2)
      .addFields(
        { name: '🏆 Rang', value: `#${position} / ${totalUsers?.count || 0}`, inline: true },
        { name: '⭐ Niveau', value: String(level), inline: true },
        { name: '✨ XP Total', value: currentXp.toLocaleString('fr-FR'), inline: true },
        { name: `📊 Progression (${progressPercent}%)`, value: `\`${bar}\`\n${xpProgress.toLocaleString('fr-FR')} / ${xpNeeded.toLocaleString('fr-FR')} XP`, inline: false },
        { name: '💬 Messages', value: (user.total_messages || 0).toLocaleString('fr-FR'), inline: true },
        { name: '🎙️ Vocal', value: `${(user.voice_minutes || 0).toLocaleString('fr-FR')} min`, inline: true },
      )
      .setTimestamp();

    return interaction.reply({ embeds: [embed] });
  },
};