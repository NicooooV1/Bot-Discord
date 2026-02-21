// ===================================
// Ultra Suite — /marry
// Système de mariage
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, ComponentType } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'social',
  cooldown: 30,

  data: new SlashCommandBuilder()
    .setName('marry')
    .setDescription('Système de mariage')
    .addSubcommand((s) =>
      s.setName('propose').setDescription('Demander en mariage')
        .addUserOption((o) => o.setName('utilisateur').setDescription('Votre âme sœur').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('divorce').setDescription('Divorcer'),
    )
    .addSubcommand((s) =>
      s.setName('status').setDescription('Voir votre statut marital'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    const getProfile = async (userId) => {
      const p = await db('social_profiles').where({ guild_id: guildId, user_id: userId }).first();
      if (!p) await db('social_profiles').insert({ guild_id: guildId, user_id: userId });
      return db('social_profiles').where({ guild_id: guildId, user_id: userId }).first();
    };

    switch (sub) {
      case 'propose': {
        const target = interaction.options.getUser('utilisateur');
        if (target.id === interaction.user.id || target.bot) return interaction.reply({ content: '❌ Cible invalide.', ephemeral: true });

        const authorProfile = await getProfile(interaction.user.id);
        const targetProfile = await getProfile(target.id);

        if (authorProfile?.married_to) return interaction.reply({ content: '❌ Vous êtes déjà marié(e) !', ephemeral: true });
        if (targetProfile?.married_to) return interaction.reply({ content: '❌ Cette personne est déjà mariée !', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle('💍 Demande en mariage')
          .setColor(0xFF69B4)
          .setDescription(`${interaction.user} demande ${target} en mariage !\n\n${target}, acceptez-vous ? 💕`)
          .setTimestamp();

        const row = new ActionRowBuilder().addComponents(
          new ButtonBuilder().setCustomId('marry_yes').setLabel('Oui, je le veux ! 💕').setStyle(ButtonStyle.Success),
          new ButtonBuilder().setCustomId('marry_no').setLabel('Non merci 💔').setStyle(ButtonStyle.Danger),
        );

        const msg = await interaction.reply({ embeds: [embed], components: [row], fetchReply: true });

        const collector = msg.createMessageComponentCollector({ componentType: ComponentType.Button, time: 60000 });

        collector.on('collect', async (btn) => {
          if (btn.user.id !== target.id) return btn.reply({ content: '❌ Seul(e) le/la demandé(e) peut répondre.', ephemeral: true });

          collector.stop();
          if (btn.customId === 'marry_yes') {
            await db('social_profiles').where({ guild_id: guildId, user_id: interaction.user.id }).update({ married_to: target.id });
            await db('social_profiles').where({ guild_id: guildId, user_id: target.id }).update({ married_to: interaction.user.id });

            const successEmbed = new EmbedBuilder()
              .setTitle('💒 Mariage célébré !')
              .setColor(0xFF69B4)
              .setDescription(`🎉 ${interaction.user} et ${target} sont maintenant mariés ! 💕💍\n\nFélicitations aux mariés ! 🥂`)
              .setTimestamp();

            return btn.update({ embeds: [successEmbed], components: [] });
          } else {
            const rejectEmbed = new EmbedBuilder()
              .setTitle('💔 Demande refusée')
              .setColor(0x95A5A6)
              .setDescription(`${target} a refusé la demande de ${interaction.user}. 😢`);
            return btn.update({ embeds: [rejectEmbed], components: [] });
          }
        });

        collector.on('end', async (collected, reason) => {
          if (reason === 'time') {
            await msg.edit({ content: '⏰ La demande a expiré.', components: [] }).catch(() => {});
          }
        });
        break;
      }

      case 'divorce': {
        const profile = await getProfile(interaction.user.id);
        if (!profile?.married_to) return interaction.reply({ content: '❌ Vous n\'êtes pas marié(e).', ephemeral: true });

        const partnerId = profile.married_to;
        await db('social_profiles').where({ guild_id: guildId, user_id: interaction.user.id }).update({ married_to: null });
        await db('social_profiles').where({ guild_id: guildId, user_id: partnerId }).update({ married_to: null });

        return interaction.reply({
          embeds: [new EmbedBuilder().setTitle('💔 Divorce').setColor(0x95A5A6).setDescription(`${interaction.user} a divorcé de <@${partnerId}>. 😢`)],
        });
      }

      case 'status': {
        const profile = await getProfile(interaction.user.id);
        if (!profile?.married_to) return interaction.reply({ content: '💔 Vous êtes célibataire.', ephemeral: true });

        return interaction.reply({
          embeds: [new EmbedBuilder()
            .setTitle('💍 Statut marital')
            .setColor(0xFF69B4)
            .setDescription(`Vous êtes marié(e) à <@${profile.married_to}> 💕`)],
          ephemeral: true,
        });
      }
    }
  },
};
