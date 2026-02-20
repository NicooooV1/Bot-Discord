// ===================================
// Ultra Suite — /setup (DEPRECATED)
// Redirige vers /config (nouveau système unifié)
//
// Conservé temporairement pour la transition.
// Sera supprimé dans une future version.
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');

module.exports = {
  module: 'admin',
  adminOnly: true,
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('setup')
    .setDescription('⚠️ Déprécié — Utilisez /config à la place')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator),

  async execute(interaction) {
    return interaction.reply({
      content:
        '⚠️ La commande `/setup` a été remplacée par le nouveau système de configuration.\n\n' +
        '**Utilisez :**\n' +
        '• `/config` — Tableau de bord interactif de tous les modules\n' +
        '• `/config module:moderation` — Configurer un module spécifique\n\n' +
        'Chaque module peut être activé, configuré, et personnalisé individuellement.',
      ephemeral: true,
    });
  },
};