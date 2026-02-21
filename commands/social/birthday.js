// ===================================
// Ultra Suite — /birthday
// Système d'anniversaires
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'social',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('birthday')
    .setDescription('Gestion des anniversaires')
    .addSubcommand((s) =>
      s.setName('set').setDescription('Définir votre anniversaire')
        .addIntegerOption((o) => o.setName('jour').setDescription('Jour').setRequired(true).setMinValue(1).setMaxValue(31))
        .addIntegerOption((o) => o.setName('mois').setDescription('Mois').setRequired(true).setMinValue(1).setMaxValue(12)),
    )
    .addSubcommand((s) =>
      s.setName('list').setDescription('Liste des prochains anniversaires'),
    )
    .addSubcommand((s) =>
      s.setName('remove').setDescription('Retirer votre anniversaire'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    const ensureProfile = async (userId) => {
      const p = await db('social_profiles').where({ guild_id: guildId, user_id: userId }).first();
      if (!p) await db('social_profiles').insert({ guild_id: guildId, user_id: userId });
      return db('social_profiles').where({ guild_id: guildId, user_id: userId }).first();
    };

    switch (sub) {
      case 'set': {
        const day = interaction.options.getInteger('jour');
        const month = interaction.options.getInteger('mois');
        const bd = `${String(day).padStart(2, '0')}/${String(month).padStart(2, '0')}`;
        await ensureProfile(interaction.user.id);
        await db('social_profiles').where({ guild_id: guildId, user_id: interaction.user.id }).update({ birthday: bd });
        return interaction.reply({ content: `✅ Anniversaire défini : 🎂 ${bd}`, ephemeral: true });
      }

      case 'list': {
        const profiles = await db('social_profiles').where({ guild_id: guildId }).whereNotNull('birthday').select('user_id', 'birthday');

        if (!profiles.length) return interaction.reply({ content: 'Aucun anniversaire enregistré.', ephemeral: true });

        const now = new Date();
        const today = `${String(now.getDate()).padStart(2, '0')}/${String(now.getMonth() + 1).padStart(2, '0')}`;

        const sorted = profiles.map((p) => {
          const [d, m] = p.birthday.split('/').map(Number);
          const thisYear = new Date(now.getFullYear(), m - 1, d);
          if (thisYear < now) thisYear.setFullYear(now.getFullYear() + 1);
          return { ...p, nextDate: thisYear };
        }).sort((a, b) => a.nextDate - b.nextDate).slice(0, 15);

        const embed = new EmbedBuilder()
          .setTitle('🎂 Prochains anniversaires')
          .setColor(0xFF69B4)
          .setDescription(sorted.map((p) => {
            const isToday = p.birthday === today;
            return `${isToday ? '🎉' : '🎂'} <@${p.user_id}> — **${p.birthday}**${isToday ? ' (Aujourd\'hui !)' : ` (<t:${Math.floor(p.nextDate.getTime() / 1000)}:R>)`}`;
          }).join('\n'));

        return interaction.reply({ embeds: [embed] });
      }

      case 'remove': {
        await ensureProfile(interaction.user.id);
        await db('social_profiles').where({ guild_id: guildId, user_id: interaction.user.id }).update({ birthday: null });
        return interaction.reply({ content: '✅ Anniversaire retiré.', ephemeral: true });
      }
    }
  },
};
