// ===================================
// Ultra Suite — /pay
// Transférer de l'argent à un autre membre
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { t } = require('../../core/i18n');
const { getDb } = require('../../database');

module.exports = {
  module: 'economy',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('pay')
    .setDescription('Envoyer de l\'argent à un membre')
    .addUserOption((opt) => opt.setName('membre').setDescription('Membre destinataire').setRequired(true))
    .addIntegerOption((opt) => opt.setName('montant').setDescription('Montant à envoyer').setRequired(true).setMinValue(1)),

  async execute(interaction) {
    const target = interaction.options.getUser('membre');
    const amount = interaction.options.getInteger('montant');
    const guildId = interaction.guildId;
    const db = getDb();
    const config = await configService.get(guildId);
    const eco = config.economy || {};
    const symbol = eco.currencySymbol || '🪙';

    // Vérifications
    if (target.id === interaction.user.id) {
      return interaction.reply({ content: '❌ Vous ne pouvez pas vous envoyer de l\'argent.', ephemeral: true });
    }
    if (target.bot) {
      return interaction.reply({ content: '❌ Vous ne pouvez pas payer un bot.', ephemeral: true });
    }

    const sender = await db('users').where('guild_id', guildId).where('user_id', interaction.user.id).first();
    if (!sender || (sender.balance || 0) < amount) {
      const msg = await t(guildId, 'economy.insufficient', {
        balance: (sender?.balance || 0).toLocaleString('fr-FR'),
        currency: symbol,
      });
      return interaction.reply({ content: `❌ ${msg}`, ephemeral: true });
    }

    await interaction.deferReply();

    // Transaction atomique
    await db.transaction(async (trx) => {
      // Débiter
      await trx('users')
        .where('guild_id', guildId).where('user_id', interaction.user.id)
        .decrement('balance', amount);

      // Créditer (upsert)
      const receiver = await trx('users').where('guild_id', guildId).where('user_id', target.id).first();
      if (receiver) {
        await trx('users').where('guild_id', guildId).where('user_id', target.id).increment('balance', amount);
      } else {
        await trx('users').insert({ guild_id: guildId, user_id: target.id, balance: amount });
      }

      // Log transaction
      await trx('transactions').insert({
        guild_id: guildId,
        from_id: interaction.user.id,
        to_id: target.id,
        amount,
        type: 'TRANSFER',
        description: `Transfert de ${interaction.user.tag} à ${target.tag}`,
      });
    });

    const newSenderBalance = (sender.balance || 0) - amount;
    const msg = await t(guildId, 'economy.transfer_success', {
      amount: amount.toLocaleString('fr-FR'),
      currency: symbol,
      target: target.username,
    });

    const embed = new EmbedBuilder()
      .setDescription(`💸 ${msg}`)
      .setColor(0x57F287)
      .addFields(
        { name: 'Votre solde', value: `${newSenderBalance.toLocaleString('fr-FR')} ${symbol}`, inline: true },
      )
      .setTimestamp();

    return interaction.editReply({ embeds: [embed] });
  },
};