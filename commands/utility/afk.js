// ===================================
// Ultra Suite — /afk
// Système AFK
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'utility',
  cooldown: 10,

  data: new SlashCommandBuilder()
    .setName('afk')
    .setDescription('Définir votre statut AFK')
    .addStringOption((o) => o.setName('raison').setDescription('Raison de votre absence')),

  async execute(interaction) {
    const db = getDb();
    const reason = interaction.options.getString('raison') || 'AFK';
    const guildId = interaction.guildId;
    const userId = interaction.user.id;

    const existing = await db('afk_users').where({ guild_id: guildId, user_id: userId }).first();
    if (existing) {
      await db('afk_users').where({ guild_id: guildId, user_id: userId }).delete();
      const duration = Date.now() - new Date(existing.since).getTime();
      const hours = Math.floor(duration / 3600000);
      const mins = Math.floor((duration % 3600000) / 60000);
      return interaction.reply({
        embeds: [new EmbedBuilder()
          .setColor(0x2ECC71)
          .setDescription(`✅ Bienvenue ! Vous n'êtes plus AFK. Durée : ${hours}h ${mins}m`)
          .setFooter({ text: `Raison précédente : ${existing.reason}` }),
        ],
      });
    }

    await db('afk_users').insert({
      guild_id: guildId,
      user_id: userId,
      reason,
      since: new Date(),
    }).onConflict(['guild_id', 'user_id']).merge();

    try {
      const member = await interaction.guild.members.fetch(userId);
      if (!member.displayName.startsWith('[AFK]')) {
        await member.setNickname(`[AFK] ${member.displayName}`.substring(0, 32)).catch(() => {});
      }
    } catch (e) { /* no perms */ }

    return interaction.reply({
      embeds: [new EmbedBuilder()
        .setColor(0xF39C12)
        .setDescription(`💤 ${interaction.user} est maintenant AFK : **${reason}**`),
      ],
    });
  },
};
