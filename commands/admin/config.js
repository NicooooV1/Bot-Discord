// ===================================
// Ultra Suite — /config
// Voir et modifier la configuration du serveur
// /config view | set <clé> <valeur> | reset
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ChannelType } = require('discord.js');
const configService = require('../../core/configService');

module.exports = {
  module: 'admin',
  adminOnly: true,
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('config')
    .setDescription('Configurer le bot pour ce serveur')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
    .addSubcommand((sub) =>
      sub.setName('view').setDescription('Voir la configuration actuelle'))
    .addSubcommand((sub) =>
      sub.setName('set').setDescription('Modifier un paramètre')
        .addStringOption((opt) =>
          opt.setName('clé').setDescription('Paramètre à modifier').setRequired(true)
            .addChoices(
              { name: 'Locale (fr/en)', value: 'locale' },
              { name: 'Channel de logs', value: 'logChannel' },
              { name: 'Channel de logs modération', value: 'modLogChannel' },
              { name: 'Channel de bienvenue', value: 'welcomeChannel' },
              { name: 'Message de bienvenue', value: 'welcomeMessage' },
              { name: 'Rôle de bienvenue', value: 'welcomeRole' },
              { name: 'Channel d\'au revoir', value: 'goodbyeChannel' },
              { name: 'Message d\'au revoir', value: 'goodbyeMessage' },
              { name: 'Catégorie tickets', value: 'ticketCategory' },
              { name: 'Channel logs tickets', value: 'ticketLogChannel' },
              { name: 'Rôle staff tickets', value: 'ticketStaffRole' },
              { name: 'Rôle mute', value: 'muteRole' },
              { name: 'Lobby vocal temp.', value: 'tempVoiceLobby' },
              { name: 'Catégorie vocaux temp.', value: 'tempVoiceCategory' },
            ))
        .addStringOption((opt) =>
          opt.setName('valeur').setDescription('Nouvelle valeur (ID ou texte)').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('reset').setDescription('Réinitialiser toute la configuration aux valeurs par défaut')),

  async execute(interaction) {
    const guildId = interaction.guildId;
    const sub = interaction.options.getSubcommand();

    // === VIEW ===
    if (sub === 'view') {
      const config = await configService.get(guildId);

      const channelOrNone = (id) => id ? `<#${id}>` : '*Non défini*';
      const roleOrNone = (id) => id ? `<@&${id}>` : '*Non défini*';
      const textOrNone = (val) => val || '*Non défini*';

      const embed = new EmbedBuilder()
        .setTitle('⚙️ Configuration du serveur')
        .setColor(0x5865F2)
        .addFields(
          { name: '🌐 Général', value: [
            `**Locale :** ${config.locale}`,
          ].join('\n'), inline: false },
          { name: '📋 Logs', value: [
            `**Logs :** ${channelOrNone(config.logChannel)}`,
            `**Logs modération :** ${channelOrNone(config.modLogChannel)}`,
          ].join('\n'), inline: true },
          { name: '👋 Onboarding', value: [
            `**Bienvenue :** ${channelOrNone(config.welcomeChannel)}`,
            `**Au revoir :** ${channelOrNone(config.goodbyeChannel)}`,
            `**Rôle auto :** ${roleOrNone(config.welcomeRole)}`,
          ].join('\n'), inline: true },
          { name: '🎫 Tickets', value: [
            `**Catégorie :** ${channelOrNone(config.ticketCategory)}`,
            `**Logs :** ${channelOrNone(config.ticketLogChannel)}`,
            `**Rôle staff :** ${roleOrNone(config.ticketStaffRole)}`,
            `**Max/user :** ${config.maxTicketsPerUser}`,
          ].join('\n'), inline: true },
          { name: '🔒 Automod', value: [
            `**Activé :** ${config.automod?.enabled ? '✅' : '❌'}`,
            `**Anti-spam :** ${config.automod?.antiSpam ? '✅' : '❌'}`,
            `**Anti-link :** ${config.automod?.antiLink ? '✅' : '❌'}`,
            `**Anti-mention :** ${config.automod?.antiMention ? '✅' : '❌'}`,
            `**Max warns :** ${config.automod?.maxWarns}`,
          ].join('\n'), inline: true },
          { name: '⭐ XP', value: [
            `**Activé :** ${config.xp?.enabled ? '✅' : '❌'}`,
            `**XP/msg :** ${config.xp?.min}-${config.xp?.max}`,
            `**Cooldown :** ${config.xp?.cooldown}s`,
            `**Channel LvlUp :** ${channelOrNone(config.xp?.levelUpChannel)}`,
          ].join('\n'), inline: true },
          { name: '💰 Économie', value: [
            `**Activé :** ${config.economy?.enabled ? '✅' : '❌'}`,
            `**Monnaie :** ${config.economy?.currencyName} (${config.economy?.currencySymbol})`,
            `**Daily :** ${config.economy?.dailyAmount}`,
            `**Weekly :** ${config.economy?.weeklyAmount}`,
          ].join('\n'), inline: true },
          { name: '🔊 Salons vocaux temp.', value: [
            `**Lobby :** ${channelOrNone(config.tempVoiceLobby)}`,
            `**Catégorie :** ${channelOrNone(config.tempVoiceCategory)}`,
          ].join('\n'), inline: true },
        )
        .setTimestamp();

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    // === SET ===
    if (sub === 'set') {
      const key = interaction.options.getString('clé');
      let value = interaction.options.getString('valeur');

      // Nettoyer les mentions Discord pour extraire l'ID
      value = value.replace(/[<#@&!>]/g, '').trim();

      // "none" ou "null" → null
      if (['none', 'null', 'aucun', 'reset'].includes(value.toLowerCase())) {
        value = null;
      }

      await configService.set(guildId, { [key]: value });

      const display = value === null ? '*Réinitialisé*' : `\`${value}\``;
      return interaction.reply({
        content: `✅ **${key}** mis à jour → ${display}`,
        ephemeral: true,
      });
    }

    // === RESET ===
    if (sub === 'reset') {
      await configService.reset(guildId);
      return interaction.reply({
        content: '✅ Configuration réinitialisée aux valeurs par défaut.\n⚠️ Les modules restent inchangés. Utilisez `/module` pour les gérer.',
        ephemeral: true,
      });
    }
  },
};