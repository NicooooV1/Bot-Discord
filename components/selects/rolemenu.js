// ===================================
// Ultra Suite — Select: rolemenu_*
// Toggle des rôles via select menu
// ===================================

const { t } = require('../../core/i18n');

module.exports = {
  id: 'rolemenu_',

  async execute(interaction) {
    const selectedRoles = interaction.values; // Array de roleIds
    const member = interaction.member;

    // Trouver le menuId
    const menuId = interaction.customId.split('_')[1];

    const added = [];
    const removed = [];

    // Ajouter les rôles sélectionnés, retirer les non-sélectionnés
    // (Pour le mode multi, on n'enlève que si on connaît les options du menu)
    for (const roleId of selectedRoles) {
      if (!member.roles.cache.has(roleId)) {
        try {
          await member.roles.add(roleId, 'Self-role via menu');
          const role = interaction.guild.roles.cache.get(roleId);
          added.push(role?.name || roleId);
        } catch {}
      }
    }

    const changes = [];
    if (added.length > 0) changes.push(`✅ Ajoutés : ${added.join(', ')}`);
    if (removed.length > 0) changes.push(`❌ Retirés : ${removed.join(', ')}`);

    await interaction.reply({
      content: changes.length > 0 ? changes.join('\n') : '✅ Rôles mis à jour.',
      ephemeral: true,
    });
  },
};
