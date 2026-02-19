// ===================================
// Ultra Suite — Modal: apply_modal
// Traite la soumission de candidature
// ===================================

const { getDb } = require('../../database');
const { successEmbed, createEmbed } = require('../../utils/embeds');
const ConfigService = require('../../core/configService');

module.exports = {
  id: 'apply_modal',

  async execute(interaction) {
    try {
      const q1 = interaction.fields.getTextInputValue('apply_q1');
    const q2 = interaction.fields.getTextInputValue('apply_q2');
    const q3 = interaction.fields.getTextInputValue('apply_q3');

    const db = getDb();
    const answers = JSON.stringify({ q1, q2, q3 });

    const [id] = await db('applications').insert({
      guild_id: interaction.guild.id,
      applicant_id: interaction.user.id,
      answers,
      status: 'pending',
    });

    // Envoyer dans le salon de logs si configuré
    const config = await ConfigService.get(interaction.guild.id);
    if (config.modLogChannel) {
      const channel = interaction.guild.channels.cache.get(config.modLogChannel);
      if (channel) {
        const embed = createEmbed('primary')
          .setTitle(`📝 Nouvelle candidature #${id}`)
          .setDescription(`De <@${interaction.user.id}>`)
          .addFields(
            { name: '1. Présentation', value: q1.substring(0, 1024) },
            { name: '2. Motivation', value: q2.substring(0, 1024) },
            { name: '3. Expérience', value: q3.substring(0, 1024) }
          )
          .setTimestamp();

        channel.send({ embeds: [embed] }).catch(() => {});
      }
    }

    return interaction.reply({
      embeds: [successEmbed('✅ Candidature envoyée ! Tu seras notifié de la décision.')],
      ephemeral: true,
    });
    } catch (err) {
      return interaction.reply({ content: '❌ Impossible d\'envoyer la candidature.', ephemeral: true }).catch(() => {});
    }
  },
};
