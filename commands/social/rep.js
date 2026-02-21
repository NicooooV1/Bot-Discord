// ===================================
// Ultra Suite — /rep
// Système de réputation
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'social',
  cooldown: 3600, // 1 hour

  data: new SlashCommandBuilder()
    .setName('rep')
    .setDescription('Donner un point de réputation')
    .addUserOption((o) => o.setName('utilisateur').setDescription('Utilisateur à récompenser').setRequired(true)),

  async execute(interaction) {
    const db = getDb();
    const target = interaction.options.getUser('utilisateur');
    const guildId = interaction.guildId;

    if (target.id === interaction.user.id) return interaction.reply({ content: '❌ Vous ne pouvez pas vous donner de la réputation.', ephemeral: true });
    if (target.bot) return interaction.reply({ content: '❌ Vous ne pouvez pas donner de réputation à un bot.', ephemeral: true });

    const existing = await db('social_profiles').where({ guild_id: guildId, user_id: target.id }).first();
    if (!existing) {
      await db('social_profiles').insert({ guild_id: guildId, user_id: target.id, bio: '', reputation: 1 });
    } else {
      await db('social_profiles').where({ guild_id: guildId, user_id: target.id }).increment('reputation', 1);
    }

    const newRep = (existing?.reputation ?? 0) + 1;

    return interaction.reply({
      embeds: [new EmbedBuilder()
        .setColor(0x2ECC71)
        .setDescription(`⭐ ${interaction.user} a donné un point de réputation à ${target} !\n\nNouvelle réputation : **${newRep}** ⭐`)],
    });
  },
};
