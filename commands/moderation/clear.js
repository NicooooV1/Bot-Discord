const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const { COLORS } = require('../../utils/logger');
const { errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('clear')
    .setDescription('🗑️ Supprimer des messages dans le salon')
    .addIntegerOption(opt =>
      opt.setName('nombre')
        .setDescription('Nombre de messages à supprimer (1-100)')
        .setRequired(true)
        .setMinValue(1)
        .setMaxValue(100)
    )
    .addUserOption(opt =>
      opt.setName('utilisateur')
        .setDescription('Filtrer par utilisateur')
    )
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageMessages),

  async execute(interaction) {
    const amount = interaction.options.getInteger('nombre');
    const targetUser = interaction.options.getUser('utilisateur');

    await interaction.deferReply({ ephemeral: true });

    try {
      let messages = await interaction.channel.messages.fetch({ limit: 100 });

      if (targetUser) {
        messages = messages.filter(msg => msg.author.id === targetUser.id);
      }

      // Prendre seulement le nombre demandé
      messages = [...messages.values()].slice(0, amount);

      // Filtrer les messages de plus de 14 jours (limitation Discord)
      const twoWeeksAgo = Date.now() - 14 * 24 * 60 * 60 * 1000;
      const deletable = messages.filter(msg => msg.createdTimestamp > twoWeeksAgo);
      const tooOld = messages.length - deletable.length;

      if (deletable.length === 0) {
        return interaction.editReply({ content: '❌ Aucun message supprimable trouvé (les messages de plus de 14 jours ne peuvent pas être supprimés en masse).' });
      }

      const deleted = await interaction.channel.bulkDelete(deletable, true);

      let description = `🗑️ **${deleted.size}** message(s) supprimé(s).`;
      if (targetUser) description += `\nFiltré par: **${targetUser.tag}**`;
      if (tooOld > 0) description += `\n⚠️ ${tooOld} message(s) ignoré(s) (trop anciens).`;

      await interaction.editReply({ content: description });
    } catch (error) {
      console.error('[CLEAR]', error);
      await interaction.editReply({ content: '❌ Erreur lors de la suppression des messages.' });
    }
  },
};
