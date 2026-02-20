// ===================================
// Ultra Suite — /setup
// Assistant de configuration guidé
// Active les modules essentiels et configure les channels
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ActionRowBuilder, StringSelectMenuBuilder, ChannelType } = require('discord.js');
const configService = require('../../core/configService');

// Presets par type de serveur
const PRESETS = {
  community: {
    label: 'Communauté',
    modules: ['moderation', 'logs', 'onboarding', 'xp', 'roles', 'utility', 'fun', 'stats'],
    description: 'Serveur communautaire classique avec XP, modération et rôles.',
  },
  gaming: {
    label: 'Gaming',
    modules: ['moderation', 'logs', 'onboarding', 'xp', 'economy', 'roles', 'utility', 'fun', 'tempvoice', 'stats'],
    description: 'Serveur gaming avec XP, économie, salons vocaux temporaires.',
  },
  rp: {
    label: 'Roleplay',
    modules: ['moderation', 'logs', 'onboarding', 'xp', 'economy', 'roles', 'rp', 'events', 'utility'],
    description: 'Serveur RP avec fiches personnages, économie et événements.',
  },
  business: {
    label: 'Professionnel',
    modules: ['moderation', 'logs', 'tickets', 'onboarding', 'tags', 'utility', 'announcements'],
    description: 'Serveur pro avec tickets, tags FAQ et annonces.',
  },
  school: {
    label: 'Éducation',
    modules: ['moderation', 'logs', 'onboarding', 'tickets', 'tags', 'roles', 'utility', 'announcements'],
    description: 'Serveur éducatif avec tickets, rôles et annonces.',
  },
};

module.exports = {
  module: 'admin',
  adminOnly: true,
  cooldown: 10,

  data: new SlashCommandBuilder()
    .setName('setup')
    .setDescription('Assistant de configuration guidé pour le serveur')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
    .addStringOption((opt) =>
      opt.setName('preset')
        .setDescription('Choisir un preset de configuration')
        .addChoices(
          { name: '🏘️ Communauté', value: 'community' },
          { name: '🎮 Gaming', value: 'gaming' },
          { name: '🎭 Roleplay', value: 'rp' },
          { name: '💼 Professionnel', value: 'business' },
          { name: '🎓 Éducation', value: 'school' },
        ))
    .addChannelOption((opt) =>
      opt.setName('logs')
        .setDescription('Channel pour les logs du bot')
        .addChannelTypes(ChannelType.GuildText))
    .addChannelOption((opt) =>
      opt.setName('welcome')
        .setDescription('Channel de bienvenue')
        .addChannelTypes(ChannelType.GuildText))
    .addChannelOption((opt) =>
      opt.setName('modlog')
        .setDescription('Channel pour les logs de modération')
        .addChannelTypes(ChannelType.GuildText)),

  async execute(interaction) {
    await interaction.deferReply({ ephemeral: true });

    const guildId = interaction.guildId;
    const preset = interaction.options.getString('preset');
    const logsChannel = interaction.options.getChannel('logs');
    const welcomeChannel = interaction.options.getChannel('welcome');
    const modLogChannel = interaction.options.getChannel('modlog');

    const changes = [];

    // 1. Appliquer le preset
    if (preset && PRESETS[preset]) {
      const p = PRESETS[preset];

      // Réinitialiser tous les modules à false d'abord
      const allModules = configService.AVAILABLE_MODULES;
      for (const mod of allModules) {
        await configService.setModule(guildId, mod, false);
      }

      // Activer les modules du preset
      for (const mod of p.modules) {
        await configService.setModule(guildId, mod, true);
      }

      changes.push(`📦 Preset **${p.label}** appliqué (${p.modules.length} modules activés)`);
    }

    // 2. Configurer les channels
    const configPatch = {};

    if (logsChannel) {
      configPatch.logChannel = logsChannel.id;
      changes.push(`📋 Channel logs → ${logsChannel}`);
    }

    if (welcomeChannel) {
      configPatch.welcomeChannel = welcomeChannel.id;
      configPatch.goodbyeChannel = welcomeChannel.id; // Même channel par défaut
      changes.push(`👋 Channel bienvenue → ${welcomeChannel}`);
    }

    if (modLogChannel) {
      configPatch.modLogChannel = modLogChannel.id;
      changes.push(`🔨 Channel logs modération → ${modLogChannel}`);
    }

    if (Object.keys(configPatch).length > 0) {
      await configService.set(guildId, configPatch);
    }

    // 3. Résumé
    if (changes.length === 0) {
      return interaction.editReply({
        content:
          '⚠️ Aucun paramètre spécifié.\n\n' +
          '**Utilisation :**\n' +
          '`/setup preset:Gaming logs:#logs welcome:#general`\n\n' +
          '**Presets disponibles :** Communauté, Gaming, Roleplay, Professionnel, Éducation',
      });
    }

    const embed = new EmbedBuilder()
      .setTitle('✅ Configuration appliquée')
      .setDescription(changes.join('\n'))
      .setColor(0x57F287)
      .setTimestamp();

    if (preset && PRESETS[preset]) {
      embed.addFields({
        name: 'Modules activés',
        value: PRESETS[preset].modules.map((m) => `\`${m}\``).join(', '),
        inline: false,
      });
    }

    embed.addFields({
      name: 'Prochaines étapes',
      value: [
        '• `/config view` — Voir la configuration complète',
        '• `/module list` — Voir les modules activés',
        '• `/config set` — Ajuster les paramètres individuels',
      ].join('\n'),
      inline: false,
    });

    return interaction.editReply({ embeds: [embed] });
  },
};