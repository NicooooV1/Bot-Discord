// ===================================
// Ultra Suite — TempVoice: /tempvoice
// Gestion des salons vocaux temporaires
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits, ChannelType } = require('discord.js');
const { getDb } = require('../../database');
const { successEmbed, errorEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'tempvoice',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('tempvoice')
    .setDescription('Gère ton salon vocal temporaire')
    .addSubcommand((sub) =>
      sub.setName('name').setDescription('Renomme ton salon').addStringOption((opt) => opt.setName('nom').setDescription('Nouveau nom').setRequired(true))
    )
    .addSubcommand((sub) =>
      sub.setName('limit').setDescription('Limite d\'utilisateurs').addIntegerOption((opt) => opt.setName('nombre').setDescription('Limite (0 = illimité)').setRequired(true).setMinValue(0).setMaxValue(99))
    )
    .addSubcommand((sub) =>
      sub.setName('lock').setDescription('Verrouille ton salon')
    )
    .addSubcommand((sub) =>
      sub.setName('unlock').setDescription('Déverrouille ton salon')
    )
    .addSubcommand((sub) =>
      sub.setName('permit').setDescription('Autorise un utilisateur').addUserOption((opt) => opt.setName('user').setDescription('Utilisateur').setRequired(true))
    )
    .addSubcommand((sub) =>
      sub.setName('reject').setDescription('Bloque un utilisateur').addUserOption((opt) => opt.setName('user').setDescription('Utilisateur').setRequired(true))
    ),

  async execute(interaction) {
    const db = getDb();
    const sub = interaction.options.getSubcommand();

    // Vérifier que l'utilisateur est dans son propre salon temporaire
    const vc = interaction.member.voice?.channel;
    if (!vc) return interaction.reply({ embeds: [errorEmbed('❌ Tu dois être dans un salon vocal.')], ephemeral: true });

    const tempVc = await db('temp_voice_channels').where({
      channel_id: vc.id,
      owner_id: interaction.user.id,
    }).first();

    if (!tempVc) {
      return interaction.reply({ embeds: [errorEmbed('❌ Ce n\'est pas ton salon vocal temporaire.')], ephemeral: true });
    }

    switch (sub) {
      case 'name': {
        const name = interaction.options.getString('nom');
        await vc.setName(name);
        return interaction.reply({ embeds: [successEmbed(`✅ Salon renommé en **${name}**`)], ephemeral: true });
      }

      case 'limit': {
        const limit = interaction.options.getInteger('nombre');
        await vc.setUserLimit(limit);
        return interaction.reply({ embeds: [successEmbed(`✅ Limite définie à **${limit || 'illimité'}**`)], ephemeral: true });
      }

      case 'lock': {
        await vc.permissionOverwrites.edit(interaction.guild.id, { Connect: false });
        return interaction.reply({ embeds: [successEmbed('🔒 Salon verrouillé.')], ephemeral: true });
      }

      case 'unlock': {
        await vc.permissionOverwrites.edit(interaction.guild.id, { Connect: null });
        return interaction.reply({ embeds: [successEmbed('🔓 Salon déverrouillé.')], ephemeral: true });
      }

      case 'permit': {
        const user = interaction.options.getUser('user');
        await vc.permissionOverwrites.edit(user.id, { Connect: true, ViewChannel: true });
        return interaction.reply({ embeds: [successEmbed(`✅ ${user} autorisé.`)], ephemeral: true });
      }

      case 'reject': {
        const user = interaction.options.getUser('user');
        await vc.permissionOverwrites.edit(user.id, { Connect: false });
        const member = await interaction.guild.members.fetch(user.id).catch(() => null);
        if (member?.voice?.channelId === vc.id) await member.voice.disconnect();
        return interaction.reply({ embeds: [successEmbed(`✅ ${user} bloqué.`)], ephemeral: true });
      }
    }
  },
};
