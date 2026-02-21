// ===================================
// Ultra Suite — /suggest
// Système de suggestions
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'polls',
  cooldown: 30,

  data: new SlashCommandBuilder()
    .setName('suggest')
    .setDescription('Gérer les suggestions')
    .addSubcommand((s) =>
      s.setName('create').setDescription('Proposer une suggestion')
        .addStringOption((o) => o.setName('suggestion').setDescription('Votre suggestion').setRequired(true))
        .addStringOption((o) => o.setName('categorie').setDescription('Catégorie (feature, bug, amélioration, autre)').addChoices(
          { name: 'Fonctionnalité', value: 'feature' },
          { name: 'Bug', value: 'bug' },
          { name: 'Amélioration', value: 'improvement' },
          { name: 'Autre', value: 'other' },
        )),
    )
    .addSubcommand((s) =>
      s.setName('approve').setDescription('Approuver une suggestion')
        .addStringOption((o) => o.setName('id').setDescription('ID de la suggestion').setRequired(true))
        .addStringOption((o) => o.setName('raison').setDescription('Raison')),
    )
    .addSubcommand((s) =>
      s.setName('deny').setDescription('Refuser une suggestion')
        .addStringOption((o) => o.setName('id').setDescription('ID de la suggestion').setRequired(true))
        .addStringOption((o) => o.setName('raison').setDescription('Raison')),
    )
    .addSubcommand((s) =>
      s.setName('consider').setDescription('Mettre en considération')
        .addStringOption((o) => o.setName('id').setDescription('ID de la suggestion').setRequired(true))
        .addStringOption((o) => o.setName('raison').setDescription('Commentaire')),
    )
    .addSubcommand((s) =>
      s.setName('list').setDescription('Lister les suggestions')
        .addStringOption((o) => o.setName('statut').setDescription('Filtrer par statut').addChoices(
          { name: 'En attente', value: 'pending' },
          { name: 'Approuvé', value: 'approved' },
          { name: 'Refusé', value: 'denied' },
          { name: 'En considération', value: 'considered' },
        )),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    const statusColors = { pending: 0xFFFF00, approved: 0x2ECC71, denied: 0xE74C3C, considered: 0x3498DB };
    const statusLabels = { pending: '⏳ En attente', approved: '✅ Approuvé', denied: '❌ Refusé', considered: '🤔 En considération' };

    switch (sub) {
      case 'create': {
        const text = interaction.options.getString('suggestion');
        const cat = interaction.options.getString('categorie') || 'other';

        const [id] = await db('suggestions').insert({
          guild_id: guildId,
          author_id: interaction.user.id,
          content: text,
          category: cat,
          status: 'pending',
          upvotes: 0,
          downvotes: 0,
        });

        const embed = new EmbedBuilder()
          .setTitle(`💡 Suggestion #${id}`)
          .setDescription(text)
          .setColor(statusColors.pending)
          .addFields(
            { name: 'Catégorie', value: cat, inline: true },
            { name: 'Statut', value: statusLabels.pending, inline: true },
            { name: 'Votes', value: '👍 0 | 👎 0', inline: true },
          )
          .setAuthor({ name: interaction.user.tag, iconURL: interaction.user.displayAvatarURL() })
          .setTimestamp();

        const row = new ActionRowBuilder().addComponents(
          new ButtonBuilder().setCustomId(`suggest_up_${id}`).setLabel('Pour').setEmoji('👍').setStyle(ButtonStyle.Success),
          new ButtonBuilder().setCustomId(`suggest_down_${id}`).setLabel('Contre').setEmoji('👎').setStyle(ButtonStyle.Danger),
        );

        const msg = await interaction.reply({ embeds: [embed], components: [row], fetchReply: true });
        await db('suggestions').where({ id }).update({ message_id: msg.id, channel_id: interaction.channelId });
        break;
      }

      case 'approve':
      case 'deny':
      case 'consider': {
        if (!interaction.member.permissions.has(PermissionFlagsBits.ManageGuild))
          return interaction.reply({ content: '❌ Permission ManageGuild requise.', ephemeral: true });

        const sId = interaction.options.getString('id');
        const reason = interaction.options.getString('raison') || 'Aucune raison';
        const status = sub === 'approve' ? 'approved' : sub === 'deny' ? 'denied' : 'considered';

        const suggestion = await db('suggestions').where({ guild_id: guildId, id: sId }).first();
        if (!suggestion) return interaction.reply({ content: '❌ Suggestion introuvable.', ephemeral: true });

        await db('suggestions').where({ id: sId }).update({ status, reviewer_id: interaction.user.id, review_reason: reason });

        const embed = new EmbedBuilder()
          .setTitle(`💡 Suggestion #${sId}`)
          .setDescription(suggestion.content)
          .setColor(statusColors[status])
          .addFields(
            { name: 'Statut', value: statusLabels[status], inline: true },
            { name: 'Réponse de', value: interaction.user.tag, inline: true },
            { name: 'Raison', value: reason },
          );

        if (suggestion.channel_id && suggestion.message_id) {
          const ch = await interaction.guild.channels.fetch(suggestion.channel_id).catch(() => null);
          if (ch) {
            const msg = await ch.messages.fetch(suggestion.message_id).catch(() => null);
            if (msg) await msg.edit({ embeds: [embed] });
          }
        }

        return interaction.reply({ content: `✅ Suggestion #${sId} — ${statusLabels[status]}`, ephemeral: true });
      }

      case 'list': {
        const statusFilter = interaction.options.getString('statut');
        let query = db('suggestions').where({ guild_id: guildId }).orderBy('id', 'desc').limit(15);
        if (statusFilter) query = query.where({ status: statusFilter });

        const suggestions = await query;
        if (!suggestions.length) return interaction.reply({ content: 'Aucune suggestion trouvée.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle('💡 Suggestions')
          .setColor(0x3498DB)
          .setDescription(suggestions.map((s) => `**#${s.id}** ${statusLabels[s.status]} — ${s.content.substring(0, 50)}${s.content.length > 50 ? '...' : ''}`).join('\n'));

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }
    }
  },
};
