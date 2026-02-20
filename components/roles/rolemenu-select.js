// ===================================
// Ultra Suite — Composant Role Menu Select
// Gère les interactions select menu pour les rôles
// ===================================

const { getDb } = require('../../database');
const { createModuleLogger } = require('../../core/logger');

const log = createModuleLogger('RoleMenu');

module.exports = {
  prefix: 'rolemenu-',
  type: 'select',
  module: 'roles',

  async execute(interaction) {
    const customId = interaction.customId;
    const menuId = customId.replace('rolemenu-', '');
    const guildId = interaction.guildId;
    const db = getDb();

    const menu = await db('role_menus').where('id', menuId).where('guild_id', guildId).first();
    if (!menu) {
      return interaction.reply({ content: '❌ Ce menu de rôles n\'existe plus.', ephemeral: true });
    }

    const allRoles = JSON.parse(menu.roles || '[]');
    const selectedIds = interaction.values; // IDs des rôles sélectionnés
    const member = interaction.member;

    await interaction.deferReply({ ephemeral: true });

    const added = [];
    const removed = [];
    const errors = [];

    for (const roleData of allRoles) {
      const role = interaction.guild.roles.cache.get(roleData.id);
      if (!role) continue;

      const hasRole = member.roles.cache.has(role.id);
      const isSelected = selectedIds.includes(role.id);

      if (isSelected && !hasRole) {
        // Ajouter le rôle
        try {
          await member.roles.add(role, 'Menu de rôles');
          added.push(role);
        } catch {
          errors.push(role.name);
        }
      } else if (!isSelected && hasRole) {
        // Retirer le rôle
        try {
          await member.roles.remove(role, 'Menu de rôles');
          removed.push(role);
        } catch {
          errors.push(role.name);
        }
      }
    }

    // Résumé
    const lines = [];
    if (added.length > 0) lines.push(`✅ Ajouté(s) : ${added.map((r) => r.toString()).join(', ')}`);
    if (removed.length > 0) lines.push(`❌ Retiré(s) : ${removed.map((r) => r.toString()).join(', ')}`);
    if (errors.length > 0) lines.push(`⚠️ Erreur(s) : ${errors.join(', ')}`);
    if (lines.length === 0) lines.push('ℹ️ Aucun changement.');

    await interaction.editReply({ content: lines.join('\n') });
  },
};