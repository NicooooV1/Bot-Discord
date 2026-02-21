// ===================================
// Ultra Suite — /profile
// Système de profils sociaux
// ===================================

const { SlashCommandBuilder, EmbedBuilder, AttachmentBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'social',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('profile')
    .setDescription('Gérer votre profil social')
    .addSubcommand((s) =>
      s.setName('view').setDescription('Voir un profil')
        .addUserOption((o) => o.setName('utilisateur').setDescription('Utilisateur')),
    )
    .addSubcommand((s) =>
      s.setName('bio').setDescription('Modifier votre bio')
        .addStringOption((o) => o.setName('texte').setDescription('Votre bio').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('color').setDescription('Couleur de l\'embed')
        .addStringOption((o) => o.setName('hex').setDescription('Couleur hex (#FF0000)').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('birthday').setDescription('Définir votre anniversaire')
        .addIntegerOption((o) => o.setName('jour').setDescription('Jour').setRequired(true).setMinValue(1).setMaxValue(31))
        .addIntegerOption((o) => o.setName('mois').setDescription('Mois').setRequired(true).setMinValue(1).setMaxValue(12)),
    )
    .addSubcommand((s) =>
      s.setName('badges').setDescription('Voir vos badges'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    const ensureProfile = async (userId) => {
      const existing = await db('social_profiles').where({ guild_id: guildId, user_id: userId }).first();
      if (!existing) {
        await db('social_profiles').insert({ guild_id: guildId, user_id: userId, bio: '', color: '#3498DB', birthday: null, badges: '[]', reputation: 0, married_to: null });
        return db('social_profiles').where({ guild_id: guildId, user_id: userId }).first();
      }
      return existing;
    };

    switch (sub) {
      case 'view': {
        const user = interaction.options.getUser('utilisateur') || interaction.user;
        const profile = await ensureProfile(user.id);
        const member = await interaction.guild.members.fetch(user.id).catch(() => null);

        // Get XP/level info
        const xpData = await db('user_levels').where({ guild_id: guildId, user_id: user.id }).first();
        const ecoData = await db('users').where({ guild_id: guildId, user_id: user.id }).first();
        const sanctions = await db('sanctions').where({ guild_id: guildId, user_id: user.id }).count('* as count').first();

        const badges = [];
        try { badges.push(...JSON.parse(profile.badges || '[]')); } catch (e) {}

        // Auto-badges
        if (member?.premiumSince) badges.push('💎 Booster');
        if (xpData?.level >= 50) badges.push('🌟 Level 50+');
        if (xpData?.level >= 100) badges.push('👑 Level 100+');
        if (ecoData?.balance >= 100000) badges.push('💰 Riche');
        if (profile.reputation >= 50) badges.push('⭐ Réputé');

        const color = parseInt((profile.color || '#3498DB').replace('#', ''), 16);

        const embed = new EmbedBuilder()
          .setTitle(`👤 Profil de ${user.tag}`)
          .setColor(color)
          .setThumbnail(user.displayAvatarURL({ size: 256 }))
          .addFields(
            { name: '📝 Bio', value: profile.bio || '*Aucune bio définie*' },
            { name: '⭐ Réputation', value: String(profile.reputation || 0), inline: true },
            { name: '📊 Niveau', value: String(xpData?.level || 0), inline: true },
            { name: '💰 Balance', value: String(ecoData?.balance || 0), inline: true },
            { name: '🎂 Anniversaire', value: profile.birthday || 'Non défini', inline: true },
            { name: '💍 Marié(e) à', value: profile.married_to ? `<@${profile.married_to}>` : 'Célibataire', inline: true },
            { name: '⚠️ Sanctions', value: String(sanctions?.count || 0), inline: true },
          );

        if (badges.length) embed.addFields({ name: '🏅 Badges', value: badges.join(' ') });
        if (member) embed.addFields({ name: '📅 Rejoint le', value: `<t:${Math.floor(member.joinedTimestamp / 1000)}:R>`, inline: true });

        return interaction.reply({ embeds: [embed] });
      }

      case 'bio': {
        const bio = interaction.options.getString('texte').substring(0, 256);
        await ensureProfile(interaction.user.id);
        await db('social_profiles').where({ guild_id: guildId, user_id: interaction.user.id }).update({ bio });
        return interaction.reply({ content: `✅ Bio mise à jour : ${bio}`, ephemeral: true });
      }

      case 'color': {
        const hex = interaction.options.getString('hex');
        if (!/^#?[0-9A-Fa-f]{6}$/.test(hex)) return interaction.reply({ content: '❌ Format invalide. Utilisez #RRGGBB.', ephemeral: true });
        const color = hex.startsWith('#') ? hex : '#' + hex;
        await ensureProfile(interaction.user.id);
        await db('social_profiles').where({ guild_id: guildId, user_id: interaction.user.id }).update({ color });
        return interaction.reply({ content: `✅ Couleur de profil : ${color}`, ephemeral: true });
      }

      case 'birthday': {
        const day = interaction.options.getInteger('jour');
        const month = interaction.options.getInteger('mois');
        const bd = `${String(day).padStart(2, '0')}/${String(month).padStart(2, '0')}`;
        await ensureProfile(interaction.user.id);
        await db('social_profiles').where({ guild_id: guildId, user_id: interaction.user.id }).update({ birthday: bd });
        return interaction.reply({ content: `✅ Anniversaire défini : 🎂 ${bd}`, ephemeral: true });
      }

      case 'badges': {
        const profile = await ensureProfile(interaction.user.id);
        const badges = [];
        try { badges.push(...JSON.parse(profile.badges || '[]')); } catch (e) {}
        if (!badges.length) return interaction.reply({ content: 'Vous n\'avez aucun badge pour le moment.', ephemeral: true });
        return interaction.reply({
          embeds: [new EmbedBuilder().setTitle('🏅 Vos badges').setColor(0xF1C40F).setDescription(badges.join('\n'))],
          ephemeral: true,
        });
      }
    }
  },
};
