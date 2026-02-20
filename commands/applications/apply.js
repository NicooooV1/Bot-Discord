// ===================================
// Ultra Suite — /apply
// Système de candidatures avec formulaire modal
// /apply | /apply setup | /apply list
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ModalBuilder, TextInputBuilder, TextInputStyle, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'applications',
  cooldown: 10,

  data: new SlashCommandBuilder()
    .setName('apply')
    .setDescription('Système de candidatures')
    .addSubcommand((sub) =>
      sub.setName('submit').setDescription('Soumettre une candidature'))
    .addSubcommand((sub) =>
      sub.setName('setup').setDescription('Configurer les candidatures (admin)')
        .addChannelOption((opt) => opt.setName('channel').setDescription('Channel de réception des candidatures').setRequired(true))
        .addStringOption((opt) => opt.setName('questions').setDescription('Questions séparées par | (max 5)').setRequired(true))
        .addRoleOption((opt) => opt.setName('reviewer_role').setDescription('Rôle des reviewers')))
    .addSubcommand((sub) =>
      sub.setName('list').setDescription('Voir les candidatures (staff)')
        .addStringOption((opt) => opt.setName('statut').setDescription('Filtrer par statut')
          .addChoices(
            { name: 'En attente', value: 'PENDING' },
            { name: 'Acceptée', value: 'ACCEPTED' },
            { name: 'Refusée', value: 'REJECTED' },
          ))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();

    // === SUBMIT ===
    if (sub === 'submit') {
      // Vérifier si déjà une candidature en attente
      const pending = await db('applications')
        .where('guild_id', guildId)
        .where('user_id', interaction.user.id)
        .where('status', 'PENDING')
        .first();

      if (pending) {
        return interaction.reply({ content: '❌ Vous avez déjà une candidature en attente.', ephemeral: true });
      }

      // Récupérer la config
      const config = await configService.get(guildId);
      const appConfig = config.applications || {};
      const questions = appConfig.questions || ['Pourquoi voulez-vous nous rejoindre ?', 'Parlez-nous de vous.'];

      // Créer le modal
      const modal = new ModalBuilder()
        .setCustomId('application-submit')
        .setTitle('📝 Candidature');

      const rows = questions.slice(0, 5).map((q, i) => {
        const input = new TextInputBuilder()
          .setCustomId(`q${i}`)
          .setLabel(q.slice(0, 45))
          .setStyle(q.length > 50 ? TextInputStyle.Paragraph : TextInputStyle.Short)
          .setRequired(true)
          .setMaxLength(1000);
        return new ActionRowBuilder().addComponents(input);
      });

      modal.addComponents(...rows);
      await interaction.showModal(modal);
    }

    // === SETUP ===
    if (sub === 'setup') {
      if (!interaction.member.permissions.has(PermissionFlagsBits.Administrator)) {
        return interaction.reply({ content: '❌ Administrateur requis.', ephemeral: true });
      }

      const channel = interaction.options.getChannel('channel');
      const reviewerRole = interaction.options.getRole('reviewer_role');
      const questionsStr = interaction.options.getString('questions');
      const questions = questionsStr.split('|').map((q) => q.trim()).filter(Boolean).slice(0, 5);

      if (questions.length === 0) {
        return interaction.reply({ content: '❌ Fournissez au moins une question.', ephemeral: true });
      }

      const config = await configService.get(guildId);
      const appConfig = {
        ...(config.applications || {}),
        channel: channel.id,
        reviewerRole: reviewerRole?.id || null,
        questions,
      };

      await configService.set(guildId, { applications: appConfig });

      const embed = new EmbedBuilder()
        .setTitle('📝 Candidatures configurées')
        .addFields(
          { name: 'Channel', value: channel.toString(), inline: true },
          { name: 'Reviewer', value: reviewerRole ? reviewerRole.toString() : '*Aucun*', inline: true },
          { name: 'Questions', value: questions.map((q, i) => `**${i + 1}.** ${q}`).join('\n'), inline: false },
        )
        .setColor(0x57F287).setTimestamp();

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    // === LIST ===
    if (sub === 'list') {
      if (!interaction.member.permissions.has(PermissionFlagsBits.ModerateMembers)) {
        return interaction.reply({ content: '❌ Permission requise.', ephemeral: true });
      }

      const status = interaction.options.getString('statut') || 'PENDING';
      const apps = await db('applications')
        .where('guild_id', guildId)
        .where('status', status)
        .orderBy('created_at', 'desc')
        .limit(15);

      if (apps.length === 0) {
        return interaction.reply({ content: `ℹ️ Aucune candidature **${status}**.`, ephemeral: true });
      }

      const statusEmoji = { PENDING: '⏳', ACCEPTED: '✅', REJECTED: '❌' };
      const lines = apps.map((a) => {
        const ts = a.created_at ? `<t:${Math.floor(new Date(a.created_at).getTime() / 1000)}:R>` : '?';
        return `${statusEmoji[a.status]} **#${a.id}** — <@${a.user_id}> — ${ts}`;
      });

      const embed = new EmbedBuilder()
        .setTitle(`📝 Candidatures — ${status}`)
        .setDescription(lines.join('\n'))
        .setColor(0x5865F2)
        .setFooter({ text: `${apps.length} candidature(s)` });

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }
  },
};