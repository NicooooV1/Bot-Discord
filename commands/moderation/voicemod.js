// ===================================
// Ultra Suite — /voicemod
// Modération vocale
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('voicemod')
    .setDescription('Modération vocale')
    .setDefaultMemberPermissions(PermissionFlagsBits.MuteMembers)
    .addSubcommand((s) =>
      s.setName('disconnect').setDescription('Déconnecter un membre du vocal')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addStringOption((o) => o.setName('raison').setDescription('Raison')),
    )
    .addSubcommand((s) =>
      s.setName('move').setDescription('Déplacer un membre vers un autre salon vocal')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addChannelOption((o) => o.setName('salon').setDescription('Salon vocal de destination').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('mute').setDescription('Muter/Unmuter un membre en vocal')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addStringOption((o) => o.setName('raison').setDescription('Raison')),
    )
    .addSubcommand((s) =>
      s.setName('deafen').setDescription('Rendre sourd/annuler un membre en vocal')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addStringOption((o) => o.setName('raison').setDescription('Raison')),
    )
    .addSubcommand((s) =>
      s.setName('moveall').setDescription('Déplacer tous les membres d\'un vocal')
        .addChannelOption((o) => o.setName('source').setDescription('Salon source').setRequired(true))
        .addChannelOption((o) => o.setName('destination').setDescription('Salon destination').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('muteall').setDescription('Muter/Unmuter tous les membres d\'un vocal')
        .addChannelOption((o) => o.setName('salon').setDescription('Salon vocal').setRequired(true)),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    switch (sub) {
      case 'disconnect': {
        const target = interaction.options.getUser('membre');
        const reason = interaction.options.getString('raison') || 'Modération vocale';
        const member = await interaction.guild.members.fetch(target.id).catch(() => null);
        if (!member?.voice.channel) return interaction.reply({ content: '❌ Ce membre n\'est pas en vocal.', ephemeral: true });
        await member.voice.disconnect(reason);
        await db('sanctions').insert({ guild_id: guildId, user_id: target.id, moderator_id: interaction.user.id, type: 'VOICE_DISCONNECT', reason });
        return interaction.reply({ content: `✅ **${target.username}** déconnecté du vocal.` });
      }

      case 'move': {
        const target = interaction.options.getUser('membre');
        const channel = interaction.options.getChannel('salon');
        const member = await interaction.guild.members.fetch(target.id).catch(() => null);
        if (!member?.voice.channel) return interaction.reply({ content: '❌ Ce membre n\'est pas en vocal.', ephemeral: true });
        await member.voice.setChannel(channel, 'Déplacement vocal');
        return interaction.reply({ content: `✅ **${target.username}** déplacé vers ${channel}.` });
      }

      case 'mute': {
        const target = interaction.options.getUser('membre');
        const reason = interaction.options.getString('raison') || 'Mute vocal';
        const member = await interaction.guild.members.fetch(target.id).catch(() => null);
        if (!member?.voice.channel) return interaction.reply({ content: '❌ Ce membre n\'est pas en vocal.', ephemeral: true });
        const newState = !member.voice.serverMute;
        await member.voice.setMute(newState, reason);
        return interaction.reply({ content: `✅ **${target.username}** ${newState ? 'muté' : 'dé-muté'} en vocal.` });
      }

      case 'deafen': {
        const target = interaction.options.getUser('membre');
        const reason = interaction.options.getString('raison') || 'Deafen vocal';
        const member = await interaction.guild.members.fetch(target.id).catch(() => null);
        if (!member?.voice.channel) return interaction.reply({ content: '❌ Ce membre n\'est pas en vocal.', ephemeral: true });
        const newState = !member.voice.serverDeaf;
        await member.voice.setDeaf(newState, reason);
        return interaction.reply({ content: `✅ **${target.username}** ${newState ? 'rendu sourd' : 'audition restaurée'} en vocal.` });
      }

      case 'moveall': {
        const source = interaction.options.getChannel('source');
        const destination = interaction.options.getChannel('destination');
        const members = source.members;
        let moved = 0;
        for (const [, member] of members) {
          try {
            await member.voice.setChannel(destination, 'Déplacement en masse');
            moved++;
          } catch { /* skip */ }
        }
        return interaction.reply({ content: `✅ **${moved}** membre(s) déplacé(s) de ${source} vers ${destination}.` });
      }

      case 'muteall': {
        const channel = interaction.options.getChannel('salon');
        const members = channel.members;
        const firstMember = members.first();
        const shouldMute = firstMember ? !firstMember.voice.serverMute : true;
        let count = 0;
        for (const [, member] of members) {
          try {
            await member.voice.setMute(shouldMute, 'Mute en masse');
            count++;
          } catch { /* skip */ }
        }
        return interaction.reply({ content: `✅ **${count}** membre(s) ${shouldMute ? 'muté(s)' : 'dé-muté(s)'}.` });
      }
    }
  },
};
