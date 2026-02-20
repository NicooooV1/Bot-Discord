// ===================================
// Ultra Suite — /purge
// Suppression en masse avec filtres
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const { createModuleLogger } = require('../../core/logger');
const log = createModuleLogger('Purge');

module.exports = {
  module: 'moderation',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('purge')
    .setDescription('Supprimer des messages en masse')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageMessages)
    .addIntegerOption((opt) => opt.setName('nombre').setDescription('Nombre de messages (1-100)').setRequired(true).setMinValue(1).setMaxValue(100))
    .addUserOption((opt) => opt.setName('membre').setDescription('Uniquement les messages de ce membre'))
    .addStringOption((opt) => opt.setName('filtre').setDescription('Filtre de contenu')
      .addChoices(
        { name: '🤖 Bots uniquement', value: 'bots' },
        { name: '👤 Humains uniquement', value: 'humans' },
        { name: '🔗 Contenant des liens', value: 'links' },
        { name: '📎 Avec pièces jointes', value: 'attachments' },
        { name: '📌 Avec embeds', value: 'embeds' },
      )),

  async execute(interaction) {
    const amount = interaction.options.getInteger('nombre');
    const targetUser = interaction.options.getUser('membre');
    const filter = interaction.options.getString('filtre');

    await interaction.deferReply({ ephemeral: true });

    try {
      const fetched = await interaction.channel.messages.fetch({ limit: amount });
      let toDelete = [...fetched.values()];

      if (targetUser) toDelete = toDelete.filter((m) => m.author.id === targetUser.id);
      if (filter === 'bots') toDelete = toDelete.filter((m) => m.author.bot);
      else if (filter === 'humans') toDelete = toDelete.filter((m) => !m.author.bot);
      else if (filter === 'links') toDelete = toDelete.filter((m) => /https?:\/\/[^\s]+/i.test(m.content));
      else if (filter === 'attachments') toDelete = toDelete.filter((m) => m.attachments.size > 0);
      else if (filter === 'embeds') toDelete = toDelete.filter((m) => m.embeds.length > 0);

      // Discord : impossible de supprimer les messages > 14 jours
      const twoWeeksAgo = Date.now() - 14 * 24 * 60 * 60 * 1000;
      const deletable = toDelete.filter((m) => m.createdTimestamp > twoWeeksAgo);
      const tooOld = toDelete.length - deletable.length;

      if (deletable.length === 0) {
        return interaction.editReply({ content: '❌ Aucun message à supprimer.' });
      }

      const deleted = await interaction.channel.bulkDelete(deletable, true);

      let summary = `🗑️ **${deleted.size}** message(s) supprimé(s).`;
      if (targetUser) summary += `\nFiltre membre : ${targetUser}`;
      if (filter) summary += `\nFiltre : \`${filter}\``;
      if (tooOld > 0) summary += `\n⚠️ ${tooOld} ignoré(s) (> 14 jours)`;

      await interaction.editReply({ content: summary });
      log.info(`Purge: ${deleted.size} msgs dans #${interaction.channel.name} par ${interaction.user.tag}`);
    } catch (err) {
      log.error(`Erreur purge: ${err.message}`);
      await interaction.editReply({ content: '❌ Erreur lors de la suppression.' });
    }
  },
};