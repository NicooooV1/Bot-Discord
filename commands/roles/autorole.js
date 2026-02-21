// ===================================
// Ultra Suite — /autorole
// Attribution automatique de rôles
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'roles',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('autorole')
    .setDescription('Gérer les rôles automatiques')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageRoles)
    .addSubcommand((s) =>
      s.setName('add').setDescription('Ajouter un auto-role')
        .addRoleOption((o) => o.setName('role').setDescription('Rôle').setRequired(true))
        .addStringOption((o) => o.setName('type').setDescription('Type').setRequired(true).addChoices(
          { name: 'Membre (tous)', value: 'member' },
          { name: 'Bot', value: 'bot' },
          { name: 'Humain', value: 'human' },
        ))
        .addIntegerOption((o) => o.setName('delai').setDescription('Délai en minutes (0 = immédiat)')),
    )
    .addSubcommand((s) =>
      s.setName('remove').setDescription('Retirer un auto-role')
        .addRoleOption((o) => o.setName('role').setDescription('Rôle').setRequired(true)),
    )
    .addSubcommand((s) => s.setName('list').setDescription('Lister les auto-roles')),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();
    const guildId = interaction.guildId;

    switch (sub) {
      case 'add': {
        const role = interaction.options.getRole('role');
        const type = interaction.options.getString('type');
        const delay = interaction.options.getInteger('delai') || 0;

        // Store in guild_config modules
        const config = await db('guild_config').where({ guild_id: guildId }).first();
        const modules = config?.modules ? JSON.parse(config.modules) : {};
        if (!modules.autoroles) modules.autoroles = [];

        const existing = modules.autoroles.findIndex((r) => r.roleId === role.id);
        if (existing >= 0) modules.autoroles[existing] = { roleId: role.id, type, delay };
        else modules.autoroles.push({ roleId: role.id, type, delay });

        await db('guild_config').where({ guild_id: guildId }).update({ modules: JSON.stringify(modules) });

        return interaction.reply({ content: `✅ Auto-role ${role} ajouté (type: ${type}${delay ? `, délai: ${delay}min` : ''}).`, ephemeral: true });
      }

      case 'remove': {
        const role = interaction.options.getRole('role');
        const config = await db('guild_config').where({ guild_id: guildId }).first();
        const modules = config?.modules ? JSON.parse(config.modules) : {};
        if (!modules.autoroles) return interaction.reply({ content: '❌ Aucun auto-role.', ephemeral: true });

        modules.autoroles = modules.autoroles.filter((r) => r.roleId !== role.id);
        await db('guild_config').where({ guild_id: guildId }).update({ modules: JSON.stringify(modules) });

        return interaction.reply({ content: `✅ Auto-role ${role} retiré.`, ephemeral: true });
      }

      case 'list': {
        const config = await db('guild_config').where({ guild_id: guildId }).first();
        const modules = config?.modules ? JSON.parse(config.modules) : {};
        const autoroles = modules.autoroles || [];

        if (!autoroles.length) return interaction.reply({ content: 'Aucun auto-role configuré.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle('🤖 Auto-Roles')
          .setColor(0x3498DB)
          .setDescription(autoroles.map((r) => `<@&${r.roleId}> — Type: ${r.type}${r.delay ? ` • Délai: ${r.delay}min` : ''}`).join('\n'));

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }
    }
  },
};
