// ===================================
// Ultra Suite — Tags: /tag
// FAQ et réponses pré-enregistrées
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');
const { successEmbed, errorEmbed, createEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'tags',
  data: new SlashCommandBuilder()
    .setName('tag')
    .setDescription('Gère les tags / FAQ')
    .addSubcommand((sub) =>
      sub
        .setName('show')
        .setDescription('Affiche un tag')
        .addStringOption((opt) => opt.setName('name').setDescription('Nom du tag').setRequired(true).setAutocomplete(true))
    )
    .addSubcommand((sub) =>
      sub
        .setName('create')
        .setDescription('Crée un tag')
        .addStringOption((opt) => opt.setName('name').setDescription('Nom').setRequired(true))
        .addStringOption((opt) => opt.setName('content').setDescription('Contenu').setRequired(true))
    )
    .addSubcommand((sub) =>
      sub
        .setName('delete')
        .setDescription('Supprime un tag')
        .addStringOption((opt) => opt.setName('name').setDescription('Nom du tag').setRequired(true).setAutocomplete(true))
    )
    .addSubcommand((sub) => sub.setName('list').setDescription('Liste tous les tags')),

  async autocomplete(interaction) {
    const db = getDb();
    const focused = interaction.options.getFocused().toLowerCase();
    const tags = await db('tags')
      .where('guild_id', interaction.guild.id)
      .andWhere('name', 'like', `%${focused}%`)
      .limit(25)
      .select('name');

    return interaction.respond(tags.map((t) => ({ name: t.name, value: t.name })));
  },

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();

    switch (sub) {
      case 'show': {
        const name = interaction.options.getString('name').toLowerCase();
        const tag = await db('tags').where({ guild_id: interaction.guild.id, name }).first();

        if (!tag) return interaction.reply({ embeds: [errorEmbed('❌ Tag introuvable.')], ephemeral: true });

        await db('tags').where('id', tag.id).increment('uses', 1);

        const tagContent = JSON.parse(tag.content);
        return interaction.reply({ content: tagContent.text || tag.content });
      }

      case 'create': {
        if (!interaction.member.permissions.has(PermissionFlagsBits.ManageMessages)) {
          return interaction.reply({ embeds: [errorEmbed('❌ Permission manquante.')], ephemeral: true });
        }

        const name = interaction.options.getString('name').toLowerCase();
        const content = interaction.options.getString('content');

        const exists = await db('tags').where({ guild_id: interaction.guild.id, name }).first();
        if (exists) return interaction.reply({ embeds: [errorEmbed('❌ Ce tag existe déjà.')], ephemeral: true });

        await db('tags').insert({
          guild_id: interaction.guild.id,
          name,
          content: JSON.stringify({ text: content }),
          creator_id: interaction.user.id,
        });

        return interaction.reply({ embeds: [successEmbed(`✅ Tag \`${name}\` créé.`)], ephemeral: true });
      }

      case 'delete': {
        if (!interaction.member.permissions.has(PermissionFlagsBits.ManageMessages)) {
          return interaction.reply({ embeds: [errorEmbed('❌ Permission manquante.')], ephemeral: true });
        }

        const name = interaction.options.getString('name').toLowerCase();
        const deleted = await db('tags').where({ guild_id: interaction.guild.id, name }).delete();

        if (deleted === 0) return interaction.reply({ embeds: [errorEmbed('❌ Tag introuvable.')], ephemeral: true });

        return interaction.reply({ embeds: [successEmbed(`✅ Tag \`${name}\` supprimé.`)], ephemeral: true });
      }

      case 'list': {
        const tags = await db('tags')
          .where('guild_id', interaction.guild.id)
          .orderBy('uses', 'desc')
          .select('name', 'uses');

        if (tags.length === 0) return interaction.reply({ content: '📭 Aucun tag.', ephemeral: true });

        const list = tags.map((t) => `\`${t.name}\` (${t.uses} utilisations)`).join('\n');

        const embed = createEmbed('primary').setTitle('🏷️ Tags').setDescription(list);

        return interaction.reply({ embeds: [embed] });
      }
    }
  },
};
