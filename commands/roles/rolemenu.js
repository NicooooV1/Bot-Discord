// ===================================
// Ultra Suite — Roles: /rolemenu
// Self-role menus avec boutons/select
// ===================================

const {
  SlashCommandBuilder,
  PermissionFlagsBits,
  StringSelectMenuBuilder,
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
} = require('discord.js');
const { getDb } = require('../../database');
const { createEmbed, successEmbed, errorEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'roles',
  cooldown: 5,
  data: new SlashCommandBuilder()
    .setName('rolemenu')
    .setDescription('Crée un menu de rôles auto-assignables')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageRoles)
    .addSubcommand((sub) =>
      sub
        .setName('create')
        .setDescription('Crée un nouveau menu de rôles')
        .addStringOption((opt) => opt.setName('titre').setDescription('Titre du menu').setRequired(true))
        .addStringOption((opt) => opt.setName('description').setDescription('Description'))
        .addStringOption((opt) =>
          opt
            .setName('mode')
            .setDescription('Mode de sélection')
            .addChoices({ name: 'Multiple', value: 'multi' }, { name: 'Unique', value: 'single' })
        )
        .addStringOption((opt) =>
          opt
            .setName('type')
            .setDescription('Type d\'interaction')
            .addChoices({ name: 'Boutons', value: 'buttons' }, { name: 'Menu déroulant', value: 'select' })
        )
    )
    .addSubcommand((sub) =>
      sub
        .setName('add')
        .setDescription('Ajoute un rôle au menu')
        .addIntegerOption((opt) => opt.setName('menu_id').setDescription('ID du menu').setRequired(true))
        .addRoleOption((opt) => opt.setName('role').setDescription('Rôle').setRequired(true))
        .addStringOption((opt) => opt.setName('label').setDescription('Label'))
        .addStringOption((opt) => opt.setName('emoji').setDescription('Emoji'))
        .addStringOption((opt) => opt.setName('description').setDescription('Description'))
    )
    .addSubcommand((sub) =>
      sub
        .setName('send')
        .setDescription('Envoie le menu dans le salon')
        .addIntegerOption((opt) => opt.setName('menu_id').setDescription('ID du menu').setRequired(true))
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();

    switch (sub) {
      case 'create': {
        const title = interaction.options.getString('titre');
        const description = interaction.options.getString('description') || '';
        const mode = interaction.options.getString('mode') || 'multi';
        const type = interaction.options.getString('type') || 'buttons';

        const [id] = await db('role_menus').insert({
          guild_id: interaction.guild.id,
          channel_id: interaction.channel.id,
          type,
          mode,
          title,
          description,
          options: JSON.stringify([]),
        });

        return interaction.reply({
          embeds: [successEmbed(`✅ Menu de rôles **#${id}** créé.\nAjoutez des rôles avec \`/rolemenu add menu_id:${id} role:...\``)],
          ephemeral: true,
        });
      }

      case 'add': {
        const menuId = interaction.options.getInteger('menu_id');
        const role = interaction.options.getRole('role');
        const label = interaction.options.getString('label') || role.name;
        const emoji = interaction.options.getString('emoji');
        const description = interaction.options.getString('description') || '';

        const menu = await db('role_menus').where({ id: menuId, guild_id: interaction.guild.id }).first();
        if (!menu) return interaction.reply({ embeds: [errorEmbed('❌ Menu introuvable.')], ephemeral: true });

        const options = JSON.parse(menu.options);
        if (options.length >= 25) return interaction.reply({ embeds: [errorEmbed('❌ Maximum 25 rôles par menu.')], ephemeral: true });

        options.push({ roleId: role.id, label, emoji, description });
        await db('role_menus').where('id', menuId).update({ options: JSON.stringify(options) });

        return interaction.reply({
          embeds: [successEmbed(`✅ Rôle ${role} ajouté au menu #${menuId} (${options.length} rôles)`)],
          ephemeral: true,
        });
      }

      case 'send': {
        const menuId = interaction.options.getInteger('menu_id');
        const menu = await db('role_menus').where({ id: menuId, guild_id: interaction.guild.id }).first();
        if (!menu) return interaction.reply({ embeds: [errorEmbed('❌ Menu introuvable.')], ephemeral: true });

        const options = JSON.parse(menu.options);
        if (options.length === 0) return interaction.reply({ embeds: [errorEmbed('❌ Aucun rôle dans ce menu.')], ephemeral: true });

        const embed = createEmbed('primary')
          .setTitle(menu.title)
          .setDescription(menu.description || 'Choisissez vos rôles :');

        let components;

        if (menu.type === 'select') {
          const select = new StringSelectMenuBuilder()
            .setCustomId(`rolemenu_${menuId}`)
            .setPlaceholder('Choisir un rôle...')
            .setMinValues(0)
            .setMaxValues(menu.mode === 'single' ? 1 : options.length)
            .addOptions(
              options.map((o) => ({
                label: o.label,
                value: o.roleId,
                description: o.description || undefined,
                emoji: o.emoji || undefined,
              }))
            );
          components = [new ActionRowBuilder().addComponents(select)];
        } else {
          // Boutons (max 5 par row, max 5 rows)
          const rows = [];
          for (let i = 0; i < options.length; i += 5) {
            const row = new ActionRowBuilder();
            const slice = options.slice(i, i + 5);
            for (const o of slice) {
              const btn = new ButtonBuilder()
                .setCustomId(`rolebtn_${menuId}_${o.roleId}`)
                .setLabel(o.label)
                .setStyle(ButtonStyle.Secondary);
              if (o.emoji) btn.setEmoji(o.emoji);
              row.addComponents(btn);
            }
            rows.push(row);
          }
          components = rows;
        }

        const msg = await interaction.channel.send({ embeds: [embed], components });
        await db('role_menus').where('id', menuId).update({
          message_id: msg.id,
          channel_id: interaction.channel.id,
        });

        return interaction.reply({ embeds: [successEmbed('✅ Menu envoyé !')], ephemeral: true });
      }
    }
  },
};
