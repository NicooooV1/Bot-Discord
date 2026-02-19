const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { getModLogs } = require('../../utils/database');
const { COLORS } = require('../../utils/logger');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('modlogs')
    .setDescription('📜 Voir l\'historique de modération d\'un utilisateur')
    .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur').setRequired(true))
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers),

  async execute(interaction) {
    const target = interaction.options.getUser('utilisateur');
    const logs = getModLogs(interaction.guild.id, target.id);

    if (logs.length === 0) {
      return interaction.reply({
        content: `✅ **${target.tag}** n'a aucun historique de modération.`,
        ephemeral: true,
      });
    }

    const actionEmojis = {
      BAN: '🔨',
      UNBAN: '🔓',
      KICK: '👢',
      MUTE: '🔇',
      UNMUTE: '🔊',
      WARN: '⚠️',
      SOFTBAN: '🧹',
      NICKNAME: '📝',
      'AUTO-MUTE': '🛡️',
    };

    const embed = new EmbedBuilder()
      .setTitle(`📜 Historique — ${target.tag}`)
      .setColor(COLORS.BLUE)
      .setThumbnail(target.displayAvatarURL({ dynamic: true }))
      .setDescription(
        logs.map(log => {
          const emoji = actionEmojis[log.action] || '📋';
          const timestamp = Math.floor(new Date(log.created_at).getTime() / 1000);
          let line = `${emoji} **${log.action}** — <t:${timestamp}:R>`;
          line += `\n> Par: <@${log.moderator_id}>`;
          if (log.reason) line += `\n> Raison: ${log.reason.substring(0, 100)}`;
          if (log.duration) line += `\n> Durée: ${log.duration}`;
          return line;
        }).join('\n\n')
      )
      .setFooter({ text: `${logs.length} action(s) enregistrée(s) (25 dernières max)` })
      .setTimestamp();

    await interaction.reply({ embeds: [embed] });
  },
};
