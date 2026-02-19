// ===================================
// Ultra Suite — Moderation: /clear
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const logQueries = require('../../database/logQueries');
const { successEmbed, errorEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'moderation',
  cooldown: 5,
  data: new SlashCommandBuilder()
    .setName('clear')
    .setDescription('Supprime des messages en masse')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageMessages)
    .addIntegerOption((opt) =>
      opt
        .setName('nombre')
        .setDescription('Nombre de messages à supprimer (1-100)')
        .setMinValue(1)
        .setMaxValue(100)
        .setRequired(true)
    )
    .addUserOption((opt) => opt.setName('membre').setDescription('Filtrer par membre'))
    .addChannelOption((opt) => opt.setName('salon').setDescription('Salon cible (défaut: salon actuel)')),

  async execute(interaction) {
    const amount = interaction.options.getInteger('nombre');
    const target = interaction.options.getUser('membre');
    const channel = interaction.options.getChannel('salon') || interaction.channel;

    await interaction.deferReply({ ephemeral: true });

    let messages = await channel.messages.fetch({ limit: 100 });

    // Filtrer par membre si spécifié
    if (target) {
      messages = messages.filter((m) => m.author.id === target.id);
    }

    // Filtrer les messages de moins de 14 jours (limite Discord)
    const twoWeeks = Date.now() - 14 * 86400 * 1000;
    messages = messages.filter((m) => m.createdTimestamp > twoWeeks);

    // Limiter au nombre demandé
    const toDelete = [...messages.values()].slice(0, amount);

    if (toDelete.length === 0) {
      return interaction.editReply({ embeds: [errorEmbed(t('mod.clear.none'))] });
    }

    const deleted = await channel.bulkDelete(toDelete, true);

    await logQueries.create({
      guildId: interaction.guild.id,
      type: 'MOD_ACTION',
      actorId: interaction.user.id,
      targetId: channel.id,
      targetType: 'channel',
      details: {
        action: 'CLEAR',
        count: deleted.size,
        targetUser: target?.id,
        channel: channel.id,
      },
    });

    await interaction.editReply({
      embeds: [successEmbed(t('mod.clear.success', undefined, { count: deleted.size }))],
    });
  },
};
