// ===================================
// Ultra Suite — /reactionrole
// Reaction roles avancés
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'roles',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('reactionrole')
    .setDescription('Gérer les reaction roles')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageRoles)
    .addSubcommand((s) =>
      s.setName('create').setDescription('Créer un panneau de reaction roles')
        .addStringOption((o) => o.setName('titre').setDescription('Titre du panneau').setRequired(true))
        .addStringOption((o) => o.setName('roles').setDescription('Rôles (emoji:@role séparés par |)').setRequired(true))
        .addStringOption((o) => o.setName('description').setDescription('Description'))
        .addStringOption((o) => o.setName('mode').setDescription('Mode').addChoices(
          { name: 'Normal (multi)', value: 'normal' },
          { name: 'Unique (1 seul)', value: 'unique' },
          { name: 'Obligatoire', value: 'required' },
        ))
        .addChannelOption((o) => o.setName('salon').setDescription('Salon')),
    )
    .addSubcommand((s) =>
      s.setName('add').setDescription('Ajouter un rôle à un panneau')
        .addStringOption((o) => o.setName('message_id').setDescription('ID du message').setRequired(true))
        .addRoleOption((o) => o.setName('role').setDescription('Rôle').setRequired(true))
        .addStringOption((o) => o.setName('emoji').setDescription('Emoji').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('remove').setDescription('Retirer un rôle d\'un panneau')
        .addStringOption((o) => o.setName('message_id').setDescription('ID du message').setRequired(true))
        .addStringOption((o) => o.setName('emoji').setDescription('Emoji à retirer').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('list').setDescription('Lister les reaction roles'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    switch (sub) {
      case 'create': {
        const title = interaction.options.getString('titre');
        const rolesStr = interaction.options.getString('roles');
        const description = interaction.options.getString('description') || 'Réagissez pour obtenir un rôle !';
        const mode = interaction.options.getString('mode') || 'normal';
        const channel = interaction.options.getChannel('salon') || interaction.channel;

        const pairs = rolesStr.split('|').map((p) => p.trim());
        const roleMap = [];

        for (const pair of pairs) {
          const match = pair.match(/^(.+?)\s*:\s*<@&(\d+)>$/);
          if (!match) continue;
          const [, emoji, roleId] = match;
          const role = interaction.guild.roles.cache.get(roleId);
          if (role) roleMap.push({ emoji: emoji.trim(), roleId, roleName: role.name });
        }

        if (!roleMap.length) {
          return interaction.reply({ content: `❌ Format invalide. Utilisez : \`emoji:@role | emoji:@role\`\nExemple : \`🎮:<@&${guildId}> | 🎵:<@&${guildId}>\``, ephemeral: true });
        }

        const embed = new EmbedBuilder()
          .setTitle(title)
          .setColor(0x3498DB)
          .setDescription(description + '\n\n' + roleMap.map((r) => `${r.emoji} — <@&${r.roleId}>`).join('\n'))
          .setFooter({ text: `Mode: ${mode === 'unique' ? 'Un seul rôle' : mode === 'required' ? 'Obligatoire' : 'Multi-rôles'}` });

        const msg = await channel.send({ embeds: [embed] });
        for (const r of roleMap) {
          await msg.react(r.emoji).catch(() => {});
        }

        await db('reaction_roles').insert({
          guild_id: guildId,
          message_id: msg.id,
          channel_id: channel.id,
          roles: JSON.stringify(roleMap),
          mode,
        }).onConflict('message_id').merge();

        return interaction.reply({ content: `✅ Reaction roles créés dans ${channel} !`, ephemeral: true });
      }

      case 'add': {
        const messageId = interaction.options.getString('message_id');
        const role = interaction.options.getRole('role');
        const emoji = interaction.options.getString('emoji');

        const rr = await db('reaction_roles').where({ guild_id: guildId, message_id: messageId }).first();
        if (!rr) return interaction.reply({ content: '❌ Panneau introuvable.', ephemeral: true });

        const roles = JSON.parse(rr.roles);
        roles.push({ emoji, roleId: role.id, roleName: role.name });
        await db('reaction_roles').where({ id: rr.id }).update({ roles: JSON.stringify(roles) });

        const channel = await interaction.guild.channels.fetch(rr.channel_id).catch(() => null);
        if (channel) {
          const msg = await channel.messages.fetch(messageId).catch(() => null);
          if (msg) await msg.react(emoji).catch(() => {});
        }

        return interaction.reply({ content: `✅ Rôle ${role} ajouté avec l'emoji ${emoji}.`, ephemeral: true });
      }

      case 'remove': {
        const messageId = interaction.options.getString('message_id');
        const emoji = interaction.options.getString('emoji');

        const rr = await db('reaction_roles').where({ guild_id: guildId, message_id: messageId }).first();
        if (!rr) return interaction.reply({ content: '❌ Panneau introuvable.', ephemeral: true });

        const roles = JSON.parse(rr.roles).filter((r) => r.emoji !== emoji);
        await db('reaction_roles').where({ id: rr.id }).update({ roles: JSON.stringify(roles) });

        return interaction.reply({ content: `✅ Emoji ${emoji} retiré.`, ephemeral: true });
      }

      case 'list': {
        const rrs = await db('reaction_roles').where({ guild_id: guildId });
        if (!rrs.length) return interaction.reply({ content: 'Aucun reaction role.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle('🏷️ Reaction Roles')
          .setColor(0x3498DB)
          .setDescription(rrs.map((rr) => {
            const roles = JSON.parse(rr.roles);
            return `**Message:** ${rr.message_id} (<#${rr.channel_id}>)\nMode: ${rr.mode} • Rôles: ${roles.length}`;
          }).join('\n\n'));

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }
    }
  },
};
