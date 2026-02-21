// ===================================
// Ultra Suite — /antinuke
// Système anti-nuke
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'antinuke',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('antinuke')
    .setDescription('Système anti-nuke')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
    .addSubcommand((s) => s.setName('enable').setDescription('Activer l\'anti-nuke'))
    .addSubcommand((s) => s.setName('disable').setDescription('Désactiver l\'anti-nuke'))
    .addSubcommand((s) => s.setName('status').setDescription('Voir le statut'))
    .addSubcommand((s) => s.setName('whitelist').setDescription('Gérer la whitelist')
      .addStringOption((o) => o.setName('action').setDescription('Action').setRequired(true).addChoices(
        { name: 'Ajouter', value: 'add' },
        { name: 'Retirer', value: 'remove' },
        { name: 'Liste', value: 'list' },
      ))
      .addUserOption((o) => o.setName('utilisateur').setDescription('Utilisateur')),
    )
    .addSubcommand((s) => s.setName('threshold').setDescription('Configurer les seuils')
      .addStringOption((o) => o.setName('type').setDescription('Type d\'action').setRequired(true).addChoices(
        { name: 'Bans', value: 'max_bans' },
        { name: 'Kicks', value: 'max_kicks' },
        { name: 'Suppression salons', value: 'max_channel_deletes' },
        { name: 'Création salons', value: 'max_channel_creates' },
        { name: 'Suppression rôles', value: 'max_role_deletes' },
        { name: 'Création rôles', value: 'max_role_creates' },
      ))
      .addIntegerOption((o) => o.setName('valeur').setDescription('Seuil (actions/10s)').setRequired(true).setMinValue(1).setMaxValue(50)),
    )
    .addSubcommand((s) => s.setName('logs').setDescription('Voir les logs anti-nuke'))
    .addSubcommand((s) => s.setName('emergency').setDescription('Mode urgence — verrouille TOUT')),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    // Ensure owner only for critical actions
    const isOwner = interaction.guild.ownerId === interaction.user.id;

    const getConfig = async () => {
      const config = await db('guild_config').where({ guild_id: guildId }).first();
      const modules = config?.modules ? JSON.parse(config.modules) : {};
      return modules.antinuke || { enabled: false, max_bans: 3, max_kicks: 5, max_channel_deletes: 3, max_channel_creates: 5, max_role_deletes: 3, max_role_creates: 5 };
    };

    const saveConfig = async (antiConfig) => {
      const config = await db('guild_config').where({ guild_id: guildId }).first();
      const modules = config?.modules ? JSON.parse(config.modules) : {};
      modules.antinuke = antiConfig;
      await db('guild_config').where({ guild_id: guildId }).update({ modules: JSON.stringify(modules) });
    };

    switch (sub) {
      case 'enable': {
        if (!isOwner) return interaction.reply({ content: '❌ Seul le propriétaire du serveur peut activer l\'anti-nuke.', ephemeral: true });
        const config = await getConfig();
        config.enabled = true;
        await saveConfig(config);
        return interaction.reply({
          embeds: [new EmbedBuilder().setTitle('🛡️ Anti-Nuke activé').setColor(0x2ECC71).setDescription('Le système anti-nuke est maintenant actif.\nToute action suspecte sera automatiquement bloquée.')],
          ephemeral: true,
        });
      }

      case 'disable': {
        if (!isOwner) return interaction.reply({ content: '❌ Seul le propriétaire peut désactiver l\'anti-nuke.', ephemeral: true });
        const config = await getConfig();
        config.enabled = false;
        await saveConfig(config);
        return interaction.reply({ content: '⚠️ Anti-nuke désactivé.', ephemeral: true });
      }

      case 'status': {
        const config = await getConfig();
        const whitelist = await db('antinuke_whitelist').where({ guild_id: guildId });

        const embed = new EmbedBuilder()
          .setTitle('🛡️ Anti-Nuke — Statut')
          .setColor(config.enabled ? 0x2ECC71 : 0xE74C3C)
          .addFields(
            { name: 'Statut', value: config.enabled ? '✅ Actif' : '❌ Inactif', inline: true },
            { name: 'Whitelist', value: `${whitelist.length} utilisateur(s)`, inline: true },
            { name: '\u200b', value: '\u200b', inline: true },
            { name: 'Seuils (actions/10s)', value: [
              `🔨 Bans: ${config.max_bans || 3}`,
              `🦵 Kicks: ${config.max_kicks || 5}`,
              `🗑️ Suppr. salons: ${config.max_channel_deletes || 3}`,
              `➕ Créa. salons: ${config.max_channel_creates || 5}`,
              `🏷️ Suppr. rôles: ${config.max_role_deletes || 3}`,
              `🏷️ Créa. rôles: ${config.max_role_creates || 5}`,
            ].join('\n') },
          );

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'whitelist': {
        if (!isOwner) return interaction.reply({ content: '❌ Seul le propriétaire peut gérer la whitelist.', ephemeral: true });
        const action = interaction.options.getString('action');
        const user = interaction.options.getUser('utilisateur');

        if (action === 'list') {
          const list = await db('antinuke_whitelist').where({ guild_id: guildId });
          if (!list.length) return interaction.reply({ content: 'Whitelist vide.', ephemeral: true });
          return interaction.reply({
            content: list.map((w) => `<@${w.user_id}>`).join(', '),
            ephemeral: true,
          });
        }

        if (!user) return interaction.reply({ content: '❌ Utilisateur requis.', ephemeral: true });

        if (action === 'add') {
          await db('antinuke_whitelist').insert({ guild_id: guildId, user_id: user.id }).onConflict(['guild_id', 'user_id']).ignore();
          return interaction.reply({ content: `✅ ${user} ajouté à la whitelist.`, ephemeral: true });
        }

        if (action === 'remove') {
          await db('antinuke_whitelist').where({ guild_id: guildId, user_id: user.id }).delete();
          return interaction.reply({ content: `✅ ${user} retiré de la whitelist.`, ephemeral: true });
        }
        break;
      }

      case 'threshold': {
        if (!isOwner) return interaction.reply({ content: '❌ Seul le propriétaire peut modifier les seuils.', ephemeral: true });
        const type = interaction.options.getString('type');
        const value = interaction.options.getInteger('valeur');
        const config = await getConfig();
        config[type] = value;
        await saveConfig(config);

        const labels = { max_bans: 'Bans', max_kicks: 'Kicks', max_channel_deletes: 'Suppr. salons', max_channel_creates: 'Créa. salons', max_role_deletes: 'Suppr. rôles', max_role_creates: 'Créa. rôles' };
        return interaction.reply({ content: `✅ Seuil **${labels[type]}** défini à **${value}** actions/10s.`, ephemeral: true });
      }

      case 'logs': {
        const logs = await db('antinuke_log').where({ guild_id: guildId }).orderBy('created_at', 'desc').limit(10);
        if (!logs.length) return interaction.reply({ content: 'Aucun log anti-nuke.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle('🛡️ Logs Anti-Nuke')
          .setColor(0xE74C3C)
          .setDescription(logs.map((l) => `<t:${Math.floor(new Date(l.created_at).getTime() / 1000)}:R> — <@${l.user_id}> : **${l.action}** → ${l.punishment || 'N/A'}`).join('\n'));

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'emergency': {
        if (!isOwner) return interaction.reply({ content: '❌ Seul le propriétaire peut activer le mode urgence.', ephemeral: true });

        await interaction.deferReply({ ephemeral: true });

        const guild = interaction.guild;
        let locked = 0;

        for (const [, channel] of guild.channels.cache) {
          try {
            if (channel.isTextBased() && !channel.isThread()) {
              await channel.permissionOverwrites.edit(guildId, { SendMessages: false });
              locked++;
            }
          } catch (e) { /* can't edit */ }
        }

        await db('antinuke_log').insert({
          guild_id: guildId,
          user_id: interaction.user.id,
          action: 'emergency_lockdown',
          details: `${locked} salons verrouillés`,
          punishment: 'server_lock',
        });

        return interaction.editReply({
          embeds: [new EmbedBuilder()
            .setTitle('🚨 MODE URGENCE ACTIVÉ')
            .setColor(0xE74C3C)
            .setDescription(`**${locked}** salons verrouillés.\n\nUtilisez \`/lockdown server\` pour déverrouiller.`)],
        });
      }
    }
  },
};
