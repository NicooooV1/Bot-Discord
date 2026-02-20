// ===================================
// Ultra Suite — /module
// Gestion des modules par serveur
// /module list | enable <nom> | disable <nom>
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const configService = require('../../core/configService');

module.exports = {
  module: 'admin',
  adminOnly: true,
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('module')
    .setDescription('Gérer les modules du bot sur ce serveur')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
    .addSubcommand((sub) =>
      sub.setName('list').setDescription('Voir tous les modules et leur état'))
    .addSubcommand((sub) =>
      sub.setName('enable').setDescription('Activer un module')
        .addStringOption((opt) =>
          opt.setName('nom').setDescription('Nom du module').setRequired(true)
            .addChoices(
              { name: 'Modération', value: 'moderation' },
              { name: 'Tickets', value: 'tickets' },
              { name: 'Logs', value: 'logs' },
              { name: 'Sécurité / Automod', value: 'security' },
              { name: 'Onboarding (welcome/goodbye)', value: 'onboarding' },
              { name: 'XP / Niveaux', value: 'xp' },
              { name: 'Économie', value: 'economy' },
              { name: 'Rôles (menus)', value: 'roles' },
              { name: 'Utilitaire', value: 'utility' },
              { name: 'Fun', value: 'fun' },
              { name: 'Tags / FAQ', value: 'tags' },
              { name: 'Annonces', value: 'announcements' },
              { name: 'Statistiques', value: 'stats' },
              { name: 'Salons vocaux temp.', value: 'tempvoice' },
              { name: 'Candidatures', value: 'applications' },
              { name: 'Événements', value: 'events' },
              { name: 'Commandes custom', value: 'custom_commands' },
              { name: 'RP', value: 'rp' },
              { name: 'Intégrations', value: 'integrations' },
            )))
    .addSubcommand((sub) =>
      sub.setName('disable').setDescription('Désactiver un module')
        .addStringOption((opt) =>
          opt.setName('nom').setDescription('Nom du module').setRequired(true)
            .addChoices(
              { name: 'Modération', value: 'moderation' },
              { name: 'Tickets', value: 'tickets' },
              { name: 'Logs', value: 'logs' },
              { name: 'Sécurité / Automod', value: 'security' },
              { name: 'Onboarding (welcome/goodbye)', value: 'onboarding' },
              { name: 'XP / Niveaux', value: 'xp' },
              { name: 'Économie', value: 'economy' },
              { name: 'Rôles (menus)', value: 'roles' },
              { name: 'Utilitaire', value: 'utility' },
              { name: 'Fun', value: 'fun' },
              { name: 'Tags / FAQ', value: 'tags' },
              { name: 'Annonces', value: 'announcements' },
              { name: 'Statistiques', value: 'stats' },
              { name: 'Salons vocaux temp.', value: 'tempvoice' },
              { name: 'Candidatures', value: 'applications' },
              { name: 'Événements', value: 'events' },
              { name: 'Commandes custom', value: 'custom_commands' },
              { name: 'RP', value: 'rp' },
              { name: 'Intégrations', value: 'integrations' },
            ))),

  async execute(interaction) {
    const guildId = interaction.guildId;
    const sub = interaction.options.getSubcommand();

    if (sub === 'list') {
      const modules = await configService.getModules(guildId);
      const sorted = Object.entries(modules).sort(([a], [b]) => a.localeCompare(b));

      const lines = sorted.map(([name, enabled]) => {
        const icon = enabled ? '✅' : '❌';
        return `${icon} **${name}**`;
      });

      const embed = new EmbedBuilder()
        .setTitle('📦 Modules du serveur')
        .setDescription(lines.join('\n'))
        .setColor(0x5865F2)
        .setFooter({ text: `${sorted.filter(([, e]) => e).length}/${sorted.length} activé(s)` })
        .setTimestamp();

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    if (sub === 'enable') {
      const name = interaction.options.getString('nom');
      await configService.setModule(guildId, name, true);
      return interaction.reply({
        content: `✅ Module **${name}** activé sur ce serveur.`,
        ephemeral: true,
      });
    }

    if (sub === 'disable') {
      const name = interaction.options.getString('nom');
      await configService.setModule(guildId, name, false);
      return interaction.reply({
        content: `❌ Module **${name}** désactivé sur ce serveur.`,
        ephemeral: true,
      });
    }
  },
};