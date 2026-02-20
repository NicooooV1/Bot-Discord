// ===================================
// Ultra Suite — /voice
// Gestion des salons vocaux temporaires
// /voice name | limit | lock | unlock | invite | kick
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ChannelType } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'tempvoice',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('voice')
    .setDescription('Gérer votre salon vocal temporaire')
    .addSubcommand((sub) =>
      sub.setName('name').setDescription('Renommer votre salon')
        .addStringOption((opt) => opt.setName('nom').setDescription('Nouveau nom').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('limit').setDescription('Limiter le nombre de membres')
        .addIntegerOption((opt) => opt.setName('max').setDescription('Limite (0 = illimité)').setRequired(true).setMinValue(0).setMaxValue(99)))
    .addSubcommand((sub) =>
      sub.setName('lock').setDescription('Verrouiller le salon (personne ne peut rejoindre)'))
    .addSubcommand((sub) =>
      sub.setName('unlock').setDescription('Déverrouiller le salon'))
    .addSubcommand((sub) =>
      sub.setName('invite').setDescription('Autoriser un membre à rejoindre')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre à inviter').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('kick').setDescription('Expulser un membre du salon')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre à expulser').setRequired(true))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const userId = interaction.user.id;
    const db = getDb();

    // Vérifier que l'user est dans un vocal temporaire qu'il possède
    const member = await interaction.guild.members.fetch(userId);
    const voiceChannel = member.voice?.channel;

    if (!voiceChannel) {
      return interaction.reply({ content: '❌ Vous devez être dans un salon vocal.', ephemeral: true });
    }

    const tempChannel = await db('temp_voice_channels')
      .where('guild_id', guildId)
      .where('channel_id', voiceChannel.id)
      .where('owner_id', userId)
      .first();

    if (!tempChannel) {
      return interaction.reply({ content: '❌ Vous n\'êtes pas le propriétaire de ce salon vocal.', ephemeral: true });
    }

    // === NAME ===
    if (sub === 'name') {
      const name = interaction.options.getString('nom').slice(0, 100);
      await voiceChannel.setName(name);
      return interaction.reply({ content: `✅ Salon renommé en **${name}**.`, ephemeral: true });
    }

    // === LIMIT ===
    if (sub === 'limit') {
      const max = interaction.options.getInteger('max');
      await voiceChannel.setUserLimit(max);
      return interaction.reply({
        content: max === 0 ? '✅ Limite retirée (illimité).' : `✅ Limite définie à **${max}** membres.`,
        ephemeral: true,
      });
    }

    // === LOCK ===
    if (sub === 'lock') {
      await voiceChannel.permissionOverwrites.edit(interaction.guild.roles.everyone, { Connect: false });
      return interaction.reply({ content: '🔒 Salon verrouillé.', ephemeral: true });
    }

    // === UNLOCK ===
    if (sub === 'unlock') {
      await voiceChannel.permissionOverwrites.edit(interaction.guild.roles.everyone, { Connect: null });
      return interaction.reply({ content: '🔓 Salon déverrouillé.', ephemeral: true });
    }

    // === INVITE ===
    if (sub === 'invite') {
      const target = interaction.options.getUser('membre');
      await voiceChannel.permissionOverwrites.edit(target.id, { Connect: true, ViewChannel: true });
      return interaction.reply({ content: `✅ ${target} peut maintenant rejoindre votre salon.`, ephemeral: true });
    }

    // === KICK ===
    if (sub === 'kick') {
      const target = interaction.options.getUser('membre');
      const targetMember = await interaction.guild.members.fetch(target.id).catch(() => null);

      if (!targetMember?.voice?.channel || targetMember.voice.channel.id !== voiceChannel.id) {
        return interaction.reply({ content: '❌ Ce membre n\'est pas dans votre salon.', ephemeral: true });
      }

      await targetMember.voice.disconnect('Expulsé par le propriétaire');
      await voiceChannel.permissionOverwrites.edit(target.id, { Connect: false });
      return interaction.reply({ content: `✅ ${target} expulsé du salon.`, ephemeral: true });
    }
  },
};