const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder, ChannelType } = require('discord.js');
const { getGuildConfig, updateGuildConfig } = require('../../utils/database');
const { COLORS } = require('../../utils/logger');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('setup')
    .setDescription('⚙️ Configurer le bot')
    .addSubcommand(sub =>
      sub.setName('logs')
        .setDescription('Définir le salon de logs')
        .addChannelOption(opt =>
          opt.setName('salon')
            .setDescription('Le salon pour les logs')
            .addChannelTypes(ChannelType.GuildText)
            .setRequired(true)
        )
    )
    .addSubcommand(sub =>
      sub.setName('welcome')
        .setDescription('Définir le salon de bienvenue')
        .addChannelOption(opt =>
          opt.setName('salon')
            .setDescription('Le salon pour les messages de bienvenue')
            .addChannelTypes(ChannelType.GuildText)
            .setRequired(true)
        )
    )
    .addSubcommand(sub =>
      sub.setName('welcome-message')
        .setDescription('Personnaliser le message de bienvenue')
        .addStringOption(opt =>
          opt.setName('message')
            .setDescription('Message ({user}, {server}, {memberCount})')
            .setRequired(true)
        )
    )
    .addSubcommand(sub =>
      sub.setName('leave-message')
        .setDescription('Personnaliser le message de départ')
        .addStringOption(opt =>
          opt.setName('message')
            .setDescription('Message ({user}, {server}, {memberCount})')
            .setRequired(true)
        )
    )
    .addSubcommand(sub =>
      sub.setName('ticket-category')
        .setDescription('Définir la catégorie pour les tickets')
        .addChannelOption(opt =>
          opt.setName('catégorie')
            .setDescription('La catégorie pour créer les tickets')
            .addChannelTypes(ChannelType.GuildCategory)
            .setRequired(true)
        )
    )
    .addSubcommand(sub =>
      sub.setName('ticket-logs')
        .setDescription('Définir le salon de logs des tickets')
        .addChannelOption(opt =>
          opt.setName('salon')
            .setDescription('Le salon pour les logs des tickets')
            .addChannelTypes(ChannelType.GuildText)
            .setRequired(true)
        )
    )
    .addSubcommand(sub =>
      sub.setName('mod-role')
        .setDescription('Définir le rôle modérateur')
        .addRoleOption(opt =>
          opt.setName('rôle')
            .setDescription('Le rôle modérateur')
            .setRequired(true)
        )
    )
    .addSubcommand(sub =>
      sub.setName('antispam')
        .setDescription('Activer/Désactiver l\'anti-spam automatique')
        .addBooleanOption(opt =>
          opt.setName('activer')
            .setDescription('Activer ou désactiver l\'anti-spam')
            .setRequired(true)
        )
    )
    .addSubcommand(sub =>
      sub.setName('view')
        .setDescription('Voir la configuration actuelle')
    )
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guild.id;

    if (sub === 'view') {
      return handleView(interaction);
    }

    const configMap = {
      'logs': { key: 'log_channel_id', get: () => interaction.options.getChannel('salon').id, label: 'Salon de logs' },
      'welcome': { key: 'welcome_channel_id', get: () => interaction.options.getChannel('salon').id, label: 'Salon de bienvenue' },
      'welcome-message': { key: 'welcome_message', get: () => interaction.options.getString('message'), label: 'Message de bienvenue' },
      'leave-message': { key: 'leave_message', get: () => interaction.options.getString('message'), label: 'Message de départ' },
      'ticket-category': { key: 'ticket_category_id', get: () => interaction.options.getChannel('catégorie').id, label: 'Catégorie des tickets' },
      'ticket-logs': { key: 'ticket_log_channel_id', get: () => interaction.options.getChannel('salon').id, label: 'Salon de logs des tickets' },
      'mod-role': { key: 'mod_role_id', get: () => interaction.options.getRole('rôle').id, label: 'Rôle modérateur' },
      'antispam': { key: 'antispam_enabled', get: () => interaction.options.getBoolean('activer') ? 1 : 0, label: 'Anti-spam' },
    };

    const config = configMap[sub];
    if (!config) return;

    const value = config.get();
    updateGuildConfig(guildId, config.key, value);

    const embed = new EmbedBuilder()
      .setTitle('⚙️ Configuration mise à jour')
      .setColor(COLORS.GREEN)
      .setDescription(`**${config.label}** a été configuré avec succès.`)
      .setTimestamp();

    await interaction.reply({ embeds: [embed], ephemeral: true });
  },
};

async function handleView(interaction) {
  const config = getGuildConfig(interaction.guild.id);

  const formatChannel = (id) => id ? `<#${id}>` : '`Non défini`';
  const formatRole = (id) => id ? `<@&${id}>` : '`Non défini`';

  const embed = new EmbedBuilder()
    .setTitle('⚙️ Configuration du bot')
    .setColor(COLORS.BLUE)
    .addFields(
      { name: '📋 Salon de logs', value: formatChannel(config.log_channel_id), inline: true },
      { name: '👋 Salon de bienvenue', value: formatChannel(config.welcome_channel_id), inline: true },
      { name: '🛡️ Rôle modérateur', value: formatRole(config.mod_role_id), inline: true },
      { name: '🎫 Catégorie tickets', value: formatChannel(config.ticket_category_id), inline: true },
      { name: '📝 Logs tickets', value: formatChannel(config.ticket_log_channel_id), inline: true },
      { name: '🛡️ Anti-spam', value: config.antispam_enabled ? '✅ Activé' : '❌ Désactivé', inline: true },
      { name: '👋 Message de bienvenue', value: `\`\`\`${config.welcome_message}\`\`\`` },
      { name: '👋 Message de départ', value: `\`\`\`${config.leave_message}\`\`\`` },
    )
    .setFooter({ text: 'Variables disponibles: {user}, {server}, {memberCount}' })
    .setTimestamp();

  await interaction.reply({ embeds: [embed], ephemeral: true });
}
