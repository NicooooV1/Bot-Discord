// ===================================
// Ultra Suite — /sanctions
// Consulter l'historique des sanctions
// /sanctions user <membre> | case <numéro> | clear <membre>
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('sanctions')
    .setDescription('Consulter l\'historique des sanctions')
    .setDefaultMemberPermissions(PermissionFlagsBits.ModerateMembers)
    .addSubcommand((sub) =>
      sub.setName('user').setDescription('Voir les sanctions d\'un membre')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('case').setDescription('Voir les détails d\'un case')
        .addIntegerOption((opt) => opt.setName('numéro').setDescription('Numéro du case').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('clear').setDescription('Effacer les warns actifs d\'un membre')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre').setRequired(true))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();

    // === USER ===
    if (sub === 'user') {
      const target = interaction.options.getUser('membre');

      const sanctions = await db('sanctions')
        .where('guild_id', guildId)
        .where('target_id', target.id)
        .orderBy('case_number', 'desc')
        .limit(20);

      if (sanctions.length === 0) {
        return interaction.reply({ content: `✅ **${target.tag}** n'a aucune sanction.`, ephemeral: true });
      }

      const lines = sanctions.map((s) => {
        const status = s.active ? '🔴' : '⚪';
        const date = s.created_at ? new Date(s.created_at).toLocaleDateString('fr-FR') : '?';
        return `${status} **#${s.case_number}** — ${s.type} — ${date}\n   ${s.reason?.slice(0, 80) || 'Pas de raison'}`;
      });

      const activeCount = sanctions.filter((s) => s.active).length;

      const embed = new EmbedBuilder()
        .setTitle(`📋 Sanctions — ${target.tag}`)
        .setDescription(lines.join('\n\n'))
        .setColor(0x5865F2)
        .setFooter({ text: `${sanctions.length} sanction(s) — ${activeCount} active(s)` })
        .setThumbnail(target.displayAvatarURL())
        .setTimestamp();

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    // === CASE ===
    if (sub === 'case') {
      const caseNum = interaction.options.getInteger('numéro');

      const sanction = await db('sanctions')
        .where('guild_id', guildId)
        .where('case_number', caseNum)
        .first();

      if (!sanction) {
        return interaction.reply({ content: `❌ Case #${caseNum} introuvable.`, ephemeral: true });
      }

      const embed = new EmbedBuilder()
        .setTitle(`Case #${sanction.case_number}`)
        .setColor(sanction.active ? 0xED4245 : 0x99AAB5)
        .addFields(
          { name: 'Type', value: sanction.type, inline: true },
          { name: 'Statut', value: sanction.active ? '🔴 Actif' : '⚪ Inactif', inline: true },
          { name: 'Cible', value: `<@${sanction.target_id}> (${sanction.target_id})`, inline: true },
          { name: 'Modérateur', value: `<@${sanction.moderator_id}>`, inline: true },
          { name: 'Raison', value: sanction.reason || '*Aucune*', inline: false },
        )
        .setTimestamp(sanction.created_at ? new Date(sanction.created_at) : null);

      if (sanction.duration) {
        const dur = sanction.duration < 3600
          ? `${Math.floor(sanction.duration / 60)}min`
          : sanction.duration < 86400
            ? `${Math.floor(sanction.duration / 3600)}h`
            : `${Math.floor(sanction.duration / 86400)}j`;
        embed.addFields({ name: 'Durée', value: dur, inline: true });
      }

      if (sanction.expires_at) {
        embed.addFields({ name: 'Expire', value: `<t:${Math.floor(new Date(sanction.expires_at).getTime() / 1000)}:R>`, inline: true });
      }

      if (sanction.revoked_at) {
        embed.addFields({ name: 'Révoqué le', value: `<t:${Math.floor(new Date(sanction.revoked_at).getTime() / 1000)}:f>`, inline: true });
      }

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    // === CLEAR ===
    if (sub === 'clear') {
      const target = interaction.options.getUser('membre');

      const updated = await db('sanctions')
        .where('guild_id', guildId)
        .where('target_id', target.id)
        .where('type', 'WARN')
        .where('active', true)
        .update({ active: false, revoked_at: new Date(), revoked_by: interaction.user.id });

      return interaction.reply({
        content: updated > 0
          ? `✅ **${updated}** avertissement(s) de **${target.tag}** effacé(s).`
          : `ℹ️ **${target.tag}** n'a aucun avertissement actif.`,
        ephemeral: true,
      });
    }
  },
};