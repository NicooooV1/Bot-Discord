// ===================================
// Ultra Suite — Moderation: /warnings
// Liste les warns actifs d'un membre
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const sanctionQueries = require('../../database/sanctionQueries');
const { createEmbed, errorEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'moderation',
  data: new SlashCommandBuilder()
    .setName('warnings')
    .setDescription('Affiche les avertissements actifs d\'un membre')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre').setRequired(true)),

  async execute(interaction) {
    const user = interaction.options.getUser('membre');

    const warns = await sanctionQueries.listForUser(interaction.guild.id, user.id, {
      type: 'WARN',
      active: true,
    });

    if (warns.length === 0) {
      return interaction.reply({
        embeds: [createEmbed('info').setDescription(`✅ **${user.tag}** n'a aucun avertissement actif.`)],
        ephemeral: true,
      });
    }

    const list = warns
      .map(
        (w) =>
          `**#${w.case_number}** — ${w.reason}\n  └ <@${w.moderator_id}> — <t:${Math.floor(new Date(w.created_at).getTime() / 1000)}:R>`
      )
      .join('\n\n');

    const embed = createEmbed('warning')
      .setTitle(`⚠️ Avertissements — ${user.tag}`)
      .setDescription(list)
      .setFooter({ text: `${warns.length} warn(s) actif(s)` });

    await interaction.reply({ embeds: [embed], ephemeral: true });
  },
};
