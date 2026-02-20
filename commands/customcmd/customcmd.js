// ===================================
// Ultra Suite — /customcmd
// Commandes personnalisées par serveur
// /customcmd create | delete | list | edit
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'custom_commands',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('customcmd')
    .setDescription('Gérer les commandes personnalisées')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addSubcommand((sub) =>
      sub.setName('create').setDescription('Créer une commande personnalisée')
        .addStringOption((opt) => opt.setName('trigger').setDescription('Mot-clé déclencheur (ex: !hello)').setRequired(true))
        .addStringOption((opt) => opt.setName('réponse').setDescription('Réponse de la commande').setRequired(true))
        .addBooleanOption((opt) => opt.setName('embed').setDescription('Envoyer en embed')))
    .addSubcommand((sub) =>
      sub.setName('edit').setDescription('Modifier une commande')
        .addStringOption((opt) => opt.setName('trigger').setDescription('Mot-clé existant').setRequired(true))
        .addStringOption((opt) => opt.setName('réponse').setDescription('Nouvelle réponse').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('delete').setDescription('Supprimer une commande')
        .addStringOption((opt) => opt.setName('trigger').setDescription('Mot-clé à supprimer').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('list').setDescription('Lister toutes les commandes')),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();

    if (sub === 'create') {
      const trigger = interaction.options.getString('trigger').toLowerCase().trim();
      const response = interaction.options.getString('réponse');
      const useEmbed = interaction.options.getBoolean('embed') || false;

      if (trigger.length > 50) return interaction.reply({ content: '❌ Trigger trop long (max 50).', ephemeral: true });

      const existing = await db('custom_commands').where('guild_id', guildId).where('trigger', trigger).first();
      if (existing) return interaction.reply({ content: `❌ La commande \`${trigger}\` existe déjà.`, ephemeral: true });

      await db('custom_commands').insert({
        guild_id: guildId,
        trigger,
        response,
        use_embed: useEmbed,
        created_by: interaction.user.id,
        uses: 0,
      });

      return interaction.reply({ content: `✅ Commande \`${trigger}\` créée.`, ephemeral: true });
    }

    if (sub === 'edit') {
      const trigger = interaction.options.getString('trigger').toLowerCase().trim();
      const response = interaction.options.getString('réponse');

      const updated = await db('custom_commands')
        .where('guild_id', guildId).where('trigger', trigger)
        .update({ response, updated_at: new Date() });

      if (!updated) return interaction.reply({ content: `❌ Commande \`${trigger}\` introuvable.`, ephemeral: true });
      return interaction.reply({ content: `✅ Commande \`${trigger}\` modifiée.`, ephemeral: true });
    }

    if (sub === 'delete') {
      const trigger = interaction.options.getString('trigger').toLowerCase().trim();
      const deleted = await db('custom_commands').where('guild_id', guildId).where('trigger', trigger).del();
      if (!deleted) return interaction.reply({ content: '❌ Introuvable.', ephemeral: true });
      return interaction.reply({ content: `✅ Commande \`${trigger}\` supprimée.`, ephemeral: true });
    }

    if (sub === 'list') {
      const cmds = await db('custom_commands').where('guild_id', guildId).orderBy('uses', 'desc');
      if (cmds.length === 0) return interaction.reply({ content: 'ℹ️ Aucune commande personnalisée.', ephemeral: true });

      const lines = cmds.map((c) => `\`${c.trigger}\` — ${c.uses || 0} utilisation(s) ${c.use_embed ? '📦' : ''}`);

      const embed = new EmbedBuilder()
        .setTitle(`⚡ Commandes personnalisées (${cmds.length})`)
        .setDescription(lines.join('\n'))
        .setColor(0x5865F2).setTimestamp();

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }
  },
};