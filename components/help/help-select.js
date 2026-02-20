// ===================================
// Ultra Suite — Composant Help Select
// Gère le menu de sélection de la commande /help
// ===================================

const { EmbedBuilder } = require('discord.js');

const MODULE_INFO = {
  admin: { emoji: '⚙️', label: 'Administration', description: 'Configuration du bot et des modules' },
  moderation: { emoji: '🔨', label: 'Modération', description: 'Ban, kick, warn, timeout, purge, lock, notes' },
  tickets: { emoji: '🎫', label: 'Tickets', description: 'Système de support par tickets' },
  logs: { emoji: '📋', label: 'Logs', description: 'Journalisation des événements' },
  security: { emoji: '🔒', label: 'Sécurité', description: 'Automod, anti-spam, anti-raid' },
  onboarding: { emoji: '👋', label: 'Onboarding', description: 'Bienvenue, au revoir, auto-rôle' },
  xp: { emoji: '⭐', label: 'XP / Niveaux', description: 'Expérience et classements' },
  economy: { emoji: '💰', label: 'Économie', description: 'Monnaie, daily, shop, transferts' },
  roles: { emoji: '🎭', label: 'Rôles', description: 'Menus de rôles' },
  utility: { emoji: '🔧', label: 'Utilitaire', description: 'Infos, avatar, embed, rappels' },
  fun: { emoji: '🎮', label: 'Fun', description: '8ball, dés, PFC et plus' },
  stats: { emoji: '📊', label: 'Statistiques', description: 'Métriques du serveur' },
  tempvoice: { emoji: '🔊', label: 'Vocaux temporaires', description: 'Gestion des salons vocaux temp.' },
  tags: { emoji: '🏷️', label: 'Tags / FAQ', description: 'Réponses rapides prédéfinies' },
  announcements: { emoji: '📢', label: 'Annonces', description: 'Annonces et publications' },
};

module.exports = {
  prefix: 'help-module-select',
  type: 'select',

  async execute(interaction) {
    const moduleName = interaction.values[0];
    const info = MODULE_INFO[moduleName] || { emoji: '📦', label: moduleName, description: '' };

    // Trouver les commandes de ce module
    const commands = [];
    if (interaction.client.commands) {
      for (const [, cmd] of interaction.client.commands) {
        const cmdModule = cmd.module || 'utility';
        if (cmdModule === moduleName) {
          commands.push({
            name: cmd.data?.name || '?',
            desc: cmd.data?.description || 'Pas de description',
          });
        }
      }
    }

    const lines = commands.map((c) => `\`/${c.name}\` — ${c.desc}`);

    const embed = new EmbedBuilder()
      .setTitle(`${info.emoji} ${info.label}`)
      .setDescription(
        `${info.description}\n\n` +
        (lines.length > 0
          ? `**Commandes (${lines.length}) :**\n${lines.join('\n')}`
          : '*Aucune commande enregistrée pour ce module.*')
      )
      .setColor(0x5865F2)
      .setFooter({ text: 'Utilisez le menu ci-dessus pour naviguer' })
      .setTimestamp();

    await interaction.update({ embeds: [embed] });
  },
};