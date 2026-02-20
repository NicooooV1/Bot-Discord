// ===================================
// Ultra Suite — Composants Applications
// Modal submit + boutons accept/reject
// ===================================

const { EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  prefix: 'application-',
  type: 'mixed', // modal + button
  module: 'applications',

  async execute(interaction) {
    const customId = interaction.customId;

    if (customId === 'application-submit') return handleSubmit(interaction);
    if (customId.startsWith('application-accept-')) return handleDecision(interaction, 'ACCEPTED');
    if (customId.startsWith('application-reject-')) return handleDecision(interaction, 'REJECTED');
  },
};

async function handleSubmit(interaction) {
  const guildId = interaction.guildId;
  const db = getDb();
  const config = await configService.get(guildId);
  const appConfig = config.applications || {};

  if (!appConfig.channel) {
    return interaction.reply({ content: '❌ Les candidatures ne sont pas configurées.', ephemeral: true });
  }

  await interaction.deferReply({ ephemeral: true });

  // Récupérer les réponses
  const questions = appConfig.questions || [];
  const answers = [];
  for (let i = 0; i < questions.length; i++) {
    try {
      answers.push({
        question: questions[i],
        answer: interaction.fields.getTextInputValue(`q${i}`),
      });
    } catch { break; }
  }

  // Sauvegarder en DB
  const [id] = await db('applications').insert({
    guild_id: guildId,
    user_id: interaction.user.id,
    status: 'PENDING',
    answers: JSON.stringify(answers),
  });

  // Envoyer dans le channel de review
  const channel = interaction.guild.channels.cache.get(appConfig.channel);
  if (!channel) {
    return interaction.editReply({ content: '❌ Channel de candidatures introuvable.' });
  }

  const embed = new EmbedBuilder()
    .setTitle(`📝 Candidature #${id}`)
    .setAuthor({ name: interaction.user.tag, iconURL: interaction.user.displayAvatarURL() })
    .setColor(0xFEE75C)
    .setTimestamp();

  for (const a of answers) {
    embed.addFields({ name: a.question, value: a.answer.slice(0, 1024), inline: false });
  }

  embed.addFields(
    { name: '📅 Compte créé', value: `<t:${Math.floor(interaction.user.createdTimestamp / 1000)}:R>`, inline: true },
    { name: '📥 Rejoint le', value: interaction.member?.joinedAt ? `<t:${Math.floor(interaction.member.joinedTimestamp / 1000)}:R>` : '?', inline: true },
  );

  const row = new ActionRowBuilder().addComponents(
    new ButtonBuilder().setCustomId(`application-accept-${id}`).setLabel('Accepter').setStyle(ButtonStyle.Success).setEmoji('✅'),
    new ButtonBuilder().setCustomId(`application-reject-${id}`).setLabel('Refuser').setStyle(ButtonStyle.Danger).setEmoji('❌'),
  );

  const ping = appConfig.reviewerRole ? `<@&${appConfig.reviewerRole}>` : '';
  await channel.send({ content: ping || undefined, embeds: [embed], components: [row] });

  await interaction.editReply({ content: '✅ Votre candidature a été soumise ! Vous serez notifié de la décision.' });
}

async function handleDecision(interaction, status) {
  const db = getDb();
  const guildId = interaction.guildId;
  const appId = interaction.customId.split('-').pop();

  const app = await db('applications').where('id', appId).where('guild_id', guildId).first();
  if (!app) return interaction.reply({ content: '❌ Candidature introuvable.', ephemeral: true });
  if (app.status !== 'PENDING') return interaction.reply({ content: '❌ Candidature déjà traitée.', ephemeral: true });

  await db('applications').where('id', appId).update({
    status,
    reviewed_by: interaction.user.id,
    reviewed_at: new Date(),
  });

  const emoji = status === 'ACCEPTED' ? '✅' : '❌';
  const label = status === 'ACCEPTED' ? 'acceptée' : 'refusée';
  const color = status === 'ACCEPTED' ? 0x57F287 : 0xED4245;

  // Mettre à jour le message
  const embed = EmbedBuilder.from(interaction.message.embeds[0])
    .setColor(color)
    .setFooter({ text: `${emoji} ${label} par ${interaction.user.tag}` });

  await interaction.update({ embeds: [embed], components: [] });

  // DM au candidat
  try {
    const user = await interaction.client.users.fetch(app.user_id);
    await user.send({
      content: `${emoji} Votre candidature sur **${interaction.guild.name}** a été **${label}** par ${interaction.user.tag}.`,
    }).catch(() => {});
  } catch { /* DMs fermés */ }
}