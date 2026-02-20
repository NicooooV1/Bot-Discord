// ===================================
// Ultra Suite — /note
// Notes de modération sur les membres
// /note add | list | delete
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('note')
    .setDescription('Gérer les notes de modération sur un membre')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addSubcommand((sub) =>
      sub.setName('add').setDescription('Ajouter une note')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre concerné').setRequired(true))
        .addStringOption((opt) => opt.setName('contenu').setDescription('Contenu de la note').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('list').setDescription('Voir les notes d\'un membre')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('delete').setDescription('Supprimer une note')
        .addIntegerOption((opt) => opt.setName('id').setDescription('ID de la note').setRequired(true))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();

    if (sub === 'add') {
      const target = interaction.options.getUser('membre');
      const content = interaction.options.getString('contenu');

      await db('mod_notes').insert({
        guild_id: guildId,
        target_id: target.id,
        author_id: interaction.user.id,
        content,
      });

      return interaction.reply({ content: `✅ Note ajoutée sur **${target.tag}**.`, ephemeral: true });
    }

    if (sub === 'list') {
      const target = interaction.options.getUser('membre');
      const notes = await db('mod_notes')
        .where('guild_id', guildId)
        .where('target_id', target.id)
        .orderBy('created_at', 'desc')
        .limit(15);

      if (notes.length === 0) {
        return interaction.reply({ content: `ℹ️ Aucune note sur **${target.tag}**.`, ephemeral: true });
      }

      const lines = notes.map((n) => {
        const ts = n.created_at ? `<t:${Math.floor(new Date(n.created_at).getTime() / 1000)}:f>` : '?';
        return `**#${n.id}** — par <@${n.author_id}> — ${ts}\n${n.content.slice(0, 150)}`;
      });

      const embed = new EmbedBuilder()
        .setTitle(`📝 Notes — ${target.tag}`)
        .setDescription(lines.join('\n\n'))
        .setColor(0x5865F2)
        .setThumbnail(target.displayAvatarURL())
        .setFooter({ text: `${notes.length} note(s)` });

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    if (sub === 'delete') {
      const id = interaction.options.getInteger('id');
      const deleted = await db('mod_notes').where('id', id).where('guild_id', guildId).del();
      if (!deleted) return interaction.reply({ content: '❌ Note introuvable.', ephemeral: true });
      return interaction.reply({ content: `✅ Note **#${id}** supprimée.`, ephemeral: true });
    }
  },
};