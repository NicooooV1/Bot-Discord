// ===================================
// Ultra Suite — /rpinventory
// Inventaire RP des personnages
// /rpinventory view | give | use
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'rp',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('rpinventory')
    .setDescription('Gérer l\'inventaire de votre personnage')
    .addSubcommand((sub) =>
      sub.setName('view').setDescription('Voir votre inventaire')
        .addStringOption((opt) => opt.setName('personnage').setDescription('Nom du personnage')))
    .addSubcommand((sub) =>
      sub.setName('give').setDescription('Donner un objet à un autre personnage (admin RP)')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre cible').setRequired(true))
        .addStringOption((opt) => opt.setName('objet').setDescription('Nom de l\'objet').setRequired(true))
        .addIntegerOption((opt) => opt.setName('quantité').setDescription('Quantité').setMinValue(1))
        .addStringOption((opt) => opt.setName('description').setDescription('Description de l\'objet')))
    .addSubcommand((sub) =>
      sub.setName('use').setDescription('Utiliser un objet')
        .addStringOption((opt) => opt.setName('objet').setDescription('Nom de l\'objet').setRequired(true))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const userId = interaction.user.id;
    const db = getDb();

    // === VIEW ===
    if (sub === 'view') {
      const charName = interaction.options.getString('personnage');
      let character;

      if (charName) {
        character = await db('rp_characters').where('guild_id', guildId).where('user_id', userId).where('name', charName).first();
      } else {
        character = await db('rp_characters').where('guild_id', guildId).where('user_id', userId).first();
      }

      if (!character) return interaction.reply({ content: '❌ Aucun personnage trouvé.', ephemeral: true });

      const items = await db('rp_inventory')
        .where('character_id', character.id)
        .where('quantity', '>', 0)
        .orderBy('name', 'asc');

      if (items.length === 0) {
        return interaction.reply({ content: `🎒 L'inventaire de **${character.name}** est vide.`, ephemeral: true });
      }

      const lines = items.map((item) => {
        const desc = item.description ? ` — *${item.description.slice(0, 50)}*` : '';
        return `• **${item.name}** x${item.quantity}${desc}`;
      });

      const embed = new EmbedBuilder()
        .setTitle(`🎒 Inventaire de ${character.name}`)
        .setDescription(lines.join('\n'))
        .setColor(0x9B59B6)
        .setFooter({ text: `${items.length} objet(s) différent(s)` })
        .setTimestamp();

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    // === GIVE (admin ou MJ) ===
    if (sub === 'give') {
      // Vérifier permission ManageGuild (MJ)
      if (!interaction.member.permissions.has('ManageGuild')) {
        return interaction.reply({ content: '❌ Permission Maître du Jeu requise.', ephemeral: true });
      }

      const target = interaction.options.getUser('membre');
      const itemName = interaction.options.getString('objet');
      const quantity = interaction.options.getInteger('quantité') || 1;
      const description = interaction.options.getString('description') || null;

      // Trouver le premier personnage du membre cible
      const character = await db('rp_characters')
        .where('guild_id', guildId).where('user_id', target.id).first();

      if (!character) return interaction.reply({ content: `❌ **${target.username}** n'a aucun personnage.`, ephemeral: true });

      // Upsert l'objet
      const existing = await db('rp_inventory')
        .where('character_id', character.id).where('name', itemName).first();

      if (existing) {
        await db('rp_inventory').where('id', existing.id)
          .increment('quantity', quantity);
      } else {
        await db('rp_inventory').insert({
          character_id: character.id,
          name: itemName,
          description,
          quantity,
        });
      }

      return interaction.reply({
        content: `✅ **${itemName}** x${quantity} donné à **${character.name}** (${target.username}).`,
      });
    }

    // === USE ===
    if (sub === 'use') {
      const itemName = interaction.options.getString('objet');

      const character = await db('rp_characters')
        .where('guild_id', guildId).where('user_id', userId).first();
      if (!character) return interaction.reply({ content: '❌ Aucun personnage.', ephemeral: true });

      const item = await db('rp_inventory')
        .where('character_id', character.id).where('name', itemName).where('quantity', '>', 0).first();
      if (!item) return interaction.reply({ content: `❌ **${itemName}** introuvable dans votre inventaire.`, ephemeral: true });

      // Décrémenter
      await db('rp_inventory').where('id', item.id).decrement('quantity', 1);

      return interaction.reply({
        content: `🎭 **${character.name}** utilise **${itemName}** ! (${item.quantity - 1} restant${item.quantity - 1 > 1 ? 's' : ''})`,
      });
    }
  },
};