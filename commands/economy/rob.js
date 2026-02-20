// ===================================
// Ultra Suite — /rob
// Voler de l'argent à un autre membre (risqué)
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

// Cooldown global en mémoire (en plus du cooldown commande)
const robCooldowns = new Map();

module.exports = {
  module: 'economy',
  cooldown: 30,

  data: new SlashCommandBuilder()
    .setName('rob')
    .setDescription('Tenter de voler de l\'argent à un membre (risqué !)')
    .addUserOption((opt) => opt.setName('membre').setDescription('Victime').setRequired(true)),

  async execute(interaction) {
    const target = interaction.options.getUser('membre');
    const guildId = interaction.guildId;
    const userId = interaction.user.id;
    const db = getDb();
    const config = await configService.get(guildId);
    const symbol = config.economy?.currencySymbol || '🪙';

    // Vérifications
    if (target.id === userId) return interaction.reply({ content: '❌ Vous ne pouvez pas vous voler vous-même.', ephemeral: true });
    if (target.bot) return interaction.reply({ content: '❌ Vous ne pouvez pas voler un bot.', ephemeral: true });

    // Cooldown 5 minutes par paire
    const cooldownKey = `${guildId}:${userId}`;
    const lastRob = robCooldowns.get(cooldownKey);
    if (lastRob && Date.now() - lastRob < 300000) {
      const remaining = Math.ceil((300000 - (Date.now() - lastRob)) / 60000);
      return interaction.reply({ content: `⏰ Vous devez attendre **${remaining} minute(s)** avant de voler à nouveau.`, ephemeral: true });
    }

    const robber = await db('users').where('guild_id', guildId).where('user_id', userId).first();
    const victim = await db('users').where('guild_id', guildId).where('user_id', target.id).first();

    if (!robber || (robber.balance || 0) < 50) {
      return interaction.reply({ content: `❌ Vous devez avoir au moins **50** ${symbol} (caution en cas d'échec).`, ephemeral: true });
    }
    if (!victim || (victim.balance || 0) < 50) {
      return interaction.reply({ content: `❌ **${target.username}** n'a pas assez d'argent à voler.`, ephemeral: true });
    }

    await interaction.deferReply();
    robCooldowns.set(cooldownKey, Date.now());

    // 40% de chance de réussite
    const success = Math.random() < 0.4;

    if (success) {
      // Voler entre 10-30% du solde de la victime
      const percent = 0.1 + Math.random() * 0.2;
      const stolen = Math.floor((victim.balance || 0) * percent);

      await db.transaction(async (trx) => {
        await trx('users').where('guild_id', guildId).where('user_id', userId).increment('balance', stolen);
        await trx('users').where('guild_id', guildId).where('user_id', target.id).decrement('balance', stolen);
        await trx('transactions').insert({
          guild_id: guildId, from_id: target.id, to_id: userId,
          amount: stolen, type: 'ROB_SUCCESS', description: `Vol réussi sur ${target.tag}`,
        });
      });

      const embed = new EmbedBuilder()
        .setDescription(`💰 Vous avez volé **${stolen.toLocaleString('fr-FR')}** ${symbol} à **${target.username}** !`)
        .setColor(0x57F287).setTimestamp();

      return interaction.editReply({ embeds: [embed] });
    } else {
      // Échec : perd 10-20% de son propre solde (amende)
      const percent = 0.1 + Math.random() * 0.1;
      const fine = Math.floor((robber.balance || 0) * percent);

      await db.transaction(async (trx) => {
        await trx('users').where('guild_id', guildId).where('user_id', userId).decrement('balance', fine);
        await trx('users').where('guild_id', guildId).where('user_id', target.id).increment('balance', Math.floor(fine / 2));
        await trx('transactions').insert({
          guild_id: guildId, from_id: userId, to_id: target.id,
          amount: fine, type: 'ROB_FAIL', description: `Tentative de vol échouée sur ${target.tag}`,
        });
      });

      const embed = new EmbedBuilder()
        .setDescription(`👮 Vous avez été attrapé ! Amende de **${fine.toLocaleString('fr-FR')}** ${symbol}.\n**${target.username}** récupère la moitié.`)
        .setColor(0xED4245).setTimestamp();

      return interaction.editReply({ embeds: [embed] });
    }
  },
};