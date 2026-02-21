// ===================================
// Ultra Suite — /verify
// Système de vérification
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'verify',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('verify')
    .setDescription('Système de vérification')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addSubcommand((s) =>
      s.setName('setup').setDescription('Configurer la vérification')
        .addChannelOption((o) => o.setName('salon').setDescription('Salon de vérification').setRequired(true))
        .addRoleOption((o) => o.setName('role').setDescription('Rôle à donner aux vérifiés').setRequired(true))
        .addStringOption((o) => o.setName('type').setDescription('Type de vérification').setRequired(true).addChoices(
          { name: 'Bouton', value: 'button' },
          { name: 'Captcha', value: 'captcha' },
          { name: 'Règlement', value: 'rules' },
          { name: 'Question', value: 'question' },
        )),
    )
    .addSubcommand((s) =>
      s.setName('panel').setDescription('Envoyer le panel de vérification'),
    )
    .addSubcommand((s) =>
      s.setName('config').setDescription('Voir/modifier la config')
        .addStringOption((o) => o.setName('message').setDescription('Message personnalisé du panel'))
        .addIntegerOption((o) => o.setName('timeout').setDescription('Timeout en minutes avant kick')),
    )
    .addSubcommand((s) =>
      s.setName('stats').setDescription('Statistiques de vérification'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    switch (sub) {
      case 'setup': {
        const channel = interaction.options.getChannel('salon');
        const role = interaction.options.getRole('role');
        const type = interaction.options.getString('type');

        await db('verification_config').insert({
          guild_id: guildId,
          channel_id: channel.id,
          role_id: role.id,
          type,
          message: 'Cliquez sur le bouton ci-dessous pour vous vérifier et accéder au serveur.',
          timeout_minutes: 10,
          enabled: true,
        }).onConflict('guild_id').merge();

        return interaction.reply({
          content: `✅ Vérification configurée !\n📌 Salon : ${channel}\n🏷️ Rôle : ${role}\n🔒 Type : ${type}\n\nUtilisez \`/verify panel\` pour envoyer le panel.`,
          ephemeral: true,
        });
      }

      case 'panel': {
        const config = await db('verification_config').where({ guild_id: guildId }).first();
        if (!config) return interaction.reply({ content: '❌ Vérification non configurée. Utilisez `/verify setup`.', ephemeral: true });

        const channel = await interaction.guild.channels.fetch(config.channel_id).catch(() => null);
        if (!channel) return interaction.reply({ content: '❌ Salon de vérification introuvable.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle('✅ Vérification requise')
          .setColor(0x2ECC71)
          .setDescription(config.message || 'Cliquez sur le bouton ci-dessous pour vous vérifier.')
          .addFields({ name: '🔒 Type', value: config.type === 'button' ? 'Bouton' : config.type === 'captcha' ? 'Captcha' : config.type === 'rules' ? 'Règlement' : 'Question' })
          .setTimestamp();

        const row = new ActionRowBuilder();

        switch (config.type) {
          case 'button':
            row.addComponents(new ButtonBuilder().setCustomId('verify_button').setLabel('✅ Se vérifier').setStyle(ButtonStyle.Success));
            break;
          case 'captcha':
            row.addComponents(new ButtonBuilder().setCustomId('verify_captcha').setLabel('🔐 Résoudre le captcha').setStyle(ButtonStyle.Primary));
            break;
          case 'rules':
            row.addComponents(new ButtonBuilder().setCustomId('verify_rules').setLabel('📜 J\'accepte le règlement').setStyle(ButtonStyle.Success));
            break;
          case 'question':
            row.addComponents(new ButtonBuilder().setCustomId('verify_question').setLabel('❓ Répondre à la question').setStyle(ButtonStyle.Primary));
            break;
        }

        await channel.send({ embeds: [embed], components: [row] });
        return interaction.reply({ content: `✅ Panel envoyé dans ${channel} !`, ephemeral: true });
      }

      case 'config': {
        const message = interaction.options.getString('message');
        const timeout = interaction.options.getInteger('timeout');

        const config = await db('verification_config').where({ guild_id: guildId }).first();
        if (!config) return interaction.reply({ content: '❌ Vérification non configurée.', ephemeral: true });

        const updates = {};
        if (message) updates.message = message;
        if (timeout !== null) updates.timeout_minutes = timeout;

        if (Object.keys(updates).length) {
          await db('verification_config').where({ guild_id: guildId }).update(updates);
          return interaction.reply({ content: '✅ Configuration mise à jour.', ephemeral: true });
        }

        const embed = new EmbedBuilder()
          .setTitle('🔒 Configuration de vérification')
          .setColor(0x2ECC71)
          .addFields(
            { name: 'Salon', value: `<#${config.channel_id}>`, inline: true },
            { name: 'Rôle', value: `<@&${config.role_id}>`, inline: true },
            { name: 'Type', value: config.type, inline: true },
            { name: 'Timeout', value: `${config.timeout_minutes} min`, inline: true },
            { name: 'Statut', value: config.enabled ? '✅ Actif' : '❌ Inactif', inline: true },
          );

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'stats': {
        const config = await db('verification_config').where({ guild_id: guildId }).first();
        if (!config) return interaction.reply({ content: '❌ Vérification non configurée.', ephemeral: true });

        // Count verified members (those with the verify role)
        await interaction.guild.members.fetch();
        const verified = interaction.guild.members.cache.filter((m) => m.roles.cache.has(config.role_id)).size;
        const total = interaction.guild.memberCount;

        const embed = new EmbedBuilder()
          .setTitle('📊 Statistiques de vérification')
          .setColor(0x2ECC71)
          .addFields(
            { name: '✅ Vérifiés', value: String(verified), inline: true },
            { name: '❌ Non vérifiés', value: String(total - verified), inline: true },
            { name: '👥 Total', value: String(total), inline: true },
            { name: '📈 Taux', value: `${total > 0 ? Math.round(verified / total * 100) : 0}%`, inline: true },
          );

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }
    }
  },
};
