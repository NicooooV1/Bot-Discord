// ===================================
// Ultra Suite — /quarantine
// Mettre un membre en quarantaine
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'moderation',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('quarantine')
    .setDescription('Mettre un membre en quarantaine (retire tous les rôles)')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageRoles)
    .addSubcommand((s) =>
      s.setName('add').setDescription('Mettre en quarantaine')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true))
        .addStringOption((o) => o.setName('raison').setDescription('Raison'))
        .addStringOption((o) => o.setName('duree').setDescription('Durée (ex: 1h, 1d, 7d)')),
    )
    .addSubcommand((s) =>
      s.setName('remove').setDescription('Retirer de la quarantaine')
        .addUserOption((o) => o.setName('membre').setDescription('Membre').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('list').setDescription('Voir les membres en quarantaine'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();

    const parseDuration = (str) => {
      if (!str) return null;
      const match = str.match(/^(\d+)(m|h|d|w)$/);
      if (!match) return null;
      const val = parseInt(match[1]);
      const unit = match[2];
      const ms = { m: 60000, h: 3600000, d: 86400000, w: 604800000 };
      return val * (ms[unit] || 0);
    };

    switch (sub) {
      case 'add': {
        const target = interaction.options.getUser('membre');
        const reason = interaction.options.getString('raison') || 'Quarantaine';
        const durationStr = interaction.options.getString('duree');
        const durationMs = parseDuration(durationStr);

        const member = await interaction.guild.members.fetch(target.id).catch(() => null);
        if (!member) return interaction.reply({ content: '❌ Membre introuvable.', ephemeral: true });

        // Save original roles
        const originalRoles = member.roles.cache
          .filter((r) => r.id !== guildId)
          .map((r) => r.id);

        // Remove all roles
        await member.roles.set([], `Quarantaine: ${reason}`).catch(() => null);

        // Add quarantine role if configured
        const config = await configService.get(guildId);
        const quarantineRole = config.moderation?.quarantineRole;
        if (quarantineRole) {
          await member.roles.add(quarantineRole, 'Quarantaine').catch(() => null);
        }

        const expiresAt = durationMs ? new Date(Date.now() + durationMs) : null;

        await db('quarantine')
          .insert({
            guild_id: guildId,
            user_id: target.id,
            moderator_id: interaction.user.id,
            reason,
            original_roles: JSON.stringify(originalRoles),
            expires_at: expiresAt,
            active: true,
          })
          .onConflict(['guild_id', 'user_id'])
          .merge();

        await db('sanctions').insert({
          guild_id: guildId,
          user_id: target.id,
          moderator_id: interaction.user.id,
          type: 'QUARANTINE',
          reason,
          expires_at: expiresAt,
        });

        const embed = new EmbedBuilder()
          .setColor(0xE74C3C)
          .setTitle('🔒 Quarantaine')
          .setDescription(`**${target.username}** a été mis en quarantaine.`)
          .addFields(
            { name: 'Raison', value: reason },
            { name: 'Durée', value: durationStr || 'Indéfinie', inline: true },
            { name: 'Rôles sauvegardés', value: `${originalRoles.length} rôle(s)`, inline: true },
          )
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }

      case 'remove': {
        const target = interaction.options.getUser('membre');
        const quarantine = await db('quarantine')
          .where({ guild_id: guildId, user_id: target.id, active: true })
          .first();

        if (!quarantine) return interaction.reply({ content: '❌ Ce membre n\'est pas en quarantaine.', ephemeral: true });

        const member = await interaction.guild.members.fetch(target.id).catch(() => null);
        if (member) {
          // Restore original roles
          const originalRoles = JSON.parse(quarantine.original_roles || '[]');
          const config = await configService.get(guildId);
          const quarantineRole = config.moderation?.quarantineRole;
          if (quarantineRole) await member.roles.remove(quarantineRole, 'Fin quarantaine').catch(() => null);
          for (const roleId of originalRoles) {
            await member.roles.add(roleId, 'Restauration post-quarantaine').catch(() => null);
          }
        }

        await db('quarantine').where({ guild_id: guildId, user_id: target.id }).update({ active: false });

        return interaction.reply({ content: `✅ **${target.username}** a été retiré de la quarantaine. Rôles restaurés.` });
      }

      case 'list': {
        const quarantined = await db('quarantine').where({ guild_id: guildId, active: true });
        if (!quarantined.length) return interaction.reply({ content: '📋 Aucun membre en quarantaine.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle('🔒 Membres en quarantaine')
          .setColor(0xE74C3C)
          .setDescription(quarantined.map((q) => {
            const expiry = q.expires_at ? `<t:${Math.floor(new Date(q.expires_at).getTime() / 1000)}:R>` : 'Indéfinie';
            return `<@${q.user_id}> — par <@${q.moderator_id}> — Expire: ${expiry}\n> ${q.reason}`;
          }).join('\n\n'))
          .setTimestamp();

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }
    }
  },
};
