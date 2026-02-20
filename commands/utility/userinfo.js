// ===================================
// Ultra Suite — /userinfo
// Informations détaillées sur un membre
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'utility',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('userinfo')
    .setDescription('Voir les informations d\'un membre')
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre à consulter')),

  async execute(interaction) {
    const target = interaction.options.getUser('membre') || interaction.user;
    const member = await interaction.guild.members.fetch(target.id).catch(() => null);
    const guildId = interaction.guildId;
    const db = getDb();

    const embed = new EmbedBuilder()
      .setAuthor({ name: target.tag, iconURL: target.displayAvatarURL() })
      .setThumbnail(target.displayAvatarURL({ size: 512 }))
      .setColor(member?.displayHexColor !== '#000000' ? member.displayHexColor : 0x5865F2)
      .addFields(
        { name: '🆔 ID', value: target.id, inline: true },
        { name: '📛 Nom d\'affichage', value: member?.displayName || target.username, inline: true },
        { name: '🤖 Bot', value: target.bot ? 'Oui' : 'Non', inline: true },
        {
          name: '📅 Compte créé',
          value: `<t:${Math.floor(target.createdTimestamp / 1000)}:f>\n(<t:${Math.floor(target.createdTimestamp / 1000)}:R>)`,
          inline: true,
        },
      )
      .setTimestamp();

    if (member) {
      embed.addFields(
        {
          name: '📥 A rejoint le',
          value: member.joinedAt
            ? `<t:${Math.floor(member.joinedTimestamp / 1000)}:f>\n(<t:${Math.floor(member.joinedTimestamp / 1000)}:R>)`
            : 'Inconnu',
          inline: true,
        },
      );

      // Rôles
      const roles = member.roles.cache
        .filter((r) => r.id !== interaction.guild.id)
        .sort((a, b) => b.position - a.position)
        .map((r) => r.toString());

      if (roles.length > 0) {
        embed.addFields({
          name: `🎭 Rôles (${roles.length})`,
          value: roles.slice(0, 20).join(', ') + (roles.length > 20 ? ` +${roles.length - 20}` : ''),
          inline: false,
        });
      }

      // Rôle le plus haut
      embed.addFields({
        name: '👑 Rôle principal',
        value: member.roles.highest.id !== interaction.guild.id ? member.roles.highest.toString() : '*Aucun*',
        inline: true,
      });

      // Permissions clés
      const perms = [];
      if (member.permissions.has('Administrator')) perms.push('Admin');
      else {
        if (member.permissions.has('ManageGuild')) perms.push('Gérer serveur');
        if (member.permissions.has('ManageMessages')) perms.push('Gérer messages');
        if (member.permissions.has('BanMembers')) perms.push('Bannir');
        if (member.permissions.has('KickMembers')) perms.push('Expulser');
        if (member.permissions.has('ManageRoles')) perms.push('Gérer rôles');
        if (member.permissions.has('ManageChannels')) perms.push('Gérer channels');
      }
      if (perms.length > 0) {
        embed.addFields({ name: '🔑 Permissions', value: perms.join(', '), inline: true });
      }

      // Timeout actif ?
      if (member.communicationDisabledUntilTimestamp && member.communicationDisabledUntilTimestamp > Date.now()) {
        embed.addFields({
          name: '🔇 Timeout',
          value: `Expire <t:${Math.floor(member.communicationDisabledUntilTimestamp / 1000)}:R>`,
          inline: true,
        });
      }
    }

    // Données DB (si existantes)
    const userData = await db('users')
      .where('guild_id', guildId)
      .where('user_id', target.id)
      .first();

    if (userData) {
      const dbFields = [];
      if (userData.level !== undefined) dbFields.push({ name: '⭐ Niveau', value: String(userData.level || 0), inline: true });
      if (userData.xp !== undefined) dbFields.push({ name: '✨ XP', value: (userData.xp || 0).toLocaleString('fr-FR'), inline: true });
      if (userData.balance !== undefined) dbFields.push({ name: '💰 Solde', value: (userData.balance || 0).toLocaleString('fr-FR'), inline: true });
      if (userData.total_messages) dbFields.push({ name: '💬 Messages', value: (userData.total_messages || 0).toLocaleString('fr-FR'), inline: true });
      if (userData.voice_minutes) dbFields.push({ name: '🎙️ Vocal', value: `${(userData.voice_minutes || 0).toLocaleString('fr-FR')} min`, inline: true });
      if (dbFields.length > 0) embed.addFields(...dbFields);
    }

    // Sanctions actives
    const activeSanctions = await db('sanctions')
      .where('guild_id', guildId)
      .where('target_id', target.id)
      .where('active', true)
      .count('id as count')
      .first();

    if ((activeSanctions?.count || 0) > 0) {
      embed.addFields({
        name: '⚠️ Sanctions actives',
        value: `${activeSanctions.count} sanction(s) active(s)`,
        inline: true,
      });
    }

    return interaction.reply({ embeds: [embed] });
  },
};