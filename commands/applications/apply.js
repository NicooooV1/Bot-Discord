// ===================================
// Ultra Suite — Applications: /apply
// Système de candidatures
// ===================================

const {
  SlashCommandBuilder,
  PermissionFlagsBits,
  ModalBuilder,
  TextInputBuilder,
  TextInputStyle,
  ActionRowBuilder,
} = require('discord.js');
const { getDb } = require('../../database');
const { successEmbed, errorEmbed, createEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'applications',
  cooldown: 10,
  data: new SlashCommandBuilder()
    .setName('apply')
    .setDescription('Gestion des candidatures')
    .addSubcommand((sub) => sub.setName('start').setDescription('Commencer une candidature'))
    .addSubcommand((sub) =>
      sub
        .setName('review')
        .setDescription('Voir les candidatures en attente')
    )
    .addSubcommand((sub) =>
      sub
        .setName('accept')
        .setDescription('Accepter une candidature')
        .addIntegerOption((opt) => opt.setName('id').setDescription('ID de la candidature').setRequired(true))
    )
    .addSubcommand((sub) =>
      sub
        .setName('deny')
        .setDescription('Refuser une candidature')
        .addIntegerOption((opt) => opt.setName('id').setDescription('ID de la candidature').setRequired(true))
        .addStringOption((opt) => opt.setName('raison').setDescription('Raison du refus'))
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();

    switch (sub) {
      case 'start': {
        // Vérifier si une candidature est déjà ouverte
        const existing = await db('applications')
          .where({ guild_id: interaction.guild.id, applicant_id: interaction.user.id, status: 'pending' })
          .first();

        if (existing) {
          return interaction.reply({ embeds: [errorEmbed('❌ Tu as déjà une candidature en attente.')], ephemeral: true });
        }

        // Ouvrir un modal
        const modal = new ModalBuilder()
          .setCustomId('apply_modal')
          .setTitle('📝 Candidature');

        const q1 = new TextInputBuilder()
          .setCustomId('apply_q1')
          .setLabel('Présentez-vous brièvement')
          .setStyle(TextInputStyle.Paragraph)
          .setRequired(true)
          .setMaxLength(1000);

        const q2 = new TextInputBuilder()
          .setCustomId('apply_q2')
          .setLabel('Pourquoi souhaitez-vous postuler ?')
          .setStyle(TextInputStyle.Paragraph)
          .setRequired(true)
          .setMaxLength(1000);

        const q3 = new TextInputBuilder()
          .setCustomId('apply_q3')
          .setLabel('Quelle est votre expérience ?')
          .setStyle(TextInputStyle.Paragraph)
          .setRequired(true)
          .setMaxLength(1000);

        modal.addComponents(
          new ActionRowBuilder().addComponents(q1),
          new ActionRowBuilder().addComponents(q2),
          new ActionRowBuilder().addComponents(q3)
        );

        return interaction.showModal(modal);
      }

      case 'review': {
        if (!interaction.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
          return interaction.reply({ embeds: [errorEmbed('❌ Permission manquante.')], ephemeral: true });
        }

        const apps = await db('applications')
          .where({ guild_id: interaction.guild.id, status: 'pending' })
          .orderBy('created_at', 'desc')
          .limit(10);

        if (apps.length === 0) {
          return interaction.reply({ content: '📭 Aucune candidature en attente.', ephemeral: true });
        }

        const list = apps.map(
          (a) => `**#${a.id}** — <@${a.applicant_id}> · <t:${Math.floor(new Date(a.created_at).getTime() / 1000)}:R>`
        );

        const embed = createEmbed('primary')
          .setTitle('📝 Candidatures en attente')
          .setDescription(list.join('\n'));

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'accept': {
        if (!interaction.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
          return interaction.reply({ embeds: [errorEmbed('❌ Permission manquante.')], ephemeral: true });
        }

        const id = interaction.options.getInteger('id');
        const updated = await db('applications')
          .where({ id, guild_id: interaction.guild.id, status: 'pending' })
          .update({ status: 'accepted', reviewer_id: interaction.user.id, updated_at: new Date().toISOString() });

        if (!updated) return interaction.reply({ embeds: [errorEmbed('❌ Candidature introuvable.')], ephemeral: true });

        const app = await db('applications').where('id', id).first();
        try {
          const user = await interaction.client.users.fetch(app.applicant_id);
          await user.send('✅ Votre candidature a été **acceptée** ! Félicitations !').catch(() => {});
        } catch {}

        return interaction.reply({ embeds: [successEmbed(`✅ Candidature #${id} acceptée.`)] });
      }

      case 'deny': {
        if (!interaction.member.permissions.has(PermissionFlagsBits.ManageGuild)) {
          return interaction.reply({ embeds: [errorEmbed('❌ Permission manquante.')], ephemeral: true });
        }

        const id = interaction.options.getInteger('id');
        const reason = interaction.options.getString('raison') || 'Aucune raison fournie.';

        const updated = await db('applications')
          .where({ id, guild_id: interaction.guild.id, status: 'pending' })
          .update({ status: 'rejected', reviewer_id: interaction.user.id, updated_at: new Date().toISOString() });

        if (!updated) return interaction.reply({ embeds: [errorEmbed('❌ Candidature introuvable.')], ephemeral: true });

        const app = await db('applications').where('id', id).first();
        try {
          const user = await interaction.client.users.fetch(app.applicant_id);
          await user.send(`❌ Votre candidature a été **refusée**.\nRaison : ${reason}`).catch(() => {});
        } catch {}

        return interaction.reply({ embeds: [successEmbed(`✅ Candidature #${id} refusée.`)] });
      }
    }
  },
};
