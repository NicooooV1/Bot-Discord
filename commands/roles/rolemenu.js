// ===================================
// Ultra Suite — /rolemenu
// Créer un menu de rôles avec select menu
// /rolemenu create | add | remove | send
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ActionRowBuilder, StringSelectMenuBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'roles',
  adminOnly: true,
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('rolemenu')
    .setDescription('Gérer les menus de rôles')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageRoles)
    .addSubcommand((sub) =>
      sub.setName('create').setDescription('Créer un nouveau menu de rôles')
        .addStringOption((opt) => opt.setName('nom').setDescription('Nom du menu').setRequired(true))
        .addStringOption((opt) => opt.setName('description').setDescription('Description affichée'))
        .addBooleanOption((opt) => opt.setName('multiple').setDescription('Permettre la sélection multiple (défaut: oui)')))
    .addSubcommand((sub) =>
      sub.setName('add').setDescription('Ajouter un rôle au menu')
        .addStringOption((opt) => opt.setName('nom').setDescription('Nom du menu').setRequired(true))
        .addRoleOption((opt) => opt.setName('role').setDescription('Rôle à ajouter').setRequired(true))
        .addStringOption((opt) => opt.setName('label').setDescription('Label affiché (défaut: nom du rôle)'))
        .addStringOption((opt) => opt.setName('emoji').setDescription('Emoji du rôle')))
    .addSubcommand((sub) =>
      sub.setName('remove').setDescription('Retirer un rôle du menu')
        .addStringOption((opt) => opt.setName('nom').setDescription('Nom du menu').setRequired(true))
        .addRoleOption((opt) => opt.setName('role').setDescription('Rôle à retirer').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('send').setDescription('Envoyer le menu dans le channel actuel')
        .addStringOption((opt) => opt.setName('nom').setDescription('Nom du menu').setRequired(true))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();

    // === CREATE ===
    if (sub === 'create') {
      const name = interaction.options.getString('nom').toLowerCase().replace(/\s+/g, '-');
      const description = interaction.options.getString('description') || '';
      const multiple = interaction.options.getBoolean('multiple') ?? true;

      // Vérifier si le menu existe déjà
      const existing = await db('role_menus').where('guild_id', guildId).where('name', name).first();
      if (existing) {
        return interaction.reply({ content: `❌ Un menu **${name}** existe déjà.`, ephemeral: true });
      }

      await db('role_menus').insert({
        guild_id: guildId,
        name,
        description,
        multiple,
        roles: JSON.stringify([]),
      });

      return interaction.reply({
        content: `✅ Menu **${name}** créé !\nAjoutez des rôles avec \`/rolemenu add nom:${name} role:@role\`\nPuis envoyez-le avec \`/rolemenu send nom:${name}\``,
        ephemeral: true,
      });
    }

    // === ADD ===
    if (sub === 'add') {
      const name = interaction.options.getString('nom').toLowerCase().replace(/\s+/g, '-');
      const role = interaction.options.getRole('role');
      const label = interaction.options.getString('label') || role.name;
      const emoji = interaction.options.getString('emoji');

      const menu = await db('role_menus').where('guild_id', guildId).where('name', name).first();
      if (!menu) {
        return interaction.reply({ content: `❌ Menu **${name}** introuvable. Créez-le d'abord avec \`/rolemenu create\`.`, ephemeral: true });
      }

      const roles = JSON.parse(menu.roles || '[]');

      // Vérifier si le rôle est déjà dans le menu
      if (roles.some((r) => r.id === role.id)) {
        return interaction.reply({ content: `❌ ${role} est déjà dans le menu **${name}**.`, ephemeral: true });
      }

      // Max 25 rôles (limite Discord select menu)
      if (roles.length >= 25) {
        return interaction.reply({ content: '❌ Maximum 25 rôles par menu.', ephemeral: true });
      }

      roles.push({ id: role.id, label, emoji: emoji || null });
      await db('role_menus').where('id', menu.id).update({ roles: JSON.stringify(roles) });

      return interaction.reply({
        content: `✅ ${role} ajouté au menu **${name}** (${roles.length}/25 rôles).`,
        ephemeral: true,
      });
    }

    // === REMOVE ===
    if (sub === 'remove') {
      const name = interaction.options.getString('nom').toLowerCase().replace(/\s+/g, '-');
      const role = interaction.options.getRole('role');

      const menu = await db('role_menus').where('guild_id', guildId).where('name', name).first();
      if (!menu) {
        return interaction.reply({ content: `❌ Menu **${name}** introuvable.`, ephemeral: true });
      }

      const roles = JSON.parse(menu.roles || '[]');
      const filtered = roles.filter((r) => r.id !== role.id);

      if (filtered.length === roles.length) {
        return interaction.reply({ content: `❌ ${role} n'est pas dans le menu **${name}**.`, ephemeral: true });
      }

      await db('role_menus').where('id', menu.id).update({ roles: JSON.stringify(filtered) });

      return interaction.reply({
        content: `✅ ${role} retiré du menu **${name}** (${filtered.length}/25 rôles).`,
        ephemeral: true,
      });
    }

    // === SEND ===
    if (sub === 'send') {
      const name = interaction.options.getString('nom').toLowerCase().replace(/\s+/g, '-');

      const menu = await db('role_menus').where('guild_id', guildId).where('name', name).first();
      if (!menu) {
        return interaction.reply({ content: `❌ Menu **${name}** introuvable.`, ephemeral: true });
      }

      const roles = JSON.parse(menu.roles || '[]');
      if (roles.length === 0) {
        return interaction.reply({ content: `❌ Le menu **${name}** n'a aucun rôle. Ajoutez-en avec \`/rolemenu add\`.`, ephemeral: true });
      }

      // Construire le select menu
      const options = roles.map((r) => {
        const opt = { label: r.label, value: r.id };
        if (r.emoji) opt.emoji = r.emoji;
        return opt;
      });

      const selectMenu = new StringSelectMenuBuilder()
        .setCustomId(`rolemenu-${menu.id}`)
        .setPlaceholder('Choisissez vos rôles')
        .setMinValues(0)
        .setMaxValues(menu.multiple ? roles.length : 1)
        .addOptions(options);

      const row = new ActionRowBuilder().addComponents(selectMenu);

      const embed = new EmbedBuilder()
        .setTitle(`🎭 ${menu.description || name}`)
        .setDescription(roles.map((r) => `${r.emoji || '•'} <@&${r.id}> — ${r.label}`).join('\n'))
        .setColor(0x5865F2)
        .setFooter({ text: menu.multiple ? 'Sélection multiple autorisée' : 'Un seul rôle à la fois' });

      await interaction.channel.send({ embeds: [embed], components: [row] });
      return interaction.reply({ content: `✅ Menu **${name}** envoyé.`, ephemeral: true });
    }
  },
};