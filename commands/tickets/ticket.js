const {
  SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder,
  ActionRowBuilder, ButtonBuilder, ButtonStyle,
  ChannelType, PermissionsBitField,
  ModalBuilder, TextInputBuilder, TextInputStyle,
  StringSelectMenuBuilder,
} = require('discord.js');
const { getGuildConfig, createTicket, getTicket, closeTicket, getOpenTickets, countTickets } = require('../../utils/database');
const { COLORS } = require('../../utils/logger');
const { errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('ticket')
    .setDescription('🎫 Système de tickets')
    .addSubcommand(sub =>
      sub.setName('panel')
        .setDescription('Créer un panneau de tickets dans ce salon')
        .addStringOption(opt => opt.setName('titre').setDescription('Titre du panneau'))
        .addStringOption(opt => opt.setName('description').setDescription('Description du panneau'))
    )
    .addSubcommand(sub =>
      sub.setName('close')
        .setDescription('Fermer le ticket actuel')
        .addStringOption(opt => opt.setName('raison').setDescription('Raison de la fermeture'))
    )
    .addSubcommand(sub =>
      sub.setName('add')
        .setDescription('Ajouter un utilisateur au ticket')
        .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur à ajouter').setRequired(true))
    )
    .addSubcommand(sub =>
      sub.setName('remove')
        .setDescription('Retirer un utilisateur du ticket')
        .addUserOption(opt => opt.setName('utilisateur').setDescription('L\'utilisateur à retirer').setRequired(true))
    )
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageChannels),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();

    if (sub === 'panel') {
      await handlePanel(interaction);
    } else if (sub === 'close') {
      await handleClose(interaction);
    } else if (sub === 'add') {
      await handleAddUser(interaction);
    } else if (sub === 'remove') {
      await handleRemoveUser(interaction);
    }
  },

  // Gestion des interactions de boutons et menus
  async handleButton(interaction) {
    if (interaction.customId === 'ticket_create') {
      await handleTicketCreate(interaction);
    } else if (interaction.customId === 'ticket_close') {
      await handleClose(interaction);
    } else if (interaction.customId === 'ticket_close_confirm') {
      await handleCloseConfirm(interaction);
    } else if (interaction.customId === 'ticket_close_cancel') {
      await interaction.update({ content: '❌ Fermeture annulée.', components: [], embeds: [] });
    }
  },

  async handleSelectMenu(interaction) {
    if (interaction.customId === 'ticket_category_select') {
      await handleCategorySelect(interaction);
    }
  },
};

// ===================================
// Panel de tickets
// ===================================
async function handlePanel(interaction) {
  const title = interaction.options.getString('titre') || '🎫 Support & Assistance';
  const description = interaction.options.getString('description') ||
    'Besoin d\'aide ? Cliquez sur le bouton ci-dessous pour créer un ticket.\n\n' +
    '**📌 Avant de créer un ticket :**\n' +
    '• Vérifiez si votre question n\'a pas déjà été posée\n' +
    '• Préparez une description détaillée de votre problème\n' +
    '• Soyez patient, notre équipe vous répondra dès que possible';

  const embed = new EmbedBuilder()
    .setTitle(title)
    .setDescription(description)
    .setColor(COLORS.BLUE)
    .setFooter({ text: 'Support Ticket System' })
    .setTimestamp();

  const selectMenu = new StringSelectMenuBuilder()
    .setCustomId('ticket_category_select')
    .setPlaceholder('📂 Choisissez une catégorie...')
    .addOptions(
      { label: '❓ Question générale', value: 'question', description: 'Posez une question à l\'équipe' },
      { label: '🐛 Signaler un bug', value: 'bug', description: 'Signalez un problème technique' },
      { label: '💡 Suggestion', value: 'suggestion', description: 'Proposez une idée ou amélioration' },
      { label: '🚨 Signalement', value: 'report', description: 'Signalez un utilisateur ou un abus' },
      { label: '📦 Autre', value: 'other', description: 'Autre demande de support' },
    );

  const selectRow = new ActionRowBuilder().addComponents(selectMenu);

  const button = new ButtonBuilder()
    .setCustomId('ticket_create')
    .setLabel('📩 Créer un ticket rapide')
    .setStyle(ButtonStyle.Secondary);

  const buttonRow = new ActionRowBuilder().addComponents(button);

  await interaction.channel.send({ embeds: [embed], components: [selectRow, buttonRow] });
  await interaction.reply({ content: '✅ Panneau de tickets créé !', ephemeral: true });
}

// ===================================
// Création de ticket (via menu déroulant)
// ===================================
async function handleCategorySelect(interaction) {
  const category = interaction.values[0];
  const categoryNames = {
    question: '❓ Question',
    bug: '🐛 Bug',
    suggestion: '💡 Suggestion',
    report: '🚨 Signalement',
    other: '📦 Autre',
  };

  await createTicketChannel(interaction, categoryNames[category] || 'Support');
}

// ===================================
// Création de ticket (via bouton)
// ===================================
async function handleTicketCreate(interaction) {
  await createTicketChannel(interaction, 'Support');
}

// ===================================
// Création du salon de ticket
// ===================================
async function createTicketChannel(interaction, subject) {
  const config = getGuildConfig(interaction.guild.id);

  // Vérifier les tickets ouverts (max 3)
  const openTickets = getOpenTickets(interaction.guild.id, interaction.user.id);
  if (openTickets.length >= 3) {
    return interaction.reply(errorReply('❌ Vous avez déjà 3 tickets ouverts. Veuillez en fermer un avant d\'en créer un nouveau.'));
  }

  await interaction.deferReply({ ephemeral: true });

  try {
    const ticketNumber = countTickets(interaction.guild.id) + 1;
    const channelName = `ticket-${ticketNumber.toString().padStart(4, '0')}`;

    // Permissions du salon
    const permissionOverwrites = [
      {
        id: interaction.guild.id, // @everyone
        deny: [PermissionsBitField.Flags.ViewChannel],
      },
      {
        id: interaction.user.id,
        allow: [
          PermissionsBitField.Flags.ViewChannel,
          PermissionsBitField.Flags.SendMessages,
          PermissionsBitField.Flags.AttachFiles,
          PermissionsBitField.Flags.ReadMessageHistory,
        ],
      },
      {
        id: interaction.client.user.id,
        allow: [
          PermissionsBitField.Flags.ViewChannel,
          PermissionsBitField.Flags.SendMessages,
          PermissionsBitField.Flags.ManageChannels,
          PermissionsBitField.Flags.ReadMessageHistory,
        ],
      },
    ];

    // Ajouter le rôle modérateur si configuré
    if (config?.mod_role_id) {
      permissionOverwrites.push({
        id: config.mod_role_id,
        allow: [
          PermissionsBitField.Flags.ViewChannel,
          PermissionsBitField.Flags.SendMessages,
          PermissionsBitField.Flags.ReadMessageHistory,
        ],
      });
    }

    // Créer le salon
    const ticketChannel = await interaction.guild.channels.create({
      name: channelName,
      type: ChannelType.GuildText,
      parent: config?.ticket_category_id || null,
      permissionOverwrites,
      topic: `Ticket de ${interaction.user.tag} — ${subject}`,
    });

    // Sauvegarder en base
    createTicket(interaction.guild.id, ticketChannel.id, interaction.user.id, subject);

    // Message d'ouverture
    const openEmbed = new EmbedBuilder()
      .setTitle(`🎫 Ticket #${ticketNumber} — ${subject}`)
      .setColor(COLORS.GREEN)
      .setDescription(
        `Bienvenue ${interaction.user} !\n\n` +
        `Décrivez votre problème ci-dessous et un membre de l'équipe vous répondra dès que possible.\n\n` +
        `**Catégorie :** ${subject}`
      )
      .setFooter({ text: `Ticket ouvert par ${interaction.user.tag}` })
      .setTimestamp();

    const closeButton = new ButtonBuilder()
      .setCustomId('ticket_close')
      .setLabel('🔒 Fermer le ticket')
      .setStyle(ButtonStyle.Danger);

    const row = new ActionRowBuilder().addComponents(closeButton);

    await ticketChannel.send({
      content: `${interaction.user}${config?.mod_role_id ? ` | <@&${config.mod_role_id}>` : ''}`,
      embeds: [openEmbed],
      components: [row],
    });

    await interaction.editReply({ content: `✅ Ticket créé ! ${ticketChannel}` });
  } catch (error) {
    console.error('[TICKET CREATE]', error);
    await interaction.editReply({ content: '❌ Erreur lors de la création du ticket.' });
  }
}

// ===================================
// Fermeture de ticket
// ===================================
async function handleClose(interaction) {
  const ticket = getTicket(interaction.channel.id);
  if (!ticket || ticket.status !== 'open') {
    return interaction.reply(errorReply('❌ Ce salon n\'est pas un ticket ouvert.'));
  }

  const embed = new EmbedBuilder()
    .setTitle('🔒 Fermer le ticket ?')
    .setDescription('Êtes-vous sûr de vouloir fermer ce ticket ? Le salon sera supprimé après 10 secondes.')
    .setColor(COLORS.ORANGE);

  const row = new ActionRowBuilder().addComponents(
    new ButtonBuilder().setCustomId('ticket_close_confirm').setLabel('✅ Confirmer').setStyle(ButtonStyle.Danger),
    new ButtonBuilder().setCustomId('ticket_close_cancel').setLabel('❌ Annuler').setStyle(ButtonStyle.Secondary),
  );

  await interaction.reply({ embeds: [embed], components: [row] });
}

async function handleCloseConfirm(interaction) {
  const ticket = getTicket(interaction.channel.id);
  if (!ticket) return;

  // Marquer comme fermé
  closeTicket(interaction.channel.id);

  const config = getGuildConfig(interaction.guild.id);

  // Sauvegarder un log du ticket
  if (config?.ticket_log_channel_id) {
    try {
      const logChannel = interaction.guild.channels.cache.get(config.ticket_log_channel_id);
      if (logChannel) {
        // Récupérer les messages
        const messages = await interaction.channel.messages.fetch({ limit: 100 });
        const transcript = messages.reverse().map(m =>
          `[${m.createdAt.toLocaleString('fr-FR')}] ${m.author.tag}: ${m.content || '[embed/fichier]'}`
        ).join('\n');

        const logEmbed = new EmbedBuilder()
          .setTitle(`📋 Ticket Fermé — #${interaction.channel.name}`)
          .setColor(COLORS.RED)
          .addFields(
            { name: '👤 Créé par', value: `<@${ticket.user_id}>`, inline: true },
            { name: '🔒 Fermé par', value: `${interaction.user}`, inline: true },
            { name: '📂 Sujet', value: ticket.subject, inline: true },
            { name: '📅 Ouvert le', value: `<t:${Math.floor(new Date(ticket.created_at).getTime() / 1000)}:f>`, inline: true },
          )
          .setTimestamp();

        // Envoyer le transcript en fichier si trop long
        if (transcript.length > 2000) {
          const buffer = Buffer.from(transcript, 'utf-8');
          await logChannel.send({
            embeds: [logEmbed],
            files: [{ attachment: buffer, name: `transcript-${interaction.channel.name}.txt` }],
          });
        } else {
          logEmbed.addFields({ name: '📝 Transcript', value: transcript || '*Aucun message*' });
          await logChannel.send({ embeds: [logEmbed] });
        }
      }
    } catch (error) {
      console.error('[TICKET LOG]', error);
    }
  }

  await interaction.update({
    content: '🔒 **Ticket fermé.** Ce salon sera supprimé dans 10 secondes...',
    embeds: [],
    components: [],
  });

  setTimeout(async () => {
    try {
      await interaction.channel.delete();
    } catch (error) {
      console.error('[TICKET DELETE]', error);
    }
  }, 10_000);
}

// ===================================
// Ajouter / Retirer un utilisateur
// ===================================
async function handleAddUser(interaction) {
  const ticket = getTicket(interaction.channel.id);
  if (!ticket) return interaction.reply(errorReply('❌ Ce salon n\'est pas un ticket.'));

  const target = interaction.options.getUser('utilisateur');

  await interaction.channel.permissionOverwrites.edit(target, {
    ViewChannel: true,
    SendMessages: true,
    ReadMessageHistory: true,
  });

  await interaction.reply({ content: `✅ ${target} a été ajouté au ticket.` });
}

async function handleRemoveUser(interaction) {
  const ticket = getTicket(interaction.channel.id);
  if (!ticket) return interaction.reply(errorReply('❌ Ce salon n\'est pas un ticket.'));

  const target = interaction.options.getUser('utilisateur');

  if (target.id === ticket.user_id) {
    return interaction.reply(errorReply('❌ Vous ne pouvez pas retirer le créateur du ticket.'));
  }

  await interaction.channel.permissionOverwrites.delete(target);
  await interaction.reply({ content: `✅ ${target} a été retiré du ticket.` });
}
