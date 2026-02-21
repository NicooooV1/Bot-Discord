// ===================================
// Ultra Suite — /premium
// Système premium
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

const TIERS = {
  free: { name: '🆓 Gratuit', color: 0x95A5A6, features: ['5 commandes custom', '3 tags', 'XP basique', 'Modération standard'] },
  bronze: { name: '🥉 Bronze', color: 0xCD7F32, features: ['15 commandes custom', '10 tags', 'XP avancé', 'Automod complet', 'Backups (3 max)', 'Logs avancés'] },
  silver: { name: '🥈 Argent', color: 0xC0C0C0, features: ['50 commandes custom', '25 tags', 'XP + cartes niv.', 'Musique HQ', 'Backups (10 max)', 'Dashboard', 'Giveaways avancés'] },
  gold: { name: '🥇 Or', color: 0xFFD700, features: ['Illimité', 'Priorité support', 'API access', 'Custom branding', 'Anti-nuke pro', 'Statistiques avancées', 'Intégrations premium'] },
};

module.exports = {
  module: 'premium',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('premium')
    .setDescription('Système premium')
    .setDMPermission(false)
    .addSubcommand((s) => s.setName('status').setDescription('Voir votre statut premium'))
    .addSubcommand((s) => s.setName('features').setDescription('Voir les fonctionnalités premium'))
    .addSubcommand((s) => s.setName('activate').setDescription('Activer un code premium')
      .addStringOption((o) => o.setName('code').setDescription('Code promo').setRequired(true)),
    )
    .addSubcommand((s) => s.setName('admin').setDescription('Gérer le premium (admin)')
      .addStringOption((o) => o.setName('action').setDescription('Action').setRequired(true).addChoices(
        { name: 'Définir le tier', value: 'set' },
        { name: 'Créer un code promo', value: 'promo' },
      ))
      .addStringOption((o) => o.setName('tier').setDescription('Tier').addChoices(
        { name: 'Gratuit', value: 'free' },
        { name: 'Bronze', value: 'bronze' },
        { name: 'Argent', value: 'silver' },
        { name: 'Or', value: 'gold' },
      ))
      .addStringOption((o) => o.setName('code').setDescription('Code promo'))
      .addIntegerOption((o) => o.setName('duree').setDescription('Durée en jours')),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    switch (sub) {
      case 'status': {
        const premium = await db('premium_guilds').where({ guild_id: guildId }).first();
        const tier = premium?.tier || 'free';
        const info = TIERS[tier];

        const embed = new EmbedBuilder()
          .setTitle(`${info.name} — Statut Premium`)
          .setColor(info.color)
          .addFields(
            { name: 'Tier actuel', value: info.name, inline: true },
            { name: 'Expire le', value: premium?.expires_at ? `<t:${Math.floor(new Date(premium.expires_at).getTime() / 1000)}:F>` : 'N/A', inline: true },
          );

        if (tier !== 'gold') {
          const nextTier = tier === 'free' ? 'bronze' : tier === 'bronze' ? 'silver' : 'gold';
          embed.addFields({ name: '⬆️ Prochain tier', value: `${TIERS[nextTier].name}\nAvantages : ${TIERS[nextTier].features.slice(0, 3).join(', ')}...` });
        }

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'features': {
        const embed = new EmbedBuilder()
          .setTitle('💎 Fonctionnalités Premium')
          .setColor(0xFFD700);

        for (const [key, tier] of Object.entries(TIERS)) {
          embed.addFields({ name: tier.name, value: tier.features.map((f) => `• ${f}`).join('\n'), inline: true });
        }

        return interaction.reply({ embeds: [embed] });
      }

      case 'activate': {
        const code = interaction.options.getString('code');
        const promo = await db('promo_codes').where({ code, used: false }).first();

        if (!promo) return interaction.reply({ content: '❌ Code invalide ou déjà utilisé.', ephemeral: true });

        const expiresAt = new Date(Date.now() + (promo.duration_days || 30) * 86400000);

        await db('premium_guilds').insert({
          guild_id: guildId,
          tier: promo.tier || 'bronze',
          activated_by: interaction.user.id,
          activated_at: new Date(),
          expires_at: expiresAt,
        }).onConflict('guild_id').merge();

        await db('promo_codes').where({ id: promo.id }).update({ used: true, used_by: guildId });

        return interaction.reply({
          embeds: [new EmbedBuilder()
            .setTitle('✅ Premium activé !')
            .setColor(TIERS[promo.tier || 'bronze'].color)
            .setDescription(`Tier : ${TIERS[promo.tier || 'bronze'].name}\nExpire : <t:${Math.floor(expiresAt.getTime() / 1000)}:F>`)],
          ephemeral: true,
        });
      }

      case 'admin': {
        if (interaction.user.id !== process.env.BOT_OWNER_ID) {
          return interaction.reply({ content: '❌ Commande réservée à l\'owner du bot.', ephemeral: true });
        }

        const action = interaction.options.getString('action');

        if (action === 'set') {
          const tier = interaction.options.getString('tier') || 'free';
          const days = interaction.options.getInteger('duree') || 30;
          const expiresAt = new Date(Date.now() + days * 86400000);

          await db('premium_guilds').insert({
            guild_id: guildId,
            tier,
            activated_by: interaction.user.id,
            activated_at: new Date(),
            expires_at: expiresAt,
          }).onConflict('guild_id').merge();

          return interaction.reply({ content: `✅ Tier défini à **${TIERS[tier].name}** pour ${days} jours.`, ephemeral: true });
        }

        if (action === 'promo') {
          const code = interaction.options.getString('code') || `PROMO-${Date.now().toString(36).toUpperCase()}`;
          const tier = interaction.options.getString('tier') || 'bronze';
          const days = interaction.options.getInteger('duree') || 30;

          await db('promo_codes').insert({ code, tier, duration_days: days, used: false });
          return interaction.reply({ content: `✅ Code promo créé : \`${code}\` (${TIERS[tier].name}, ${days} jours)`, ephemeral: true });
        }
        break;
      }
    }
  },
};
