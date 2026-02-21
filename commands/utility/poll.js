// ===================================
// Ultra Suite — /poll
// Système de sondages
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'polls',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('poll')
    .setDescription('Créer un sondage')
    .addSubcommand((s) =>
      s.setName('create').setDescription('Créer un sondage')
        .addStringOption((o) => o.setName('question').setDescription('Question').setRequired(true))
        .addStringOption((o) => o.setName('options').setDescription('Options séparées par | (ex: Oui|Non|Peut-être)').setRequired(true))
        .addStringOption((o) => o.setName('duree').setDescription('Durée (ex: 1h, 1d)'))
        .addBooleanOption((o) => o.setName('multiple').setDescription('Choix multiples'))
        .addBooleanOption((o) => o.setName('anonyme').setDescription('Votes anonymes'))
        .addChannelOption((o) => o.setName('salon').setDescription('Salon')),
    )
    .addSubcommand((s) =>
      s.setName('end').setDescription('Terminer un sondage')
        .addStringOption((o) => o.setName('message_id').setDescription('ID du message').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('results').setDescription('Voir les résultats')
        .addStringOption((o) => o.setName('message_id').setDescription('ID du message').setRequired(true)),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();

    const parseDuration = (str) => {
      if (!str) return null;
      const m = str.match(/^(\d+)(m|h|d|w)$/);
      if (!m) return null;
      const ms = { m: 60000, h: 3600000, d: 86400000, w: 604800000 };
      return parseInt(m[1]) * (ms[m[2]] || 0);
    };

    const emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'];

    switch (sub) {
      case 'create': {
        const question = interaction.options.getString('question');
        const optionsStr = interaction.options.getString('options');
        const durationStr = interaction.options.getString('duree');
        const multiple = interaction.options.getBoolean('multiple') || false;
        const anonymous = interaction.options.getBoolean('anonyme') || false;
        const channel = interaction.options.getChannel('salon') || interaction.channel;

        const options = optionsStr.split('|').map((o) => o.trim()).filter(Boolean).slice(0, 10);
        if (options.length < 2) return interaction.reply({ content: '❌ Minimum 2 options.', ephemeral: true });

        const durationMs = parseDuration(durationStr);
        const endsAt = durationMs ? new Date(Date.now() + durationMs) : null;

        const pollOptions = options.map((label, i) => ({ label, emoji: emojis[i], votes: [] }));

        const embed = new EmbedBuilder()
          .setTitle('📊 ' + question)
          .setColor(0x3498DB)
          .setDescription(pollOptions.map((o) => `${o.emoji} **${o.label}** — 0 vote(s)`).join('\n'))
          .setFooter({ text: `${multiple ? 'Choix multiples' : 'Choix unique'} • ${anonymous ? 'Anonyme' : 'Public'}${endsAt ? ` • Fin: ` : ''}` })
          .setTimestamp(endsAt);

        const rows = [];
        for (let i = 0; i < pollOptions.length; i += 5) {
          const row = new ActionRowBuilder();
          for (let j = i; j < Math.min(i + 5, pollOptions.length); j++) {
            row.addComponents(
              new ButtonBuilder().setCustomId(`poll_vote_${j}`).setLabel(pollOptions[j].label).setEmoji(pollOptions[j].emoji).setStyle(ButtonStyle.Secondary),
            );
          }
          rows.push(row);
        }

        const msg = await channel.send({ embeds: [embed], components: rows });

        await db('polls').insert({
          guild_id: guildId,
          channel_id: channel.id,
          message_id: msg.id,
          creator_id: interaction.user.id,
          question,
          options: JSON.stringify(pollOptions),
          multiple_choice: multiple,
          anonymous,
          status: 'active',
          ends_at: endsAt,
        });

        return interaction.reply({ content: `✅ Sondage créé dans ${channel} !`, ephemeral: true });
      }

      case 'end': {
        const messageId = interaction.options.getString('message_id');
        const poll = await db('polls').where({ guild_id: guildId, message_id: messageId, status: 'active' }).first();
        if (!poll) return interaction.reply({ content: '❌ Sondage introuvable.', ephemeral: true });

        await db('polls').where({ id: poll.id }).update({ status: 'ended' });

        const options = JSON.parse(poll.options);
        const totalVotes = options.reduce((sum, o) => sum + o.votes.length, 0);
        const maxVotes = Math.max(...options.map((o) => o.votes.length));

        const embed = new EmbedBuilder()
          .setTitle('📊 ' + poll.question + ' (Terminé)')
          .setColor(0x95A5A6)
          .setDescription(options.map((o) => {
            const pct = totalVotes > 0 ? Math.round(o.votes.length / totalVotes * 100) : 0;
            const bar = '█'.repeat(Math.round(pct / 5)) + '░'.repeat(20 - Math.round(pct / 5));
            const winner = o.votes.length === maxVotes && maxVotes > 0 ? ' 🏆' : '';
            return `${o.emoji} **${o.label}**${winner}\n${bar} ${pct}% (${o.votes.length})`;
          }).join('\n\n'))
          .setFooter({ text: `Total: ${totalVotes} vote(s)` });

        const channel = await interaction.guild.channels.fetch(poll.channel_id).catch(() => null);
        if (channel) {
          const msg = await channel.messages.fetch(messageId).catch(() => null);
          if (msg) await msg.edit({ embeds: [embed], components: [] });
        }

        return interaction.reply({ content: '✅ Sondage terminé.', ephemeral: true });
      }

      case 'results': {
        const messageId = interaction.options.getString('message_id');
        const poll = await db('polls').where({ guild_id: guildId, message_id: messageId }).first();
        if (!poll) return interaction.reply({ content: '❌ Sondage introuvable.', ephemeral: true });

        const options = JSON.parse(poll.options);
        const totalVotes = options.reduce((sum, o) => sum + o.votes.length, 0);

        const embed = new EmbedBuilder()
          .setTitle('📊 Résultats — ' + poll.question)
          .setColor(0x3498DB)
          .setDescription(options.map((o) => {
            const pct = totalVotes > 0 ? Math.round(o.votes.length / totalVotes * 100) : 0;
            const bar = '█'.repeat(Math.round(pct / 5)) + '░'.repeat(20 - Math.round(pct / 5));
            return `${o.emoji} **${o.label}**\n${bar} ${pct}% (${o.votes.length})`;
          }).join('\n\n'))
          .setFooter({ text: `Total: ${totalVotes} vote(s) • Statut: ${poll.status}` });

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }
    }
  },
};
