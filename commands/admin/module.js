// ===================================
// Ultra Suite — /module (DEPRECATED)
// Redirige vers /config (nouveau système unifié)
//
// La commande /module est absorbée par /config.
// Les actions enable/disable sont maintenant des boutons
// dans le dashboard interactif de chaque module.
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');
const configService = require('../../core/configService');
const moduleRegistry = require('../../core/moduleRegistry');
const configEngine = require('../../core/configEngine');

module.exports = {
  module: 'admin',
  adminOnly: true,
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('module')
    .setDescription('Gérer les modules — voir /config pour le dashboard complet')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
    .addSubcommand((sub) =>
      sub.setName('list').setDescription('Voir tous les modules et leur état'))
    .addSubcommand((sub) =>
      sub.setName('enable').setDescription('Activer un module')
        .addStringOption((opt) =>
          opt.setName('nom').setDescription('Nom du module').setRequired(true).setAutocomplete(true)))
    .addSubcommand((sub) =>
      sub.setName('disable').setDescription('Désactiver un module')
        .addStringOption((opt) =>
          opt.setName('nom').setDescription('Nom du module').setRequired(true).setAutocomplete(true))),

  async autocomplete(interaction) {
    const focused = interaction.options.getFocused().toLowerCase();
    const allModules = moduleRegistry.getAll();

    const filtered = allModules
      .filter((m) => m.id.toLowerCase().includes(focused) || m.name.toLowerCase().includes(focused))
      .slice(0, 25)
      .map((m) => ({ name: `${m.emoji} ${m.name}`, value: m.id }));

    await interaction.respond(filtered);
  },

  async execute(interaction) {
    const guildId = interaction.guildId;
    const sub = interaction.options.getSubcommand();

    if (sub === 'list') {
      const modules = await configService.getModules(guildId);
      const config = await configService.get(guildId);
      const allManifests = moduleRegistry.getAll();

      const lines = allManifests
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((m) => {
          const status = configEngine.getModuleStatus(
            m.id,
            { modules: config?.modules || {} },
            modules?.[m.id] || false
          );
          return `${status.stateEmoji} ${m.emoji} **${m.name}** — ${status.stateLabel}`;
        });

      const embed = new EmbedBuilder()
        .setTitle('📦 Modules du serveur')
        .setDescription(lines.join('\n'))
        .setColor(0x5865F2)
        .setFooter({
          text: '💡 Utilisez /config pour le tableau de bord interactif complet',
        })
        .setTimestamp();

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    if (sub === 'enable') {
      const name = interaction.options.getString('nom');

      // Vérifier les dépendances
      const modules = await configService.getModules(guildId);
      const depCheck = moduleRegistry.checkDependencies(name, modules || {});
      if (!depCheck.satisfied) {
        return interaction.reply({
          content: `⚠️ Impossible — dépendances manquantes : ${depCheck.missing.map(d => `\`${d}\``).join(', ')}`,
          ephemeral: true,
        });
      }

      await configService.setModule(guildId, name, true);

      const manifest = moduleRegistry.get(name);
      const missing = moduleRegistry.getMissingRequired(name, {});

      let msg = `✅ Module **${manifest?.name || name}** activé.`;
      if (missing.length > 0) {
        msg += `\n\n⚠️ **Configuration requise :**\n${missing.map(m => `→ ${m.label} (\`${m.type}\`)`).join('\n')}`;
        msg += `\n\nUtilisez \`/config module:${name}\` pour configurer.`;
      }

      return interaction.reply({ content: msg, ephemeral: true });
    }

    if (sub === 'disable') {
      const name = interaction.options.getString('nom');

      // Vérifier les dépendants
      const modules = await configService.getModules(guildId);
      const allManifests = moduleRegistry.getAll();
      const dependents = allManifests.filter(
        (m) => m.dependencies.includes(name) && (modules?.[m.id] || false)
      );

      if (dependents.length > 0) {
        return interaction.reply({
          content: `⚠️ Impossible — requis par : ${dependents.map(d => `\`${d.name}\``).join(', ')}`,
          ephemeral: true,
        });
      }

      await configService.setModule(guildId, name, false);
      const manifest = moduleRegistry.get(name);
      return interaction.reply({
        content: `❌ Module **${manifest?.name || name}** désactivé.`,
        ephemeral: true,
      });
    }
  },
};