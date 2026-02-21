// ===================================
// Ultra Suite — /automod
// Configuration AutoMod avancée
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'security',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('automod')
    .setDescription('Configurer l\'AutoMod')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addSubcommand((s) =>
      s.setName('status').setDescription('Voir la configuration actuelle'),
    )
    .addSubcommand((s) =>
      s.setName('toggle').setDescription('Activer/désactiver un filtre')
        .addStringOption((o) => o.setName('filtre').setDescription('Filtre').setRequired(true).addChoices(
          { name: 'Anti-invitations', value: 'antiInvite' },
          { name: 'Anti-liens', value: 'antiLink' },
          { name: 'Anti-spam', value: 'antiSpam' },
          { name: 'Anti-majuscules', value: 'antiCaps' },
          { name: 'Anti-emojis', value: 'antiEmoji' },
          { name: 'Anti-mentions', value: 'antiMention' },
          { name: 'Anti-newlines', value: 'antiNewline' },
          { name: 'Anti-zalgo', value: 'antiZalgo' },
          { name: 'Anti-phishing', value: 'antiPhishing' },
          { name: 'Anti-publicité', value: 'antiAd' },
          { name: 'Filtre NSFW', value: 'nsfwFilter' },
          { name: 'Mots interdits', value: 'wordFilter' },
          { name: 'Toxicité IA', value: 'aiToxicity' },
        )),
    )
    .addSubcommand((s) =>
      s.setName('action').setDescription('Définir l\'action d\'un filtre')
        .addStringOption((o) => o.setName('filtre').setDescription('Filtre').setRequired(true).addChoices(
          { name: 'Anti-invitations', value: 'antiInvite' },
          { name: 'Anti-liens', value: 'antiLink' },
          { name: 'Anti-spam', value: 'antiSpam' },
          { name: 'Anti-majuscules', value: 'antiCaps' },
          { name: 'Anti-emojis', value: 'antiEmoji' },
          { name: 'Anti-mentions', value: 'antiMention' },
          { name: 'Mots interdits', value: 'wordFilter' },
        ))
        .addStringOption((o) => o.setName('action').setDescription('Action').setRequired(true).addChoices(
          { name: 'Supprimer', value: 'delete' },
          { name: 'Avertir', value: 'warn' },
          { name: 'Timeout (5min)', value: 'timeout_5' },
          { name: 'Timeout (30min)', value: 'timeout_30' },
          { name: 'Timeout (1h)', value: 'timeout_60' },
          { name: 'Kick', value: 'kick' },
          { name: 'Ban', value: 'ban' },
        )),
    )
    .addSubcommand((s) =>
      s.setName('threshold').setDescription('Configurer le seuil d\'un filtre')
        .addStringOption((o) => o.setName('filtre').setDescription('Filtre').setRequired(true).addChoices(
          { name: 'Anti-spam (msg/5s)', value: 'antiSpam' },
          { name: 'Anti-majuscules (%)', value: 'antiCaps' },
          { name: 'Anti-emojis (max)', value: 'antiEmoji' },
          { name: 'Anti-mentions (max)', value: 'antiMention' },
          { name: 'Anti-newlines (max)', value: 'antiNewline' },
        ))
        .addIntegerOption((o) => o.setName('valeur').setDescription('Seuil').setRequired(true).setMinValue(1)),
    )
    .addSubcommand((s) =>
      s.setName('whitelist').setDescription('Ajouter/retirer un rôle ou salon de la whitelist')
        .addRoleOption((o) => o.setName('role').setDescription('Rôle'))
        .addChannelOption((o) => o.setName('salon').setDescription('Salon')),
    )
    .addSubcommand((s) =>
      s.setName('words').setDescription('Gérer les mots interdits')
        .addStringOption((o) => o.setName('action').setDescription('Action').setRequired(true).addChoices(
          { name: 'Ajouter', value: 'add' },
          { name: 'Supprimer', value: 'remove' },
          { name: 'Lister', value: 'list' },
          { name: 'Importer', value: 'import' },
        ))
        .addStringOption((o) => o.setName('mot').setDescription('Mot ou expression (séparé par virgules pour multiples)')),
    )
    .addSubcommand((s) =>
      s.setName('linkwhitelist').setDescription('Gérer les domaines autorisés')
        .addStringOption((o) => o.setName('action').setDescription('Action').setRequired(true).addChoices(
          { name: 'Ajouter', value: 'add' },
          { name: 'Supprimer', value: 'remove' },
          { name: 'Lister', value: 'list' },
        ))
        .addStringOption((o) => o.setName('domaine').setDescription('Nom de domaine (ex: youtube.com)')),
    )
    .addSubcommand((s) =>
      s.setName('learning').setDescription('Activer/désactiver le mode apprentissage'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const config = await configService.get(guildId);
    const automod = config.security?.automod || {};

    const filterLabels = {
      antiInvite: '🔗 Anti-invitations',
      antiLink: '🌐 Anti-liens',
      antiSpam: '💬 Anti-spam',
      antiCaps: '🔠 Anti-majuscules',
      antiEmoji: '😀 Anti-emojis',
      antiMention: '📢 Anti-mentions',
      antiNewline: '📜 Anti-newlines',
      antiZalgo: '🔣 Anti-zalgo',
      antiPhishing: '🎣 Anti-phishing',
      antiAd: '📺 Anti-publicité',
      nsfwFilter: '🔞 Filtre NSFW',
      wordFilter: '🚫 Mots interdits',
      aiToxicity: '🤖 Toxicité IA',
    };

    switch (sub) {
      case 'status': {
        const embed = new EmbedBuilder()
          .setTitle('🛡️ Configuration AutoMod')
          .setColor(0x3498DB)
          .setDescription(Object.entries(filterLabels).map(([key, label]) => {
            const enabled = automod[key]?.enabled ?? false;
            const action = automod[key]?.action || 'delete';
            const threshold = automod[key]?.threshold;
            return `${enabled ? '✅' : '❌'} ${label} — Action: \`${action}\`${threshold ? ` — Seuil: ${threshold}` : ''}`;
          }).join('\n'));

        const whitelist = automod.whitelistedRoles || [];
        const whitelistChannels = automod.whitelistedChannels || [];
        if (whitelist.length) embed.addFields({ name: '🔓 Rôles whitelistés', value: whitelist.map((r) => `<@&${r}>`).join(', ') });
        if (whitelistChannels.length) embed.addFields({ name: '🔓 Salons whitelistés', value: whitelistChannels.map((c) => `<#${c}>`).join(', ') });
        if (automod.learningMode) embed.addFields({ name: '📚 Mode apprentissage', value: 'Activé — les actions ne sont pas appliquées, seulement loggées' });

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'toggle': {
        const filter = interaction.options.getString('filtre');
        const current = automod[filter]?.enabled ?? false;
        automod[filter] = { ...(automod[filter] || {}), enabled: !current };
        await configService.update(guildId, { security: { ...config.security, automod } });
        return interaction.reply({
          content: `${!current ? '✅' : '❌'} ${filterLabels[filter]} ${!current ? 'activé' : 'désactivé'}.`,
          ephemeral: true,
        });
      }

      case 'action': {
        const filter = interaction.options.getString('filtre');
        const action = interaction.options.getString('action');
        automod[filter] = { ...(automod[filter] || {}), action };
        await configService.update(guildId, { security: { ...config.security, automod } });
        return interaction.reply({
          content: `✅ Action pour ${filterLabels[filter]} : \`${action}\`.`,
          ephemeral: true,
        });
      }

      case 'threshold': {
        const filter = interaction.options.getString('filtre');
        const value = interaction.options.getInteger('valeur');
        automod[filter] = { ...(automod[filter] || {}), threshold: value };
        await configService.update(guildId, { security: { ...config.security, automod } });
        return interaction.reply({
          content: `✅ Seuil pour ${filterLabels[filter]} : **${value}**.`,
          ephemeral: true,
        });
      }

      case 'whitelist': {
        const role = interaction.options.getRole('role');
        const channel = interaction.options.getChannel('salon');
        if (!role && !channel) return interaction.reply({ content: '❌ Spécifiez un rôle ou un salon.', ephemeral: true });

        if (role) {
          const list = automod.whitelistedRoles || [];
          const idx = list.indexOf(role.id);
          if (idx >= 0) { list.splice(idx, 1); } else { list.push(role.id); }
          automod.whitelistedRoles = list;
          await configService.update(guildId, { security: { ...config.security, automod } });
          return interaction.reply({ content: `✅ Rôle ${role} ${idx >= 0 ? 'retiré de' : 'ajouté à'} la whitelist.`, ephemeral: true });
        }

        if (channel) {
          const list = automod.whitelistedChannels || [];
          const idx = list.indexOf(channel.id);
          if (idx >= 0) { list.splice(idx, 1); } else { list.push(channel.id); }
          automod.whitelistedChannels = list;
          await configService.update(guildId, { security: { ...config.security, automod } });
          return interaction.reply({ content: `✅ Salon ${channel} ${idx >= 0 ? 'retiré de' : 'ajouté à'} la whitelist.`, ephemeral: true });
        }
        break;
      }

      case 'words': {
        const action = interaction.options.getString('action');
        const word = interaction.options.getString('mot');
        const words = automod.blockedWords || [];

        if (action === 'list') {
          if (!words.length) return interaction.reply({ content: '📋 Aucun mot interdit configuré.', ephemeral: true });
          return interaction.reply({ content: `🚫 Mots interdits (${words.length}) :\n\`\`\`\n${words.join(', ')}\n\`\`\``, ephemeral: true });
        }
        if (!word) return interaction.reply({ content: '❌ Spécifiez un mot.', ephemeral: true });

        const newWords = word.split(',').map((w) => w.trim().toLowerCase()).filter(Boolean);

        if (action === 'add') {
          for (const w of newWords) { if (!words.includes(w)) words.push(w); }
          automod.blockedWords = words;
          await configService.update(guildId, { security: { ...config.security, automod } });
          return interaction.reply({ content: `✅ ${newWords.length} mot(s) ajouté(s). Total: ${words.length}`, ephemeral: true });
        }
        if (action === 'remove') {
          automod.blockedWords = words.filter((w) => !newWords.includes(w));
          await configService.update(guildId, { security: { ...config.security, automod } });
          return interaction.reply({ content: `✅ ${newWords.length} mot(s) retiré(s).`, ephemeral: true });
        }
        if (action === 'import') {
          // Expects comma-separated list in the 'mot' field
          automod.blockedWords = [...new Set([...words, ...newWords])];
          await configService.update(guildId, { security: { ...config.security, automod } });
          return interaction.reply({ content: `✅ ${newWords.length} mot(s) importé(s). Total: ${automod.blockedWords.length}`, ephemeral: true });
        }
        break;
      }

      case 'linkwhitelist': {
        const action = interaction.options.getString('action');
        const domain = interaction.options.getString('domaine');
        const domains = automod.whitelistedDomains || [];

        if (action === 'list') {
          if (!domains.length) return interaction.reply({ content: '📋 Aucun domaine whitelisté.', ephemeral: true });
          return interaction.reply({ content: `🌐 Domaines autorisés :\n${domains.map((d) => `\`${d}\``).join(', ')}`, ephemeral: true });
        }
        if (!domain) return interaction.reply({ content: '❌ Spécifiez un domaine.', ephemeral: true });

        if (action === 'add') {
          if (!domains.includes(domain.toLowerCase())) domains.push(domain.toLowerCase());
          automod.whitelistedDomains = domains;
          await configService.update(guildId, { security: { ...config.security, automod } });
          return interaction.reply({ content: `✅ Domaine \`${domain}\` ajouté.`, ephemeral: true });
        }
        if (action === 'remove') {
          automod.whitelistedDomains = domains.filter((d) => d !== domain.toLowerCase());
          await configService.update(guildId, { security: { ...config.security, automod } });
          return interaction.reply({ content: `✅ Domaine \`${domain}\` retiré.`, ephemeral: true });
        }
        break;
      }

      case 'learning': {
        automod.learningMode = !automod.learningMode;
        await configService.update(guildId, { security: { ...config.security, automod } });
        return interaction.reply({
          content: automod.learningMode
            ? '📚 Mode apprentissage **activé**. Les actions ne sont pas appliquées, seulement loggées.'
            : '🛡️ Mode apprentissage **désactivé**. Les actions seront appliquées.',
          ephemeral: true,
        });
      }
    }
  },
};
