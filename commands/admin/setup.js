// ===================================
// Ultra Suite — Admin: /setup
// Configuration interactive du bot
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits, ChannelType, StringSelectMenuBuilder, ActionRowBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { successEmbed, errorEmbed, createEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'admin',
  data: new SlashCommandBuilder()
    .setName('setup')
    .setDescription('Configure le bot pour ce serveur')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
    .addSubcommand((sub) =>
      sub
        .setName('module')
        .setDescription('Active ou désactive un module')
        .addStringOption((opt) =>
          opt
            .setName('nom')
            .setDescription('Nom du module')
            .setRequired(true)
            .addChoices(
              { name: 'Modération', value: 'moderation' },
              { name: 'Logs', value: 'logs' },
              { name: 'Sécurité', value: 'security' },
              { name: 'Onboarding', value: 'onboarding' },
              { name: 'Rôles', value: 'roles' },
              { name: 'Tickets', value: 'tickets' },
              { name: 'XP & Niveaux', value: 'xp' },
              { name: 'Économie', value: 'economy' },
              { name: 'Utilitaire', value: 'utility' },
              { name: 'Fun', value: 'fun' },
              { name: 'Musique', value: 'music' },
              { name: 'Vocal Temporaire', value: 'tempvoice' },
              { name: 'Candidatures', value: 'applications' },
              { name: 'Tags/FAQ', value: 'tags' },
              { name: 'Événements', value: 'events' },
              { name: 'RP', value: 'rp' },
              { name: 'Intégrations', value: 'integrations' },
              { name: 'Annonces', value: 'announcements' },
              { name: 'Statistiques', value: 'stats' }
            )
        )
        .addBooleanOption((opt) =>
          opt.setName('actif').setDescription('Activer ou désactiver').setRequired(true)
        )
    )
    .addSubcommand((sub) =>
      sub
        .setName('logs')
        .setDescription('Définit le salon de logs')
        .addChannelOption((opt) =>
          opt
            .setName('salon')
            .setDescription('Salon de logs')
            .addChannelTypes(ChannelType.GuildText)
            .setRequired(true)
        )
    )
    .addSubcommand((sub) =>
      sub
        .setName('modlogs')
        .setDescription('Définit le salon de logs de modération')
        .addChannelOption((opt) =>
          opt
            .setName('salon')
            .setDescription('Salon de modlogs')
            .addChannelTypes(ChannelType.GuildText)
            .setRequired(true)
        )
    )
    .addSubcommand((sub) =>
      sub
        .setName('welcome')
        .setDescription('Configure le message de bienvenue')
        .addChannelOption((opt) =>
          opt
            .setName('salon')
            .setDescription('Salon de bienvenue')
            .addChannelTypes(ChannelType.GuildText)
            .setRequired(true)
        )
        .addStringOption((opt) =>
          opt.setName('message').setDescription('Message ({{user}}, {{guild}}, {{count}})').setRequired(false)
        )
        .addRoleOption((opt) =>
          opt.setName('role').setDescription('Rôle à donner automatiquement').setRequired(false)
        )
    )
    .addSubcommand((sub) =>
      sub
        .setName('tickets')
        .setDescription('Configure le système de tickets')
        .addChannelOption((opt) =>
          opt
            .setName('categorie')
            .setDescription('Catégorie pour les tickets')
            .addChannelTypes(ChannelType.GuildCategory)
            .setRequired(true)
        )
        .addChannelOption((opt) =>
          opt
            .setName('logs')
            .setDescription('Salon de logs tickets')
            .addChannelTypes(ChannelType.GuildText)
            .setRequired(false)
        )
        .addRoleOption((opt) =>
          opt.setName('staff').setDescription('Rôle staff pour les tickets').setRequired(false)
        )
    )
    .addSubcommand((sub) =>
      sub
        .setName('muterole')
        .setDescription('Définit le rôle mute')
        .addRoleOption((opt) =>
          opt.setName('role').setDescription('Rôle mute').setRequired(true)
        )
    )
    .addSubcommand((sub) =>
      sub
        .setName('view')
        .setDescription('Affiche la configuration actuelle')
    )
    .addSubcommand((sub) =>
      sub
        .setName('reset')
        .setDescription('Réinitialise toute la configuration')
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();

    switch (sub) {
      case 'module': {
        const name = interaction.options.getString('nom');
        const enabled = interaction.options.getBoolean('actif');
        await configService.setModule(interaction.guild.id, name, enabled);
        const key = enabled ? 'admin.setup.module_enabled' : 'admin.setup.module_disabled';
        return interaction.reply({ embeds: [successEmbed(t(key, undefined, { module: name }))], ephemeral: true });
      }

      case 'logs': {
        const channel = interaction.options.getChannel('salon');
        await configService.set(interaction.guild.id, { logChannel: channel.id });
        return interaction.reply({
          embeds: [successEmbed(t('admin.setup.config_updated', undefined, { key: 'logChannel', value: channel.toString() }))],
          ephemeral: true,
        });
      }

      case 'modlogs': {
        const channel = interaction.options.getChannel('salon');
        await configService.set(interaction.guild.id, { modLogChannel: channel.id });
        return interaction.reply({
          embeds: [successEmbed(t('admin.setup.config_updated', undefined, { key: 'modLogChannel', value: channel.toString() }))],
          ephemeral: true,
        });
      }

      case 'welcome': {
        const channel = interaction.options.getChannel('salon');
        const message = interaction.options.getString('message');
        const role = interaction.options.getRole('role');

        const patch = { welcomeChannel: channel.id };
        if (message) patch.welcomeMessage = message;
        if (role) patch.welcomeRole = role.id;

        await configService.set(interaction.guild.id, patch);
        return interaction.reply({
          embeds: [successEmbed(`✅ Bienvenue configuré dans ${channel}`)],
          ephemeral: true,
        });
      }

      case 'tickets': {
        const category = interaction.options.getChannel('categorie');
        const logsChannel = interaction.options.getChannel('logs');
        const staff = interaction.options.getRole('staff');

        const patch = { ticketCategory: category.id };
        if (logsChannel) patch.ticketLogChannel = logsChannel.id;
        if (staff) patch.ticketStaffRole = staff.id;

        await configService.set(interaction.guild.id, patch);
        return interaction.reply({
          embeds: [successEmbed(`✅ Tickets configurés dans ${category}`)],
          ephemeral: true,
        });
      }

      case 'muterole': {
        const role = interaction.options.getRole('role');
        await configService.set(interaction.guild.id, { muteRole: role.id });
        return interaction.reply({
          embeds: [successEmbed(t('admin.setup.config_updated', undefined, { key: 'muteRole', value: role.toString() }))],
          ephemeral: true,
        });
      }

      case 'view': {
        const config = await configService.get(interaction.guild.id);
        const modules = await configService.getModules(interaction.guild.id);

        const embed = createEmbed('primary')
          .setTitle(`⚙️ Configuration — ${interaction.guild.name}`)
          .addFields(
            {
              name: '📋 Modules activés',
              value: Object.entries(modules)
                .map(([k, v]) => `${v ? '✅' : '❌'} ${k}`)
                .join('\n') || 'Aucun module activé',
            },
            {
              name: '📝 Logs',
              value: `Logs : ${config.logChannel ? `<#${config.logChannel}>` : 'Non défini'}\nModLogs : ${config.modLogChannel ? `<#${config.modLogChannel}>` : 'Non défini'}`,
              inline: true,
            },
            {
              name: '👋 Bienvenue',
              value: `Salon : ${config.welcomeChannel ? `<#${config.welcomeChannel}>` : 'Non défini'}\nRôle : ${config.welcomeRole ? `<@&${config.welcomeRole}>` : 'Non défini'}`,
              inline: true,
            },
            {
              name: '🎫 Tickets',
              value: `Catégorie : ${config.ticketCategory ? `<#${config.ticketCategory}>` : 'Non défini'}\nStaff : ${config.ticketStaffRole ? `<@&${config.ticketStaffRole}>` : 'Non défini'}`,
              inline: true,
            },
            {
              name: '🔇 Mute Role',
              value: config.muteRole ? `<@&${config.muteRole}>` : 'Non défini',
              inline: true,
            }
          );

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'reset': {
        await configService.set(interaction.guild.id, configService.DEFAULT_CONFIG);
        return interaction.reply({ embeds: [successEmbed(t('admin.setup.config_reset'))], ephemeral: true });
      }
    }
  },
};
