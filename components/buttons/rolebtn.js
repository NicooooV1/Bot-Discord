// ===================================
// Ultra Suite — Button: rolebtn_*
// Toggle un rôle via bouton
// ===================================

const { t } = require('../../core/i18n');

module.exports = {
  id: 'rolebtn_',

  async execute(interaction) {
    // customId format: rolebtn_{menuId}_{roleId}
    const parts = interaction.customId.split('_');
    const roleId = parts[2];

    if (!roleId) return;

    const role = interaction.guild.roles.cache.get(roleId);
    if (!role) {
      return interaction.reply({ content: '❌ Rôle introuvable.', ephemeral: true });
    }

    const member = interaction.member;
    if (member.roles.cache.has(roleId)) {
      await member.roles.remove(roleId, 'Self-role');
      return interaction.reply({
        content: t('roles.removed', undefined, { role: role.name }),
        ephemeral: true,
      });
    } else {
      await member.roles.add(roleId, 'Self-role');
      return interaction.reply({
        content: t('roles.added', undefined, { role: role.name }),
        ephemeral: true,
      });
    }
  },
};
