// ===================================
// Ultra Suite — Verify Buttons Handler
// ===================================

const { EmbedBuilder, ModalBuilder, TextInputBuilder, TextInputStyle, ActionRowBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  customIds: ['verify_button', 'verify_captcha', 'verify_rules', 'verify_question'],
  type: 'button',

  async execute(interaction) {
    const db = getDb();
    const guildId = interaction.guildId;
    const config = await db('verification_config').where({ guild_id: guildId }).first();
    if (!config || !config.enabled) return interaction.reply({ content: '❌ Système de vérification désactivé.', ephemeral: true });

    const member = await interaction.guild.members.fetch(interaction.user.id).catch(() => null);
    if (!member) return;

    // Check if already verified
    if (member.roles.cache.has(config.role_id)) {
      return interaction.reply({ content: '✅ Vous êtes déjà vérifié(e) !', ephemeral: true });
    }

    switch (interaction.customId) {
      case 'verify_button':
      case 'verify_rules': {
        // Direct verification
        await member.roles.add(config.role_id).catch(() => {});
        return interaction.reply({
          embeds: [new EmbedBuilder()
            .setTitle('✅ Vérifié !')
            .setColor(0x2ECC71)
            .setDescription('Vous avez été vérifié avec succès. Bienvenue sur le serveur !')],
          ephemeral: true,
        });
      }

      case 'verify_captcha': {
        // Generate simple captcha
        const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
        const captcha = Array.from({ length: 6 }, () => chars[Math.floor(Math.random() * chars.length)]).join('');

        const modal = new ModalBuilder()
          .setCustomId(`verify_captcha_modal_${captcha}`)
          .setTitle('Vérification Captcha');

        const input = new TextInputBuilder()
          .setCustomId('captcha_input')
          .setLabel(`Entrez le code : ${captcha}`)
          .setStyle(TextInputStyle.Short)
          .setMinLength(6)
          .setMaxLength(6)
          .setRequired(true);

        modal.addComponents(new ActionRowBuilder().addComponents(input));
        return interaction.showModal(modal);
      }

      case 'verify_question': {
        const modal = new ModalBuilder()
          .setCustomId('verify_question_modal')
          .setTitle('Vérification — Question');

        const input = new TextInputBuilder()
          .setCustomId('question_input')
          .setLabel('Pourquoi souhaitez-vous rejoindre ?')
          .setStyle(TextInputStyle.Paragraph)
          .setMinLength(10)
          .setMaxLength(500)
          .setRequired(true);

        modal.addComponents(new ActionRowBuilder().addComponents(input));
        return interaction.showModal(modal);
      }
    }
  },
};
