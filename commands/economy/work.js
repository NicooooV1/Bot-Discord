// ===================================
// Ultra Suite — /work
// Travailler pour gagner de l'argent
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

const JOBS = [
  { name: 'Développeur', emoji: '💻', min: 100, max: 500 },
  { name: 'Chef cuisinier', emoji: '👨‍🍳', min: 80, max: 400 },
  { name: 'Médecin', emoji: '🩺', min: 150, max: 600 },
  { name: 'Artiste', emoji: '🎨', min: 50, max: 350 },
  { name: 'Professeur', emoji: '📚', min: 100, max: 450 },
  { name: 'Mécanicien', emoji: '🔧', min: 90, max: 400 },
  { name: 'Jardinier', emoji: '🌱', min: 60, max: 300 },
  { name: 'Photographe', emoji: '📷', min: 70, max: 380 },
  { name: 'Musicien', emoji: '🎵', min: 80, max: 420 },
  { name: 'Architecte', emoji: '🏗️', min: 120, max: 550 },
  { name: 'Astronaute', emoji: '🚀', min: 200, max: 800 },
  { name: 'Détective', emoji: '🔍', min: 110, max: 470 },
  { name: 'Pompier', emoji: '🚒', min: 130, max: 500 },
  { name: 'Pilote', emoji: '✈️', min: 180, max: 650 },
  { name: 'Streamer', emoji: '🎮', min: 50, max: 700 },
];

module.exports = {
  module: 'economy',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('work')
    .setDescription('Travailler pour gagner de l\'argent'),

  async execute(interaction) {
    const guildId = interaction.guildId;
    const userId = interaction.user.id;
    const db = getDb();
    const config = await configService.get(guildId);
    const eco = config.economy || {};
    const symbol = eco.currencySymbol || '🪙';
    const workCooldown = eco.workCooldown || 3600000; // 1h default

    // Check cooldown
    let cooldownRow = await db('work_cooldowns').where({ guild_id: guildId, user_id: userId }).first();
    if (!cooldownRow) {
      await db('work_cooldowns').insert({ guild_id: guildId, user_id: userId });
      cooldownRow = { last_work: null };
    }

    const now = Date.now();
    const lastWork = cooldownRow.last_work ? new Date(cooldownRow.last_work).getTime() : 0;
    const remaining = workCooldown - (now - lastWork);

    if (remaining > 0) {
      const mins = Math.floor(remaining / 60000);
      const secs = Math.floor((remaining % 60000) / 1000);
      return interaction.reply({
        content: `⏳ Vous devez attendre **${mins}m ${secs}s** avant de travailler à nouveau.`,
        ephemeral: true,
      });
    }

    // Random job
    const job = JOBS[Math.floor(Math.random() * JOBS.length)];
    const base = Math.floor(Math.random() * (job.max - job.min + 1)) + job.min;

    // Apply multiplier
    const multiplier = eco.workMultiplier || 1;
    const earned = Math.floor(base * multiplier);

    // Update balance
    await db('users')
      .insert({ guild_id: guildId, user_id: userId, balance: earned })
      .onConflict(['guild_id', 'user_id'])
      .merge({ balance: db.raw('balance + ?', [earned]), total_earned: db.raw('COALESCE(total_earned, 0) + ?', [earned]) });

    // Update cooldown
    await db('work_cooldowns').where({ guild_id: guildId, user_id: userId }).update({ last_work: new Date() });

    // Log transaction
    await db('transactions').insert({
      guild_id: guildId,
      from_id: 'SYSTEM',
      to_id: userId,
      amount: earned,
      type: 'work',
      note: job.name,
    });

    const embed = new EmbedBuilder()
      .setColor(0x2ECC71)
      .setTitle(`${job.emoji} ${job.name}`)
      .setDescription(`Vous avez travaillé comme **${job.name}** et gagné **${earned.toLocaleString('fr-FR')}** ${symbol} !`)
      .setFooter({ text: `Prochain travail dans ${Math.floor(workCooldown / 60000)} minutes` })
      .setTimestamp();

    return interaction.reply({ embeds: [embed] });
  },
};
