// ===================================
// Ultra Suite — /massban
// Bannir plusieurs utilisateurs
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'moderation',
  cooldown: 30,

  data: new SlashCommandBuilder()
    .setName('massban')
    .setDescription('Bannir plusieurs utilisateurs par ID')
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .addStringOption((o) => o.setName('ids').setDescription('IDs séparés par des espaces').setRequired(true))
    .addStringOption((o) => o.setName('raison').setDescription('Raison')),

  async execute(interaction) {
    const idsStr = interaction.options.getString('ids');
    const reason = interaction.options.getString('raison') || 'Massban';
    const guildId = interaction.guildId;
    const db = getDb();

    const ids = idsStr.split(/[\s,]+/).filter((id) => /^\d{17,20}$/.test(id));
    if (!ids.length) return interaction.reply({ content: '❌ Aucun ID valide fourni.', ephemeral: true });
    if (ids.length > 50) return interaction.reply({ content: '❌ Maximum 50 utilisateurs à la fois.', ephemeral: true });

    await interaction.deferReply();

    let banned = 0;
    let failed = 0;
    const errors = [];

    for (const id of ids) {
      try {
        await interaction.guild.members.ban(id, { deleteMessageDays: 1, reason: `[Massban] ${reason}` });
        banned++;
        await db('sanctions').insert({
          guild_id: guildId,
          user_id: id,
          moderator_id: interaction.user.id,
          type: 'BAN',
          reason: `[Massban] ${reason}`,
        });
      } catch (err) {
        failed++;
        errors.push(`\`${id}\`: ${err.message}`);
      }
    }

    const embed = new EmbedBuilder()
      .setTitle('🔨 Massban')
      .setColor(banned > 0 ? 0xE74C3C : 0x95A5A6)
      .addFields(
        { name: '✅ Bannis', value: `${banned}`, inline: true },
        { name: '❌ Échoués', value: `${failed}`, inline: true },
        { name: 'Raison', value: reason },
      )
      .setTimestamp();

    if (errors.length && errors.length <= 10) {
      embed.addFields({ name: 'Erreurs', value: errors.join('\n') });
    }

    return interaction.editReply({ embeds: [embed] });
  },
};
