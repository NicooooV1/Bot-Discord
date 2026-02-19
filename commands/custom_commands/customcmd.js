// ===================================
// Ultra Suite — Custom Commands: /customcmd
// Commandes personnalisées par serveur
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');
const { successEmbed, errorEmbed, createEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'custom_commands',
  data: new SlashCommandBuilder()
    .setName('customcmd')
    .setDescription('Gère les commandes personnalisées')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addSubcommand((sub) =>
      sub
        .setName('create')
        .setDescription('Crée une commande personnalisée')
        .addStringOption((opt) => opt.setName('trigger').setDescription('Déclencheur (ex: !hello)').setRequired(true))
        .addStringOption((opt) => opt.setName('response').setDescription('Réponse').setRequired(true))
        .addBooleanOption((opt) => opt.setName('embed').setDescription('Envoyer en embed ?'))
    )
    .addSubcommand((sub) =>
      sub
        .setName('delete')
        .setDescription('Supprime une commande personnalisée')
        .addStringOption((opt) => opt.setName('trigger').setDescription('Déclencheur').setRequired(true))
    )
    .addSubcommand((sub) => sub.setName('list').setDescription('Liste les commandes personnalisées')),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();

    switch (sub) {
      case 'create': {
        const trigger = interaction.options.getString('trigger').toLowerCase();
        const response = interaction.options.getString('response');
        const isEmbed = interaction.options.getBoolean('embed') || false;

        const exists = await db('custom_commands')
          .where({ guild_id: interaction.guild.id, name: trigger })
          .first();

        if (exists) {
          // Update
          await db('custom_commands').where('id', exists.id).update({
            response: JSON.stringify({ text: response, ephemeral: false, embed: isEmbed }),
          });
          return interaction.reply({ embeds: [successEmbed(`✅ Commande \`${trigger}\` mise à jour.`)], ephemeral: true });
        }

        await db('custom_commands').insert({
          guild_id: interaction.guild.id,
          name: trigger,
          response: JSON.stringify({ text: response, ephemeral: false, embed: isEmbed }),
          creator_id: interaction.user.id,
        });

        return interaction.reply({ embeds: [successEmbed(`✅ Commande \`${trigger}\` créée.`)], ephemeral: true });
      }

      case 'delete': {
        const trigger = interaction.options.getString('trigger').toLowerCase();
        const deleted = await db('custom_commands')
          .where({ guild_id: interaction.guild.id, name: trigger })
          .delete();

        if (!deleted) return interaction.reply({ embeds: [errorEmbed('❌ Commande introuvable.')], ephemeral: true });

        return interaction.reply({ embeds: [successEmbed(`✅ Commande \`${trigger}\` supprimée.`)], ephemeral: true });
      }

      case 'list': {
        const cmds = await db('custom_commands')
          .where('guild_id', interaction.guild.id)
          .select('name', 'uses');

        if (cmds.length === 0) return interaction.reply({ content: '📭 Aucune commande personnalisée.', ephemeral: true });

        const list = cmds.map((c) => `\`${c.name}\` — ${c.uses || 0} utilisations`).join('\n');
        const embed = createEmbed('primary').setTitle('⚙️ Commandes personnalisées').setDescription(list);

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }
    }
  },
};
