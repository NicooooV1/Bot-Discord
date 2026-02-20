// ===================================
// Ultra Suite — /stats
// Dashboard de statistiques du serveur
// /stats overview | members | messages | moderation
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'stats',
  cooldown: 10,

  data: new SlashCommandBuilder()
    .setName('stats')
    .setDescription('Statistiques du serveur')
    .addSubcommand((sub) =>
      sub.setName('overview').setDescription('Vue d\'ensemble'))
    .addSubcommand((sub) =>
      sub.setName('members').setDescription('Statistiques des membres'))
    .addSubcommand((sub) =>
      sub.setName('messages').setDescription('Statistiques des messages (7 derniers jours)'))
    .addSubcommand((sub) =>
      sub.setName('moderation').setDescription('Statistiques de modération')
        .addStringOption((opt) => opt.setName('période').setDescription('Période')
          .addChoices(
            { name: '7 jours', value: '7' },
            { name: '30 jours', value: '30' },
            { name: '90 jours', value: '90' },
          ))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();

    await interaction.deferReply();

    // === OVERVIEW ===
    if (sub === 'overview') {
      const today = new Date().toISOString().slice(0, 10);
      const todayMetrics = await db('daily_metrics').where('guild_id', guildId).where('date', today).first();

      const totalUsers = await db('users').where('guild_id', guildId).count('id as count').first();
      const totalMessages = await db('users').where('guild_id', guildId).sum('total_messages as total').first();
      const totalSanctions = await db('sanctions').where('guild_id', guildId).count('id as count').first();
      const activeSanctions = await db('sanctions').where('guild_id', guildId).where('active', true).count('id as count').first();
      const openTickets = await db('tickets').where('guild_id', guildId).where('status', 'OPEN').count('id as count').first();

      const embed = new EmbedBuilder()
        .setTitle('📊 Statistiques — Vue d\'ensemble')
        .setColor(0x5865F2)
        .addFields(
          { name: '👥 Membres Discord', value: String(interaction.guild.memberCount), inline: true },
          { name: '📝 Membres trackés', value: String(totalUsers?.count || 0), inline: true },
          { name: '💬 Messages total', value: (totalMessages?.total || 0).toLocaleString('fr-FR'), inline: true },
          { name: '📅 Aujourd\'hui', value: [
            `Messages : ${todayMetrics?.messages || 0}`,
            `Nouveaux : ${todayMetrics?.new_members || 0}`,
            `Départs : ${todayMetrics?.left_members || 0}`,
            `Vocal : ${todayMetrics?.voice_minutes || 0} min`,
          ].join('\n'), inline: false },
          { name: '🔨 Sanctions', value: `${totalSanctions?.count || 0} total (${activeSanctions?.count || 0} actives)`, inline: true },
          { name: '🎫 Tickets ouverts', value: String(openTickets?.count || 0), inline: true },
        )
        .setTimestamp();

      return interaction.editReply({ embeds: [embed] });
    }

    // === MEMBERS ===
    if (sub === 'members') {
      const sevenDaysAgo = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
      const metrics = await db('daily_metrics')
        .where('guild_id', guildId)
        .where('date', '>=', sevenDaysAgo)
        .orderBy('date', 'asc');

      const totalJoined = metrics.reduce((sum, m) => sum + (m.new_members || 0), 0);
      const totalLeft = metrics.reduce((sum, m) => sum + (m.left_members || 0), 0);
      const netGrowth = totalJoined - totalLeft;

      // Top contributeurs
      const topMessages = await db('users').where('guild_id', guildId)
        .orderBy('total_messages', 'desc').limit(5).select('user_id', 'total_messages');
      const topVoice = await db('users').where('guild_id', guildId)
        .orderBy('voice_minutes', 'desc').limit(5).select('user_id', 'voice_minutes');

      const topMsgLines = topMessages.map((u, i) => `**${i + 1}.** <@${u.user_id}> — ${(u.total_messages || 0).toLocaleString('fr-FR')} msgs`);
      const topVoiceLines = topVoice.filter((u) => u.voice_minutes > 0).map((u, i) => `**${i + 1}.** <@${u.user_id}> — ${(u.voice_minutes || 0).toLocaleString('fr-FR')} min`);

      const embed = new EmbedBuilder()
        .setTitle('👥 Statistiques Membres — 7 derniers jours')
        .setColor(0x5865F2)
        .addFields(
          { name: '📈 Arrivées', value: String(totalJoined), inline: true },
          { name: '📉 Départs', value: String(totalLeft), inline: true },
          { name: '📊 Croissance nette', value: `${netGrowth >= 0 ? '+' : ''}${netGrowth}`, inline: true },
          { name: '💬 Top Messages', value: topMsgLines.join('\n') || '*Aucune donnée*', inline: false },
          { name: '🎙️ Top Vocal', value: topVoiceLines.join('\n') || '*Aucune donnée*', inline: false },
        )
        .setTimestamp();

      return interaction.editReply({ embeds: [embed] });
    }

    // === MESSAGES ===
    if (sub === 'messages') {
      const sevenDaysAgo = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
      const metrics = await db('daily_metrics')
        .where('guild_id', guildId)
        .where('date', '>=', sevenDaysAgo)
        .orderBy('date', 'asc');

      const days = ['Dim', 'Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam'];
      const lines = metrics.map((m) => {
        const d = new Date(m.date);
        const day = days[d.getDay()];
        const bar = '█'.repeat(Math.min(20, Math.floor((m.messages || 0) / 10)));
        return `\`${day} ${m.date.slice(5)}\` ${bar} **${m.messages || 0}**`;
      });

      const total = metrics.reduce((sum, m) => sum + (m.messages || 0), 0);
      const avg = metrics.length > 0 ? Math.round(total / metrics.length) : 0;

      const embed = new EmbedBuilder()
        .setTitle('💬 Messages — 7 derniers jours')
        .setDescription(lines.join('\n') || '*Aucune donnée*')
        .setColor(0x5865F2)
        .setFooter({ text: `Total : ${total.toLocaleString('fr-FR')} • Moyenne : ${avg}/jour` })
        .setTimestamp();

      return interaction.editReply({ embeds: [embed] });
    }

    // === MODERATION ===
    if (sub === 'moderation') {
      const days = parseInt(interaction.options.getString('période') || '30', 10);
      const since = new Date(Date.now() - days * 86400000);

      const sanctions = await db('sanctions')
        .where('guild_id', guildId)
        .where('created_at', '>=', since)
        .select('type');

      const counts = {};
      for (const s of sanctions) {
        counts[s.type] = (counts[s.type] || 0) + 1;
      }

      const topModerators = await db('sanctions')
        .where('guild_id', guildId)
        .where('created_at', '>=', since)
        .groupBy('moderator_id')
        .orderByRaw('count(*) desc')
        .limit(5)
        .select('moderator_id', db.raw('count(*) as total'));

      const modLines = topModerators.map((m, i) => `**${i + 1}.** <@${m.moderator_id}> — ${m.total} actions`);

      const embed = new EmbedBuilder()
        .setTitle(`🔨 Modération — ${days} derniers jours`)
        .setColor(0xED4245)
        .addFields(
          { name: 'Actions par type', value: Object.entries(counts).map(([t, c]) => `${t}: **${c}**`).join('\n') || '*Aucune*', inline: false },
          { name: 'Top Modérateurs', value: modLines.join('\n') || '*Aucun*', inline: false },
          { name: 'Total', value: String(sanctions.length), inline: true },
        )
        .setTimestamp();

      return interaction.editReply({ embeds: [embed] });
    }
  },
};