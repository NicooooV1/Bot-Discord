// ===================================
// Ultra Suite — /xpadmin
// Gérer le système d'XP
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'xp',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('xpadmin')
    .setDescription('Gérer le système d\'XP')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addSubcommand((s) =>
      s.setName('set')
        .setDescription('Définir l\'XP d\'un membre')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addIntegerOption((o) => o.setName('xp').setDescription('Quantité d\'XP').setRequired(true).setMinValue(0)),
    )
    .addSubcommand((s) =>
      s.setName('add')
        .setDescription('Ajouter de l\'XP à un membre')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addIntegerOption((o) => o.setName('xp').setDescription('Quantité d\'XP').setRequired(true).setMinValue(1)),
    )
    .addSubcommand((s) =>
      s.setName('remove')
        .setDescription('Retirer de l\'XP à un membre')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addIntegerOption((o) => o.setName('xp').setDescription('Quantité d\'XP').setRequired(true).setMinValue(1)),
    )
    .addSubcommand((s) =>
      s.setName('reset')
        .setDescription('Réinitialiser l\'XP d\'un membre ou du serveur')
        .addUserOption((o) => o.setName('membre').setDescription('Membre (vide = tout le serveur)')),
    )
    .addSubcommand((s) =>
      s.setName('setlevel')
        .setDescription('Définir le niveau d\'un membre')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addIntegerOption((o) => o.setName('niveau').setDescription('Niveau').setRequired(true).setMinValue(0)),
    )
    .addSubcommand((s) =>
      s.setName('multiplier')
        .setDescription('Définir un multiplicateur pour un rôle ou salon')
        .addNumberOption((o) => o.setName('valeur').setDescription('Multiplicateur (ex: 1.5)').setRequired(true).setMinValue(0.1).setMaxValue(10))
        .addRoleOption((o) => o.setName('role').setDescription('Rôle ciblé'))
        .addChannelOption((o) => o.setName('salon').setDescription('Salon ciblé')),
    )
    .addSubcommand((s) =>
      s.setName('blacklist')
        .setDescription('Blacklister un salon pour l\'XP')
        .addChannelOption((o) => o.setName('salon').setDescription('Salon à blacklister').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('rewards')
        .setDescription('Voir et gérer les récompenses de niveau')
        .addIntegerOption((o) => o.setName('niveau').setDescription('Niveau de la récompense'))
        .addRoleOption((o) => o.setName('role').setDescription('Rôle à donner'))
        .addStringOption((o) => o.setName('action').setDescription('Action').addChoices(
          { name: 'Ajouter', value: 'add' },
          { name: 'Supprimer', value: 'remove' },
          { name: 'Lister', value: 'list' },
        )),
    )
    .addSubcommand((s) =>
      s.setName('import')
        .setDescription('Importer des données XP (JSON)')
        .addAttachmentOption((o) => o.setName('fichier').setDescription('Fichier JSON').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('export')
        .setDescription('Exporter les données XP en JSON'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();
    const config = await configService.get(guildId);

    const xpForLevel = (level) => {
      const base = config.xp?.xpPerLevel || 100;
      return Math.floor(base * Math.pow(level, 1.5));
    };

    switch (sub) {
      case 'set': {
        const target = interaction.options.getUser('membre');
        const xp = interaction.options.getInteger('xp');
        await db('users').where({ guild_id: guildId, user_id: target.id })
          .update({ xp })
          .onConflict(['guild_id', 'user_id']).merge();
        return interaction.reply({ content: `✅ XP de **${target.username}** défini à **${xp}**.`, ephemeral: true });
      }

      case 'add': {
        const target = interaction.options.getUser('membre');
        const xp = interaction.options.getInteger('xp');
        await db('users')
          .insert({ guild_id: guildId, user_id: target.id, xp })
          .onConflict(['guild_id', 'user_id'])
          .merge({ xp: db.raw('xp + ?', [xp]) });
        return interaction.reply({ content: `✅ **+${xp} XP** ajouté à **${target.username}**.`, ephemeral: true });
      }

      case 'remove': {
        const target = interaction.options.getUser('membre');
        const xp = interaction.options.getInteger('xp');
        await db('users')
          .where({ guild_id: guildId, user_id: target.id })
          .update({ xp: db.raw('GREATEST(0, xp - ?)', [xp]) });
        return interaction.reply({ content: `✅ **-${xp} XP** retiré à **${target.username}**.`, ephemeral: true });
      }

      case 'reset': {
        const target = interaction.options.getUser('membre');
        if (target) {
          await db('users').where({ guild_id: guildId, user_id: target.id }).update({ xp: 0, level: 0 });
          return interaction.reply({ content: `✅ XP de **${target.username}** réinitialisé.`, ephemeral: true });
        }
        await db('users').where({ guild_id: guildId }).update({ xp: 0, level: 0 });
        return interaction.reply({ content: `✅ XP de **tout le serveur** réinitialisé.`, ephemeral: true });
      }

      case 'setlevel': {
        const target = interaction.options.getUser('membre');
        const level = interaction.options.getInteger('niveau');
        const requiredXp = xpForLevel(level);
        await db('users')
          .insert({ guild_id: guildId, user_id: target.id, level, xp: requiredXp })
          .onConflict(['guild_id', 'user_id'])
          .merge({ level, xp: requiredXp });
        return interaction.reply({ content: `✅ **${target.username}** est maintenant niveau **${level}**.`, ephemeral: true });
      }

      case 'multiplier': {
        const value = interaction.options.getNumber('valeur');
        const role = interaction.options.getRole('role');
        const channel = interaction.options.getChannel('salon');
        const xpConfig = config.xp || {};

        if (role) {
          const multipliers = xpConfig.roleMultipliers || {};
          multipliers[role.id] = value;
          await configService.update(guildId, { xp: { ...xpConfig, roleMultipliers: multipliers } });
          return interaction.reply({ content: `✅ Multiplicateur **x${value}** pour le rôle ${role}.`, ephemeral: true });
        }
        if (channel) {
          const multipliers = xpConfig.channelMultipliers || {};
          multipliers[channel.id] = value;
          await configService.update(guildId, { xp: { ...xpConfig, channelMultipliers: multipliers } });
          return interaction.reply({ content: `✅ Multiplicateur **x${value}** pour le salon ${channel}.`, ephemeral: true });
        }
        return interaction.reply({ content: '❌ Spécifiez un rôle ou un salon.', ephemeral: true });
      }

      case 'blacklist': {
        const channel = interaction.options.getChannel('salon');
        const xpConfig = config.xp || {};
        const list = xpConfig.blacklistedChannels || [];
        const idx = list.indexOf(channel.id);
        if (idx >= 0) {
          list.splice(idx, 1);
          await configService.update(guildId, { xp: { ...xpConfig, blacklistedChannels: list } });
          return interaction.reply({ content: `✅ ${channel} retiré de la blacklist XP.`, ephemeral: true });
        }
        list.push(channel.id);
        await configService.update(guildId, { xp: { ...xpConfig, blacklistedChannels: list } });
        return interaction.reply({ content: `✅ ${channel} ajouté à la blacklist XP.`, ephemeral: true });
      }

      case 'rewards': {
        const action = interaction.options.getString('action') || 'list';
        const level = interaction.options.getInteger('niveau');
        const role = interaction.options.getRole('role');
        const xpConfig = config.xp || {};
        const rewards = xpConfig.levelRewards || {};

        if (action === 'list') {
          const entries = Object.entries(rewards);
          if (!entries.length) return interaction.reply({ content: '📋 Aucune récompense configurée.', ephemeral: true });
          const embed = new EmbedBuilder()
            .setTitle('🎁 Récompenses de niveau')
            .setColor(0x9B59B6)
            .setDescription(entries.map(([lvl, roleId]) => `Niveau **${lvl}** → <@&${roleId}>`).join('\n'));
          return interaction.reply({ embeds: [embed], ephemeral: true });
        }

        if (!level || !role) return interaction.reply({ content: '❌ Spécifiez un niveau et un rôle.', ephemeral: true });

        if (action === 'add') {
          rewards[level] = role.id;
          await configService.update(guildId, { xp: { ...xpConfig, levelRewards: rewards } });
          return interaction.reply({ content: `✅ Récompense ajoutée : niveau **${level}** → ${role}`, ephemeral: true });
        }
        if (action === 'remove') {
          delete rewards[level];
          await configService.update(guildId, { xp: { ...xpConfig, levelRewards: rewards } });
          return interaction.reply({ content: `✅ Récompense retirée pour le niveau **${level}**.`, ephemeral: true });
        }
        break;
      }

      case 'export': {
        const users = await db('users').where('guild_id', guildId).select('user_id', 'xp', 'level');
        const jsonStr = JSON.stringify(users, null, 2);
        const buffer = Buffer.from(jsonStr, 'utf-8');
        return interaction.reply({
          content: '📤 Données XP exportées.',
          files: [{ attachment: buffer, name: `xp-export-${guildId}.json` }],
          ephemeral: true,
        });
      }

      case 'import': {
        const attachment = interaction.options.getAttachment('fichier');
        if (!attachment.name.endsWith('.json')) return interaction.reply({ content: '❌ Fichier JSON requis.', ephemeral: true });
        try {
          const response = await fetch(attachment.url);
          const data = await response.json();
          if (!Array.isArray(data)) return interaction.reply({ content: '❌ Format invalide.', ephemeral: true });
          await interaction.deferReply({ ephemeral: true });
          for (const entry of data) {
            if (entry.user_id && typeof entry.xp === 'number') {
              await db('users')
                .insert({ guild_id: guildId, user_id: entry.user_id, xp: entry.xp, level: entry.level || 0 })
                .onConflict(['guild_id', 'user_id'])
                .merge({ xp: entry.xp, level: entry.level || 0 });
            }
          }
          return interaction.editReply({ content: `✅ **${data.length}** entrées importées.` });
        } catch {
          return interaction.editReply({ content: '❌ Erreur lors de l\'import.' });
        }
      }
    }
  },
};
