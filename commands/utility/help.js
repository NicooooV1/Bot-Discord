// ===================================
// Ultra Suite — /help
// Aide dynamique — affiche les commandes disponibles
// Filtre par module activé sur le serveur
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, StringSelectMenuBuilder } = require('discord.js');
const configService = require('../../core/configService');

// Descriptions des modules
const MODULE_INFO = {
  admin: { emoji: '⚙️', label: 'Administration', description: 'Configuration du bot et des modules' },
  moderation: { emoji: '🔨', label: 'Modération', description: 'Ban, kick, warn, timeout, purge, lock' },
  tickets: { emoji: '🎫', label: 'Tickets', description: 'Système de support par tickets' },
  logs: { emoji: '📋', label: 'Logs', description: 'Journalisation des événements du serveur' },
  security: { emoji: '🔒', label: 'Sécurité', description: 'Automod, anti-spam, anti-raid' },
  onboarding: { emoji: '👋', label: 'Onboarding', description: 'Messages de bienvenue/au revoir, auto-rôle' },
  xp: { emoji: '⭐', label: 'XP / Niveaux', description: 'Système d\'expérience et classements' },
  economy: { emoji: '💰', label: 'Économie', description: 'Monnaie virtuelle, daily, transferts' },
  roles: { emoji: '🎭', label: 'Rôles', description: 'Menus de rôles en réaction' },
  utility: { emoji: '🔧', label: 'Utilitaire', description: 'Infos serveur/user, avatar, embed' },
  fun: { emoji: '🎮', label: 'Fun', description: 'Commandes amusantes' },
  stats: { emoji: '📊', label: 'Statistiques', description: 'Métriques et analyses du serveur' },
  tempvoice: { emoji: '🔊', label: 'Vocaux temporaires', description: 'Salons vocaux auto-créés' },
  tags: { emoji: '🏷️', label: 'Tags / FAQ', description: 'Réponses rapides prédéfinies' },
  announcements: { emoji: '📢', label: 'Annonces', description: 'Annonces planifiées' },
  applications: { emoji: '📝', label: 'Candidatures', description: 'Formulaires de candidature' },
  events: { emoji: '🎉', label: 'Événements', description: 'Gestion d\'événements serveur' },
  custom_commands: { emoji: '⚡', label: 'Commandes custom', description: 'Commandes personnalisées' },
  rp: { emoji: '🎭', label: 'RP', description: 'Outils de roleplay' },
  integrations: { emoji: '🔗', label: 'Intégrations', description: 'Intégrations tierces' },
};

module.exports = {
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('help')
    .setDescription('Afficher l\'aide et les commandes disponibles')
    .addStringOption((opt) =>
      opt.setName('module').setDescription('Voir l\'aide d\'un module spécifique')),

  async execute(interaction) {
    const specificModule = interaction.options.getString('module');
    const guildId = interaction.guildId;
    const modules = await configService.getModules(guildId);

    // Si un module est spécifié, afficher ses commandes
    if (specificModule) {
      return showModuleHelp(interaction, specificModule);
    }

    // Vue d'ensemble
    const enabledModules = Object.entries(modules)
      .filter(([, enabled]) => enabled)
      .map(([name]) => name);

    const disabledModules = Object.entries(modules)
      .filter(([, enabled]) => !enabled)
      .map(([name]) => name);

    // Modules activés
    const enabledLines = enabledModules.map((name) => {
      const info = MODULE_INFO[name] || { emoji: '📦', label: name };
      return `${info.emoji} **${info.label}** — ${info.description || ''}`;
    });

    // Admin toujours visible
    const adminInfo = MODULE_INFO.admin;
    const adminLine = `${adminInfo.emoji} **${adminInfo.label}** — ${adminInfo.description}`;

    const embed = new EmbedBuilder()
      .setTitle('📖 Ultra Suite — Aide')
      .setDescription(
        'Bienvenue ! Voici les modules activés sur ce serveur.\n' +
        'Utilisez `/help module:<nom>` pour voir les commandes d\'un module.\n\n' +
        `**Modules actifs (${enabledModules.length}) :**\n` +
        `${adminLine}\n` +
        (enabledLines.length > 0 ? enabledLines.join('\n') : '*Aucun module activé*')
      )
      .setColor(0x5865F2)
      .setTimestamp();

    if (disabledModules.length > 0) {
      embed.addFields({
        name: `❌ Modules désactivés (${disabledModules.length})`,
        value: disabledModules.map((n) => `\`${n}\``).join(', ') + '\n*Activez-les avec `/module enable`*',
        inline: false,
      });
    }

    embed.addFields({
      name: '🔗 Liens utiles',
      value: [
        '`/setup` — Configuration rapide guidée',
        '`/config view` — Voir la configuration',
        '`/module list` — État des modules',
      ].join('\n'),
      inline: false,
    });

    // Select menu pour naviguer
    const options = [{ label: 'Administration', value: 'admin', emoji: '⚙️' }];
    for (const name of enabledModules) {
      const info = MODULE_INFO[name];
      if (info && options.length < 25) {
        options.push({ label: info.label, value: name, emoji: info.emoji });
      }
    }

    const row = new ActionRowBuilder().addComponents(
      new StringSelectMenuBuilder()
        .setCustomId('help-module-select')
        .setPlaceholder('📖 Choisir un module pour voir ses commandes')
        .addOptions(options),
    );

    return interaction.reply({ embeds: [embed], components: [row], ephemeral: true });
  },
};

function showModuleHelp(interaction, moduleName) {
  const info = MODULE_INFO[moduleName] || { emoji: '📦', label: moduleName, description: '' };

  // Trouver les commandes de ce module
  const commands = interaction.client.commands?.filter((cmd) => {
    const cmdModule = cmd.module || 'utility';
    return cmdModule === moduleName;
  }) || [];

  const lines = commands.map((cmd) => {
    const name = cmd.data?.name || '?';
    const desc = cmd.data?.description || 'Pas de description';
    return `\`/${name}\` — ${desc}`;
  });

  const embed = new EmbedBuilder()
    .setTitle(`${info.emoji} ${info.label}`)
    .setDescription(
      `${info.description}\n\n` +
      (lines.length > 0
        ? `**Commandes (${lines.length}) :**\n${lines.join('\n')}`
        : '*Aucune commande enregistrée pour ce module.*')
    )
    .setColor(0x5865F2)
    .setFooter({ text: 'Utilisez /help pour revenir à la vue d\'ensemble' })
    .setTimestamp();

  return interaction.reply({ embeds: [embed], ephemeral: true });
}