// ===================================
// Ultra Suite — /shop
// Boutique du serveur
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, StringSelectMenuBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'economy',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('shop')
    .setDescription('Boutique du serveur')
    .addSubcommand((s) =>
      s.setName('view').setDescription('Voir la boutique')
        .addIntegerOption((o) => o.setName('page').setDescription('Page').setMinValue(1)),
    )
    .addSubcommand((s) =>
      s.setName('buy').setDescription('Acheter un article')
        .addIntegerOption((o) => o.setName('id').setDescription('ID de l\'article').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('inventory').setDescription('Voir votre inventaire')
        .addUserOption((o) => o.setName('membre').setDescription('Membre à consulter')),
    )
    .addSubcommand((s) =>
      s.setName('use').setDescription('Utiliser un article de votre inventaire')
        .addIntegerOption((o) => o.setName('id').setDescription('ID de l\'article').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('sell').setDescription('Revendre un article')
        .addIntegerOption((o) => o.setName('id').setDescription('ID de l\'article').setRequired(true)),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const userId = interaction.user.id;
    const db = getDb();
    const config = await configService.get(guildId);
    const eco = config.economy || {};
    const symbol = eco.currencySymbol || '🪙';

    switch (sub) {
      case 'view': {
        const page = (interaction.options.getInteger('page') || 1) - 1;
        const perPage = 10;
        const items = await db('shop_items')
          .where({ guild_id: guildId, available: true })
          .orderBy('price', 'asc')
          .offset(page * perPage)
          .limit(perPage);

        const total = await db('shop_items').where({ guild_id: guildId, available: true }).count('id as c').first();
        const totalPages = Math.ceil((total?.c || 0) / perPage);

        if (!items.length) {
          return interaction.reply({ content: '🛒 La boutique est vide.', ephemeral: true });
        }

        const embed = new EmbedBuilder()
          .setTitle('🛒 Boutique du serveur')
          .setColor(0xE67E22)
          .setDescription(items.map((item) => {
            const stock = item.stock === -1 ? '∞' : `${item.stock}`;
            const roleReq = item.required_role ? ` | Rôle: <@&${item.required_role}>` : '';
            return `**#${item.id}** — ${item.emoji || '📦'} **${item.name}** — ${item.price.toLocaleString('fr-FR')} ${symbol}\n> ${item.description || 'Aucune description'}${roleReq} | Stock: ${stock}`;
          }).join('\n\n'))
          .setFooter({ text: `Page ${page + 1}/${totalPages || 1} • /shop buy <id> pour acheter` });

        const row = new ActionRowBuilder().addComponents(
          new ButtonBuilder().setCustomId(`shop_prev_${page}`).setLabel('◀️').setStyle(ButtonStyle.Secondary).setDisabled(page === 0),
          new ButtonBuilder().setCustomId(`shop_next_${page}`).setLabel('▶️').setStyle(ButtonStyle.Secondary).setDisabled(page + 1 >= totalPages),
        );

        return interaction.reply({ embeds: [embed], components: totalPages > 1 ? [row] : [] });
      }

      case 'buy': {
        const itemId = interaction.options.getInteger('id');
        const item = await db('shop_items').where({ guild_id: guildId, id: itemId, available: true }).first();
        if (!item) return interaction.reply({ content: '❌ Article introuvable.', ephemeral: true });

        // Check stock
        if (item.stock !== -1 && item.stock <= 0) {
          return interaction.reply({ content: '❌ Cet article est en rupture de stock.', ephemeral: true });
        }

        // Check role requirement
        if (item.required_role) {
          const member = await interaction.guild.members.fetch(userId).catch(() => null);
          if (!member?.roles.cache.has(item.required_role)) {
            return interaction.reply({ content: `❌ Vous avez besoin du rôle <@&${item.required_role}> pour acheter cet article.`, ephemeral: true });
          }
        }

        // Check balance
        const user = await db('users').where({ guild_id: guildId, user_id: userId }).first();
        if (!user || (user.balance || 0) < item.price) {
          return interaction.reply({ content: `❌ Vous n'avez pas assez de ${symbol}. (Besoin: ${item.price.toLocaleString('fr-FR')})`, ephemeral: true });
        }

        // Check max purchases
        if (item.max_per_user > 0) {
          const owned = await db('inventories')
            .where({ guild_id: guildId, user_id: userId, item_id: itemId })
            .sum('quantity as q').first();
          if ((owned?.q || 0) >= item.max_per_user) {
            return interaction.reply({ content: `❌ Vous avez déjà le maximum (${item.max_per_user}) de cet article.`, ephemeral: true });
          }
        }

        // Process purchase
        await db('users').where({ guild_id: guildId, user_id: userId })
          .update({
            balance: db.raw('balance - ?', [item.price]),
            total_spent: db.raw('COALESCE(total_spent, 0) + ?', [item.price]),
          });

        if (item.stock !== -1) {
          await db('shop_items').where({ id: itemId }).update({ stock: db.raw('stock - 1') });
        }

        // Add to inventory
        const existing = await db('inventories').where({ guild_id: guildId, user_id: userId, item_id: itemId }).first();
        if (existing) {
          await db('inventories').where({ id: existing.id }).update({ quantity: db.raw('quantity + 1') });
        } else {
          await db('inventories').insert({ guild_id: guildId, user_id: userId, item_id: itemId, quantity: 1 });
        }

        // Log transaction
        await db('transactions').insert({
          guild_id: guildId,
          from_id: userId,
          to_id: 'SHOP',
          amount: item.price,
          type: 'shop_buy',
          note: item.name,
        });

        // Auto-apply role if item gives a role
        if (item.type === 'role' && item.role_id) {
          const member = await interaction.guild.members.fetch(userId).catch(() => null);
          if (member) await member.roles.add(item.role_id).catch(() => null);
        }

        return interaction.reply({
          content: `✅ Vous avez acheté ${item.emoji || '📦'} **${item.name}** pour **${item.price.toLocaleString('fr-FR')}** ${symbol} !`,
        });
      }

      case 'inventory': {
        const target = interaction.options.getUser('membre') || interaction.user;
        const items = await db('inventories')
          .join('shop_items', 'inventories.item_id', 'shop_items.id')
          .where({ 'inventories.guild_id': guildId, 'inventories.user_id': target.id })
          .where('inventories.quantity', '>', 0)
          .select('shop_items.*', 'inventories.quantity');

        if (!items.length) {
          return interaction.reply({
            content: target.id === userId ? '🎒 Votre inventaire est vide.' : `🎒 **${target.username}** n'a rien dans son inventaire.`,
            ephemeral: true,
          });
        }

        const embed = new EmbedBuilder()
          .setTitle(`🎒 Inventaire de ${target.username}`)
          .setColor(0x3498DB)
          .setDescription(items.map((i) =>
            `${i.emoji || '📦'} **${i.name}** x${i.quantity} — ${i.type === 'consumable' ? '🔄 Utilisable' : i.type === 'role' ? '👑 Rôle' : '🏷️ Collectible'}`,
          ).join('\n'))
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }

      case 'use': {
        const itemId = interaction.options.getInteger('id');
        const inv = await db('inventories')
          .join('shop_items', 'inventories.item_id', 'shop_items.id')
          .where({ 'inventories.guild_id': guildId, 'inventories.user_id': userId, 'inventories.item_id': itemId })
          .where('inventories.quantity', '>', 0)
          .select('shop_items.*', 'inventories.quantity', 'inventories.id as inv_id')
          .first();

        if (!inv) return interaction.reply({ content: '❌ Article introuvable dans votre inventaire.', ephemeral: true });
        if (inv.type !== 'consumable') return interaction.reply({ content: '❌ Cet article n\'est pas utilisable.', ephemeral: true });

        // Decrease quantity
        await db('inventories').where({ id: inv.inv_id }).update({ quantity: db.raw('quantity - 1') });

        // Apply item action
        let result = `Vous avez utilisé ${inv.emoji || '📦'} **${inv.name}**.`;
        const action = typeof inv.action === 'string' ? JSON.parse(inv.action || '{}') : (inv.action || {});
        if (action.type === 'xp_boost') {
          result += `\n🔥 Boost d'XP de **x${action.value || 2}** pendant **${action.duration || 60}** minutes !`;
        } else if (action.type === 'currency') {
          const amount = action.value || 500;
          await db('users').where({ guild_id: guildId, user_id: userId }).update({ balance: db.raw('balance + ?', [amount]) });
          result += `\n💰 Vous avez reçu **${amount}** ${symbol} !`;
        } else if (action.type === 'temp_role' && action.role_id) {
          const member = await interaction.guild.members.fetch(userId).catch(() => null);
          if (member) {
            await member.roles.add(action.role_id).catch(() => null);
            result += `\n👑 Rôle temporaire <@&${action.role_id}> ajouté !`;
          }
        }

        return interaction.reply({ content: `✅ ${result}` });
      }

      case 'sell': {
        const itemId = interaction.options.getInteger('id');
        const inv = await db('inventories')
          .join('shop_items', 'inventories.item_id', 'shop_items.id')
          .where({ 'inventories.guild_id': guildId, 'inventories.user_id': userId, 'inventories.item_id': itemId })
          .where('inventories.quantity', '>', 0)
          .select('shop_items.*', 'inventories.quantity', 'inventories.id as inv_id')
          .first();

        if (!inv) return interaction.reply({ content: '❌ Article introuvable dans votre inventaire.', ephemeral: true });

        const sellPrice = Math.floor(inv.price * 0.5);
        await db('inventories').where({ id: inv.inv_id }).update({ quantity: db.raw('quantity - 1') });
        await db('users').where({ guild_id: guildId, user_id: userId }).update({
          balance: db.raw('balance + ?', [sellPrice]),
          total_earned: db.raw('COALESCE(total_earned, 0) + ?', [sellPrice]),
        });

        await db('transactions').insert({
          guild_id: guildId,
          from_id: 'SHOP',
          to_id: userId,
          amount: sellPrice,
          type: 'shop_sell',
          note: inv.name,
        });

        return interaction.reply({
          content: `✅ Vous avez vendu ${inv.emoji || '📦'} **${inv.name}** pour **${sellPrice.toLocaleString('fr-FR')}** ${symbol} (50% du prix).`,
        });
      }
    }
  },
};
