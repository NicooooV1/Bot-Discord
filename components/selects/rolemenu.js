// ===================================
// Ultra Suite — Select: rolemenu_*
// Toggle des rôles via select menu
// ===================================

const { getDb } = require('../../database/index');

module.exports = {
  id: 'rolemenu_',
  module: 'roles',

  async execute(interaction) {
    const selectedRoles = interaction.values; // Array de roleIds
    const member = interaction.member;

    // Trouver le menuId depuis le customId (format: rolemenu_{menuId})
    const menuId = interaction.customId.split('_')[1];

    // Récupérer les options du menu depuis la BDD pour savoir quels rôles sont possibles
    const db = getDb();
    const menu = await db('role_menus')
      .where({ id: menuId, guild_id: interaction.guild.id })
      .first();

    if (!menu) {
      return interaction.reply({ content: '❌ Menu introuvable.', ephemeral: true });
    }

    let menuOptions;
    try {
      menuOptions = JSON.parse(menu.options || '[]');
    } catch {
      menuOptions = [];
    }

    const allMenuRoleIds = menuOptions.map((opt) => opt.roleId);

    const added = [];
    const removed = [];

    // Ajouter les rôles sélectionnés
    for (const roleId of selectedRoles) {
      if (!member.roles.cache.has(roleId)) {
        try {
          await member.roles.add(roleId, 'Self-role via menu');
          const role = interaction.guild.roles.cache.get(roleId);
          added.push(role?.name || roleId);
        } catch {}
      }
    }

    // Retirer les rôles du menu qui ne sont PAS dans la sélection
    for (const roleId of allMenuRoleIds) {
      if (!selectedRoles.includes(roleId) && member.roles.cache.has(roleId)) {
        try {
          await member.roles.remove(roleId, 'Self-role via menu (désélectionné)');
          const role = interaction.guild.roles.cache.get(roleId);
          removed.push(role?.name || roleId);
        } catch {}
      }
    }

    const changes = [];
    if (added.length > 0) changes.push(`✅ Ajoutés : ${added.join(', ')}`);
    if (removed.length > 0) changes.push(`❌ Retirés : ${removed.join(', ')}`);

    await interaction.reply({
      content: changes.length > 0 ? changes.join('\n') : '✅ Aucun changement.',
      ephemeral: true,
    });
  },
};
