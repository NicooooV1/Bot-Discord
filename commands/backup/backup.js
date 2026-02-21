// ===================================
// Ultra Suite — /backup
// Système de backup
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'backup',
  cooldown: 60,

  data: new SlashCommandBuilder()
    .setName('backup')
    .setDescription('Système de backup du serveur')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
    .addSubcommand((s) => s.setName('create').setDescription('Créer une backup')
      .addStringOption((o) => o.setName('nom').setDescription('Nom de la backup'))
      .addBooleanOption((o) => o.setName('messages').setDescription('Inclure les messages (100 derniers/salon)'))
      .addBooleanOption((o) => o.setName('roles').setDescription('Inclure les rôles'))
      .addBooleanOption((o) => o.setName('channels').setDescription('Inclure les salons'))
      .addBooleanOption((o) => o.setName('emojis').setDescription('Inclure les emojis')),
    )
    .addSubcommand((s) => s.setName('list').setDescription('Lister les backups'))
    .addSubcommand((s) => s.setName('info').setDescription('Détails d\'une backup')
      .addStringOption((o) => o.setName('id').setDescription('ID de la backup').setRequired(true)),
    )
    .addSubcommand((s) => s.setName('delete').setDescription('Supprimer une backup')
      .addStringOption((o) => o.setName('id').setDescription('ID de la backup').setRequired(true)),
    )
    .addSubcommand((s) => s.setName('load').setDescription('Charger une backup (DANGER)')
      .addStringOption((o) => o.setName('id').setDescription('ID de la backup').setRequired(true)),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    switch (sub) {
      case 'create': {
        await interaction.deferReply({ ephemeral: true });
        const name = interaction.options.getString('nom') || `Backup ${new Date().toISOString().split('T')[0]}`;
        const includeMessages = interaction.options.getBoolean('messages') ?? false;
        const includeRoles = interaction.options.getBoolean('roles') ?? true;
        const includeChannels = interaction.options.getBoolean('channels') ?? true;
        const includeEmojis = interaction.options.getBoolean('emojis') ?? true;

        const guild = interaction.guild;
        const backupData = {
          name: guild.name,
          icon: guild.iconURL(),
          settings: {
            verificationLevel: guild.verificationLevel,
            defaultMessageNotifications: guild.defaultMessageNotifications,
            explicitContentFilter: guild.explicitContentFilter,
          },
        };

        if (includeRoles) {
          backupData.roles = guild.roles.cache
            .filter((r) => !r.managed && r.id !== guildId)
            .sort((a, b) => b.position - a.position)
            .map((r) => ({ name: r.name, color: r.hexColor, hoist: r.hoist, permissions: r.permissions.bitfield.toString(), mentionable: r.mentionable, position: r.position }));
        }

        if (includeChannels) {
          backupData.channels = guild.channels.cache
            .filter((c) => c.type !== 15) // Not a thread
            .sort((a, b) => a.position - b.position)
            .map((c) => ({ name: c.name, type: c.type, topic: c.topic, nsfw: c.nsfw, parent: c.parent?.name, position: c.position, rateLimitPerUser: c.rateLimitPerUser }));
        }

        if (includeEmojis) {
          backupData.emojis = guild.emojis.cache.map((e) => ({ name: e.name, url: e.url, animated: e.animated }));
        }

        if (includeMessages) {
          backupData.messages = {};
          const textChannels = guild.channels.cache.filter((c) => c.isTextBased() && !c.isThread());
          for (const [id, channel] of textChannels) {
            try {
              const msgs = await channel.messages.fetch({ limit: 100 });
              backupData.messages[channel.name] = msgs.map((m) => ({
                author: m.author.tag,
                content: m.content,
                timestamp: m.createdTimestamp,
                embeds: m.embeds.length,
                attachments: m.attachments.map((a) => a.url),
              }));
            } catch (e) { /* can't read channel */ }
          }
        }

        const [backupId] = await db('backups').insert({
          guild_id: guildId,
          creator_id: interaction.user.id,
          name,
          data: JSON.stringify(backupData),
          size: Buffer.byteLength(JSON.stringify(backupData)),
        });

        const embed = new EmbedBuilder()
          .setTitle('💾 Backup créée')
          .setColor(0x2ECC71)
          .addFields(
            { name: '🆔 ID', value: String(backupId), inline: true },
            { name: '📝 Nom', value: name, inline: true },
            { name: '📦 Taille', value: `${(Buffer.byteLength(JSON.stringify(backupData)) / 1024).toFixed(1)} KB`, inline: true },
            { name: '📊 Contenu', value: [
              includeRoles ? `✅ ${backupData.roles?.length || 0} rôles` : '❌ Rôles',
              includeChannels ? `✅ ${backupData.channels?.length || 0} salons` : '❌ Salons',
              includeEmojis ? `✅ ${backupData.emojis?.length || 0} emojis` : '❌ Emojis',
              includeMessages ? '✅ Messages' : '❌ Messages',
            ].join('\n') },
          );

        return interaction.editReply({ embeds: [embed] });
      }

      case 'list': {
        const backups = await db('backups').where({ guild_id: guildId }).orderBy('created_at', 'desc').limit(10);
        if (!backups.length) return interaction.reply({ content: 'Aucune backup trouvée.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle('💾 Backups')
          .setColor(0x3498DB)
          .setDescription(backups.map((b) => `**#${b.id}** — ${b.name}\n📅 ${new Date(b.created_at).toLocaleDateString('fr-FR')} • 📦 ${(b.size / 1024).toFixed(1)} KB`).join('\n\n'));

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'info': {
        const id = interaction.options.getString('id');
        const backup = await db('backups').where({ guild_id: guildId, id }).first();
        if (!backup) return interaction.reply({ content: '❌ Backup introuvable.', ephemeral: true });

        const data = JSON.parse(backup.data);
        const embed = new EmbedBuilder()
          .setTitle(`💾 Backup #${id} — ${backup.name}`)
          .setColor(0x3498DB)
          .addFields(
            { name: '👤 Créateur', value: `<@${backup.creator_id}>`, inline: true },
            { name: '📅 Date', value: new Date(backup.created_at).toLocaleDateString('fr-FR'), inline: true },
            { name: '📦 Taille', value: `${(backup.size / 1024).toFixed(1)} KB`, inline: true },
            { name: '🏷️ Rôles', value: String(data.roles?.length || 0), inline: true },
            { name: '📺 Salons', value: String(data.channels?.length || 0), inline: true },
            { name: '😀 Emojis', value: String(data.emojis?.length || 0), inline: true },
          );

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'delete': {
        const id = interaction.options.getString('id');
        const deleted = await db('backups').where({ guild_id: guildId, id }).delete();
        return interaction.reply({ content: deleted ? `✅ Backup #${id} supprimée.` : '❌ Introuvable.', ephemeral: true });
      }

      case 'load': {
        const id = interaction.options.getString('id');
        const backup = await db('backups').where({ guild_id: guildId, id }).first();
        if (!backup) return interaction.reply({ content: '❌ Backup introuvable.', ephemeral: true });

        return interaction.reply({
          content: `⚠️ **ATTENTION** — Le chargement d'une backup va modifier le serveur.\nPour des raisons de sécurité, cette action nécessite une confirmation via le dashboard web.\n\nBackup : **${backup.name}** (#${id})`,
          ephemeral: true,
        });
      }
    }
  },
};
