// ===================================
// Ultra Suite — /ecoadmin
// Administration de l'économie
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'economy',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('ecoadmin')
    .setDescription('Gérer l\'économie du serveur')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addSubcommand((s) =>
      s.setName('give').setDescription('Donner de l\'argent à un membre')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addIntegerOption((o) => o.setName('montant').setDescription('Montant').setRequired(true).setMinValue(1)),
    )
    .addSubcommand((s) =>
      s.setName('take').setDescription('Retirer de l\'argent à un membre')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addIntegerOption((o) => o.setName('montant').setDescription('Montant').setRequired(true).setMinValue(1)),
    )
    .addSubcommand((s) =>
      s.setName('set').setDescription('Définir le solde d\'un membre')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addIntegerOption((o) => o.setName('montant').setDescription('Montant').setRequired(true).setMinValue(0)),
    )
    .addSubcommand((s) =>
      s.setName('reset').setDescription('Réinitialiser l\'économie')
        .addUserOption((o) => o.setName('membre').setDescription('Membre (vide = tout le serveur)')),
    )
    .addSubcommand((s) =>
      s.setName('additem').setDescription('Ajouter un article à la boutique')
        .addStringOption((o) => o.setName('nom').setDescription('Nom de l\'article').setRequired(true))
        .addIntegerOption((o) => o.setName('prix').setDescription('Prix').setRequired(true).setMinValue(1))
        .addStringOption((o) => o.setName('description').setDescription('Description'))
        .addStringOption((o) => o.setName('emoji').setDescription('Emoji'))
        .addStringOption((o) => o.setName('type').setDescription('Type d\'article').addChoices(
          { name: 'Consommable', value: 'consumable' },
          { name: 'Rôle', value: 'role' },
          { name: 'Collectible', value: 'collectible' },
          { name: 'Badge', value: 'badge' },
        ))
        .addRoleOption((o) => o.setName('role').setDescription('Rôle donné (type rôle)'))
        .addIntegerOption((o) => o.setName('stock').setDescription('Stock (-1 = illimité)').setMinValue(-1))
        .addIntegerOption((o) => o.setName('max_par_user').setDescription('Maximum par utilisateur').setMinValue(0))
        .addRoleOption((o) => o.setName('role_requis').setDescription('Rôle requis pour acheter')),
    )
    .addSubcommand((s) =>
      s.setName('removeitem').setDescription('Supprimer un article de la boutique')
        .addIntegerOption((o) => o.setName('id').setDescription('ID de l\'article').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('edititem').setDescription('Modifier un article')
        .addIntegerOption((o) => o.setName('id').setDescription('ID').setRequired(true))
        .addStringOption((o) => o.setName('nom').setDescription('Nouveau nom'))
        .addIntegerOption((o) => o.setName('prix').setDescription('Nouveau prix').setMinValue(1))
        .addStringOption((o) => o.setName('description').setDescription('Nouvelle description'))
        .addIntegerOption((o) => o.setName('stock').setDescription('Nouveau stock').setMinValue(-1)),
    )
    .addSubcommand((s) =>
      s.setName('inflation').setDescription('Appliquer une inflation/déflation')
        .addNumberOption((o) => o.setName('pourcentage').setDescription('Pourcentage (ex: 10 pour +10%, -10 pour -10%)').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('stats').setDescription('Statistiques de l\'économie'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();
    const config = await configService.get(guildId);
    const eco = config.economy || {};
    const symbol = eco.currencySymbol || '🪙';

    switch (sub) {
      case 'give': {
        const target = interaction.options.getUser('membre');
        const amount = interaction.options.getInteger('montant');
        await db('users')
          .insert({ guild_id: guildId, user_id: target.id, balance: amount })
          .onConflict(['guild_id', 'user_id'])
          .merge({ balance: db.raw('balance + ?', [amount]) });
        await db('transactions').insert({ guild_id: guildId, from_id: 'ADMIN', to_id: target.id, amount, type: 'admin_give' });
        return interaction.reply({ content: `✅ **+${amount.toLocaleString('fr-FR')}** ${symbol} donné à **${target.username}**.`, ephemeral: true });
      }

      case 'take': {
        const target = interaction.options.getUser('membre');
        const amount = interaction.options.getInteger('montant');
        await db('users').where({ guild_id: guildId, user_id: target.id })
          .update({ balance: db.raw('GREATEST(0, balance - ?)', [amount]) });
        await db('transactions').insert({ guild_id: guildId, from_id: target.id, to_id: 'ADMIN', amount, type: 'admin_take' });
        return interaction.reply({ content: `✅ **-${amount.toLocaleString('fr-FR')}** ${symbol} retiré à **${target.username}**.`, ephemeral: true });
      }

      case 'set': {
        const target = interaction.options.getUser('membre');
        const amount = interaction.options.getInteger('montant');
        await db('users')
          .insert({ guild_id: guildId, user_id: target.id, balance: amount })
          .onConflict(['guild_id', 'user_id'])
          .merge({ balance: amount });
        return interaction.reply({ content: `✅ Solde de **${target.username}** défini à **${amount.toLocaleString('fr-FR')}** ${symbol}.`, ephemeral: true });
      }

      case 'reset': {
        const target = interaction.options.getUser('membre');
        if (target) {
          await db('users').where({ guild_id: guildId, user_id: target.id }).update({ balance: 0 });
          await db('inventories').where({ guild_id: guildId, user_id: target.id }).delete();
          return interaction.reply({ content: `✅ Économie de **${target.username}** réinitialisée.`, ephemeral: true });
        }
        await db('users').where({ guild_id: guildId }).update({ balance: 0 });
        await db('inventories').where({ guild_id: guildId }).delete();
        await db('transactions').where({ guild_id: guildId }).delete();
        return interaction.reply({ content: '✅ Économie du serveur réinitialisée.', ephemeral: true });
      }

      case 'additem': {
        const name = interaction.options.getString('nom');
        const price = interaction.options.getInteger('prix');
        const description = interaction.options.getString('description') || '';
        const emoji = interaction.options.getString('emoji') || '📦';
        const type = interaction.options.getString('type') || 'consumable';
        const role = interaction.options.getRole('role');
        const stock = interaction.options.getInteger('stock') ?? -1;
        const maxPerUser = interaction.options.getInteger('max_par_user') ?? 0;
        const requiredRole = interaction.options.getRole('role_requis');

        const [id] = await db('shop_items').insert({
          guild_id: guildId,
          name,
          description,
          emoji,
          price,
          type,
          role_id: role?.id || null,
          required_role: requiredRole?.id || null,
          stock,
          max_per_user: maxPerUser,
          available: true,
        });

        return interaction.reply({ content: `✅ Article **${emoji} ${name}** ajouté à la boutique (ID: #${id}, Prix: ${price} ${symbol}).`, ephemeral: true });
      }

      case 'removeitem': {
        const itemId = interaction.options.getInteger('id');
        const deleted = await db('shop_items').where({ guild_id: guildId, id: itemId }).delete();
        if (!deleted) return interaction.reply({ content: '❌ Article introuvable.', ephemeral: true });
        return interaction.reply({ content: `✅ Article #${itemId} supprimé de la boutique.`, ephemeral: true });
      }

      case 'edititem': {
        const itemId = interaction.options.getInteger('id');
        const updates = {};
        const name = interaction.options.getString('nom');
        const price = interaction.options.getInteger('prix');
        const desc = interaction.options.getString('description');
        const stock = interaction.options.getInteger('stock');
        if (name) updates.name = name;
        if (price) updates.price = price;
        if (desc) updates.description = desc;
        if (stock !== null && stock !== undefined) updates.stock = stock;
        if (!Object.keys(updates).length) return interaction.reply({ content: '❌ Aucune modification.', ephemeral: true });
        await db('shop_items').where({ guild_id: guildId, id: itemId }).update(updates);
        return interaction.reply({ content: `✅ Article #${itemId} modifié.`, ephemeral: true });
      }

      case 'inflation': {
        const pct = interaction.options.getNumber('pourcentage');
        const factor = 1 + pct / 100;
        await db('users').where({ guild_id: guildId }).update({ balance: db.raw('ROUND(balance * ?)', [factor]) });
        await db('shop_items').where({ guild_id: guildId }).update({ price: db.raw('ROUND(price * ?)', [factor]) });
        const direction = pct > 0 ? 'Inflation' : 'Déflation';
        return interaction.reply({ content: `✅ ${direction} de **${Math.abs(pct)}%** appliquée.`, ephemeral: true });
      }

      case 'stats': {
        const totalUsers = await db('users').where({ guild_id: guildId }).where('balance', '>', 0).count('id as c').first();
        const totalMoney = await db('users').where({ guild_id: guildId }).sum('balance as s').first();
        const totalItems = await db('shop_items').where({ guild_id: guildId, available: true }).count('id as c').first();
        const totalTx = await db('transactions').where({ guild_id: guildId }).count('id as c').first();
        const richest = await db('users').where({ guild_id: guildId }).orderBy('balance', 'desc').first();

        const embed = new EmbedBuilder()
          .setTitle('📊 Statistiques économie')
          .setColor(0xE67E22)
          .addFields(
            { name: '👥 Comptes actifs', value: `${totalUsers?.c || 0}`, inline: true },
            { name: '💰 Monnaie en circulation', value: `${(totalMoney?.s || 0).toLocaleString('fr-FR')} ${symbol}`, inline: true },
            { name: '🛒 Articles en boutique', value: `${totalItems?.c || 0}`, inline: true },
            { name: '📝 Transactions', value: `${totalTx?.c || 0}`, inline: true },
            { name: '🏆 Plus riche', value: richest ? `<@${richest.user_id}> — ${(richest.balance || 0).toLocaleString('fr-FR')} ${symbol}` : 'N/A', inline: true },
          )
          .setTimestamp();

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }
    }
  },
};
