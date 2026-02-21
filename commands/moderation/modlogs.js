// ===================================
// Ultra Suite — /modlogs
// Historique de modération complet
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'moderation',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('modlogs')
    .setDescription('Voir l\'historique de modération')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addSubcommand((s) =>
      s.setName('user').setDescription('Historique d\'un membre')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addStringOption((o) => o.setName('type').setDescription('Filtrer par type').addChoices(
          { name: 'Warn', value: 'WARN' },
          { name: 'Timeout', value: 'TIMEOUT' },
          { name: 'Kick', value: 'KICK' },
          { name: 'Ban', value: 'BAN' },
          { name: 'Note', value: 'NOTE' },
        ))
        .addIntegerOption((o) => o.setName('page').setDescription('Page').setMinValue(1)),
    )
    .addSubcommand((s) =>
      s.setName('server').setDescription('Historique complet du serveur')
        .addStringOption((o) => o.setName('type').setDescription('Filtrer par type').addChoices(
          { name: 'Warn', value: 'WARN' },
          { name: 'Timeout', value: 'TIMEOUT' },
          { name: 'Kick', value: 'KICK' },
          { name: 'Ban', value: 'BAN' },
        ))
        .addIntegerOption((o) => o.setName('page').setDescription('Page').setMinValue(1)),
    )
    .addSubcommand((s) =>
      s.setName('case').setDescription('Détails d\'un cas')
        .addIntegerOption((o) => o.setName('id').setDescription('ID du cas').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('reason').setDescription('Modifier la raison d\'un cas')
        .addIntegerOption((o) => o.setName('id').setDescription('ID du cas').setRequired(true))
        .addStringOption((o) => o.setName('raison').setDescription('Nouvelle raison').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('delete').setDescription('Supprimer un cas')
        .addIntegerOption((o) => o.setName('id').setDescription('ID du cas').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('export').setDescription('Exporter les logs de modération')
        .addUserOption((o) => o.setName('membre').setDescription('Membre (vide = tout)')),
    )
    .addSubcommand((s) =>
      s.setName('stats').setDescription('Statistiques de modération')
        .addUserOption((o) => o.setName('moderateur').setDescription('Filtrer par modérateur')),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();
    const perPage = 10;

    const typeEmojis = { WARN: '⚠️', TIMEOUT: '🔇', KICK: '👢', BAN: '🔨', SOFTBAN: '🥾', NOTE: '📝', QUARANTINE: '🔒' };

    switch (sub) {
      case 'user': {
        const target = interaction.options.getUser('membre');
        const type = interaction.options.getString('type');
        const page = (interaction.options.getInteger('page') || 1) - 1;

        let query = db('sanctions').where({ guild_id: guildId, user_id: target.id });
        if (type) query = query.where('type', type);

        const total = await query.clone().count('id as c').first();
        const cases = await query.orderBy('created_at', 'desc').offset(page * perPage).limit(perPage);
        const totalPages = Math.ceil((total?.c || 0) / perPage);

        if (!cases.length) {
          return interaction.reply({ content: `📋 Aucun cas pour **${target.username}**.`, ephemeral: true });
        }

        const embed = new EmbedBuilder()
          .setTitle(`📋 Historique — ${target.username}`)
          .setColor(0xE74C3C)
          .setDescription(cases.map((c) =>
            `${typeEmojis[c.type] || '📌'} **#${c.id}** — ${c.type} | ${c.reason || 'Aucune raison'}\n> Par <@${c.moderator_id}> — <t:${Math.floor(new Date(c.created_at).getTime() / 1000)}:R>`,
          ).join('\n\n'))
          .setFooter({ text: `Page ${page + 1}/${totalPages || 1} • Total: ${total?.c || 0} cas` });

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'server': {
        const type = interaction.options.getString('type');
        const page = (interaction.options.getInteger('page') || 1) - 1;

        let query = db('sanctions').where({ guild_id: guildId });
        if (type) query = query.where('type', type);

        const total = await query.clone().count('id as c').first();
        const cases = await query.orderBy('created_at', 'desc').offset(page * perPage).limit(perPage);
        const totalPages = Math.ceil((total?.c || 0) / perPage);

        if (!cases.length) {
          return interaction.reply({ content: '📋 Aucun cas enregistré.', ephemeral: true });
        }

        const embed = new EmbedBuilder()
          .setTitle('📋 Historique du serveur')
          .setColor(0xE74C3C)
          .setDescription(cases.map((c) =>
            `${typeEmojis[c.type] || '📌'} **#${c.id}** — ${c.type} | <@${c.user_id}>\n> ${c.reason || 'Aucune raison'} — Par <@${c.moderator_id}>`,
          ).join('\n\n'))
          .setFooter({ text: `Page ${page + 1}/${totalPages || 1} • Total: ${total?.c || 0} cas` });

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'case': {
        const caseId = interaction.options.getInteger('id');
        const c = await db('sanctions').where({ guild_id: guildId, id: caseId }).first();
        if (!c) return interaction.reply({ content: '❌ Cas introuvable.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle(`${typeEmojis[c.type] || '📌'} Cas #${c.id}`)
          .setColor(c.type === 'BAN' ? 0xE74C3C : c.type === 'WARN' ? 0xFEE75C : 0xE67E22)
          .addFields(
            { name: 'Type', value: c.type, inline: true },
            { name: 'Utilisateur', value: `<@${c.user_id}>`, inline: true },
            { name: 'Modérateur', value: `<@${c.moderator_id}>`, inline: true },
            { name: 'Raison', value: c.reason || 'Aucune raison', inline: false },
            { name: 'Date', value: `<t:${Math.floor(new Date(c.created_at).getTime() / 1000)}:F>`, inline: true },
            { name: 'Expiration', value: c.expires_at ? `<t:${Math.floor(new Date(c.expires_at).getTime() / 1000)}:F>` : 'Permanent', inline: true },
          )
          .setTimestamp();

        if (c.duration) embed.addFields({ name: 'Durée', value: c.duration, inline: true });

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'reason': {
        const caseId = interaction.options.getInteger('id');
        const reason = interaction.options.getString('raison');
        const updated = await db('sanctions').where({ guild_id: guildId, id: caseId }).update({ reason });
        if (!updated) return interaction.reply({ content: '❌ Cas introuvable.', ephemeral: true });
        return interaction.reply({ content: `✅ Raison du cas #${caseId} mise à jour.`, ephemeral: true });
      }

      case 'delete': {
        const caseId = interaction.options.getInteger('id');
        const deleted = await db('sanctions').where({ guild_id: guildId, id: caseId }).delete();
        if (!deleted) return interaction.reply({ content: '❌ Cas introuvable.', ephemeral: true });
        return interaction.reply({ content: `✅ Cas #${caseId} supprimé.`, ephemeral: true });
      }

      case 'export': {
        const target = interaction.options.getUser('membre');
        let query = db('sanctions').where({ guild_id: guildId });
        if (target) query = query.where('user_id', target.id);
        const cases = await query.orderBy('created_at', 'desc');
        const jsonStr = JSON.stringify(cases, null, 2);
        const buffer = Buffer.from(jsonStr, 'utf-8');
        return interaction.reply({
          content: `📤 ${cases.length} cas exportés.`,
          files: [{ attachment: buffer, name: `modlogs-${guildId}${target ? `-${target.id}` : ''}.json` }],
          ephemeral: true,
        });
      }

      case 'stats': {
        const mod = interaction.options.getUser('moderateur');
        let query = db('sanctions').where({ guild_id: guildId });
        if (mod) query = query.where('moderator_id', mod.id);

        const typeCounts = await query.clone().select('type').count('id as count').groupBy('type');
        const total = typeCounts.reduce((sum, t) => sum + t.count, 0);
        const last30d = await query.clone()
          .where('created_at', '>', new Date(Date.now() - 30 * 86400000))
          .count('id as c').first();

        const embed = new EmbedBuilder()
          .setTitle(`📊 Statistiques de modération${mod ? ` — ${mod.username}` : ''}`)
          .setColor(0x3498DB)
          .addFields(
            { name: '📋 Total', value: `${total}`, inline: true },
            { name: '📅 30 derniers jours', value: `${last30d?.c || 0}`, inline: true },
            ...typeCounts.map((t) => ({
              name: `${typeEmojis[t.type] || '📌'} ${t.type}`,
              value: `${t.count}`,
              inline: true,
            })),
          )
          .setTimestamp();

        // Top moderators
        if (!mod) {
          const topMods = await db('sanctions').where({ guild_id: guildId })
            .select('moderator_id')
            .count('id as count')
            .groupBy('moderator_id')
            .orderBy('count', 'desc')
            .limit(5);
          if (topMods.length) {
            embed.addFields({
              name: '🏆 Top modérateurs',
              value: topMods.map((m, i) => `**#${i + 1}** <@${m.moderator_id}> — ${m.count} actions`).join('\n'),
            });
          }
        }

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }
    }
  },
};
