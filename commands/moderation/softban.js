// ===================================
// Ultra Suite — /softban
// Bannir puis débannir pour purger les messages
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('softban')
    .setDescription('Softban un membre (ban + unban pour purger les messages)')
    .setDefaultMemberPermissions(PermissionFlagsBits.BanMembers)
    .addUserOption((o) => o.setName('membre').setDescription('Membre à softban').setRequired(true))
    .addStringOption((o) => o.setName('raison').setDescription('Raison'))
    .addIntegerOption((o) => o.setName('jours').setDescription('Jours de messages à supprimer').setMinValue(1).setMaxValue(7)),

  async execute(interaction) {
    const target = interaction.options.getUser('membre');
    const reason = interaction.options.getString('raison') || 'Aucune raison';
    const days = interaction.options.getInteger('jours') || 7;
    const guildId = interaction.guildId;
    const db = getDb();

    const member = await interaction.guild.members.fetch(target.id).catch(() => null);
    if (member && !member.bannable) {
      return interaction.reply({ content: '❌ Je ne peux pas softban ce membre.', ephemeral: true });
    }

    await interaction.guild.members.ban(target.id, { deleteMessageDays: days, reason: `[Softban] ${reason}` });
    await interaction.guild.members.unban(target.id, 'Softban — unban automatique');

    await db('sanctions').insert({
      guild_id: guildId,
      user_id: target.id,
      moderator_id: interaction.user.id,
      type: 'SOFTBAN',
      reason,
    });

    // Log
    const config = await configService.get(guildId);
    const logChannel = config.moderation?.logChannel;
    if (logChannel) {
      const ch = await interaction.guild.channels.fetch(logChannel).catch(() => null);
      if (ch) {
        const { EmbedBuilder } = require('discord.js');
        const embed = new EmbedBuilder()
          .setColor(0xE67E22)
          .setTitle('🥾 Softban')
          .addFields(
            { name: 'Membre', value: `${target.tag} (${target.id})`, inline: true },
            { name: 'Modérateur', value: `${interaction.user.tag}`, inline: true },
            { name: 'Raison', value: reason },
            { name: 'Messages supprimés', value: `${days} jour(s)`, inline: true },
          )
          .setTimestamp();
        ch.send({ embeds: [embed] }).catch(() => null);
      }
    }

    return interaction.reply({ content: `🥾 **${target.username}** a été softban. (${days}j de messages supprimés)\n📝 Raison: ${reason}` });
  },
};
