// ===================================
// Ultra Suite — /config
// Point d'entrée UNIQUE pour toute la configuration
//
// Sans argument : dashboard global (tous les modules)
// Avec module    : dashboard interactif du module
//
// Remplace les anciens /config view|set|reset, /module, /setup
// ===================================

const {
  SlashCommandBuilder,
  EmbedBuilder,
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  StringSelectMenuBuilder,
  PermissionFlagsBits,
} = require('discord.js');

const configService = require('../../core/configService');
const moduleRegistry = require('../../core/moduleRegistry');
const configEngine = require('../../core/configEngine');

module.exports = {
  module: 'admin',
  adminOnly: true,
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('config')
    .setDescription('Configurer le bot — tableau de bord interactif')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
    .addStringOption((opt) =>
      opt
        .setName('module')
        .setDescription('Module à configurer (vide = vue globale)')
        .setRequired(false)
        .setAutocomplete(true)
    ),

  // ===================================
  // Autocomplete : liste les modules enregistrés
  // ===================================
  async autocomplete(interaction) {
    const focused = interaction.options.getFocused().toLowerCase();
    const allModules = moduleRegistry.getAll();

    const filtered = allModules
      .filter(
        (m) =>
          m.id.toLowerCase().includes(focused) ||
          m.name.toLowerCase().includes(focused)
      )
      .slice(0, 25)
      .map((m) => ({
        name: `${m.emoji} ${m.name}`,
        value: m.id,
      }));

    await interaction.respond(filtered);
  },

  // ===================================
  // Exécution principale
  // ===================================
  async execute(interaction) {
    const guildId = interaction.guildId;
    const moduleId = interaction.options.getString('module');

    if (moduleId) {
      return showModuleDashboard(interaction, guildId, moduleId);
    }

    return showGlobalDashboard(interaction, guildId);
  },
};

// ===================================
// Dashboard Global — Vue de tous les modules
// ===================================

async function showGlobalDashboard(interaction, guildId) {
  const config = await configService.get(guildId);
  const modules = await configService.getModules(guildId);
  const allManifests = moduleRegistry.getAll();
  const categories = moduleRegistry.getCategories();

  const embed = new EmbedBuilder()
    .setTitle('⚙️ Configuration du serveur')
    .setDescription(
      'Tableau de bord de tous les modules.\n' +
      'Utilisez `/config module:<nom>` pour configurer un module.\n' +
      'Ou sélectionnez un module ci-dessous.'
    )
    .setColor(0x5865F2)
    .setTimestamp();

  // Stats globales
  const total = allManifests.length;
  let active = 0;
  let unconfigured = 0;
  let disabled = 0;

  for (const manifest of allManifests) {
    const status = configEngine.getModuleStatus(
      manifest.id,
      { modules: config?.modules || {} },
      modules?.[manifest.id] || false
    );
    if (status.state === 'ACTIVE') active++;
    else if (status.state === 'ENABLED_UNCONFIGURED') unconfigured++;
    else disabled++;
  }

  embed.addFields({
    name: '📊 Résumé',
    value:
      `🟢 Actifs : **${active}** — ` +
      `🟡 À configurer : **${unconfigured}** — ` +
      `⚫ Désactivés : **${disabled}** / **${total}**`,
    inline: false,
  });

  // Lister par catégorie
  for (const [catId, catModules] of categories) {
    const catInfo = moduleRegistry.CATEGORY_LABELS[catId] || {
      label: catId,
      emoji: '📦',
    };

    const lines = catModules.map((m) => {
      const status = configEngine.getModuleStatus(
        m.id,
        { modules: config?.modules || {} },
        modules?.[m.id] || false
      );
      return `${status.stateEmoji} ${m.emoji} **${m.name}** — ${status.stateLabel}`;
    });

    embed.addFields({
      name: `${catInfo.emoji} ${catInfo.label}`,
      value: lines.join('\n') || '*Aucun module*',
      inline: false,
    });
  }

  // Select menu pour choisir un module
  const selectOptions = allManifests.slice(0, 25).map((m) => ({
    label: m.name,
    description: m.description.substring(0, 100),
    value: m.id,
    emoji: m.emoji,
  }));

  const selectRow = new ActionRowBuilder().addComponents(
    new StringSelectMenuBuilder()
      .setCustomId('config:select_module')
      .setPlaceholder('📦 Sélectionner un module à configurer...')
      .addOptions(selectOptions)
  );

  // Boutons d'actions globales
  const buttonRow = new ActionRowBuilder().addComponents(
    new ButtonBuilder()
      .setCustomId('config:global:export')
      .setLabel('Exporter')
      .setEmoji('📤')
      .setStyle(ButtonStyle.Secondary),
    new ButtonBuilder()
      .setCustomId('config:global:import')
      .setLabel('Importer')
      .setEmoji('📥')
      .setStyle(ButtonStyle.Secondary),
    new ButtonBuilder()
      .setCustomId('config:global:reset_all')
      .setLabel('Tout réinitialiser')
      .setEmoji('🗑️')
      .setStyle(ButtonStyle.Danger)
  );

  await interaction.reply({
    embeds: [embed],
    components: [selectRow, buttonRow],
    ephemeral: true,
  });
}

// ===================================
// Dashboard Module — Vue détaillée d'un module
// ===================================

async function showModuleDashboard(interaction, guildId, moduleId) {
  const manifest = moduleRegistry.get(moduleId);
  if (!manifest) {
    return interaction.reply({
      content: `❌ Module **${moduleId}** inconnu. Utilisez \`/config\` pour voir la liste.`,
      ephemeral: true,
    });
  }

  const config = await configService.get(guildId);
  const modules = await configService.getModules(guildId);
  const isEnabled = modules?.[moduleId] || false;
  const moduleConfig = configEngine.getModuleConfig(
    { modules: config?.modules || {} },
    moduleId
  );
  const status = configEngine.getModuleStatus(
    moduleId,
    { modules: config?.modules || {} },
    isEnabled
  );

  const embed = buildModuleEmbed(manifest, moduleConfig, status, isEnabled);
  const components = buildModuleComponents(manifest, moduleId, isEnabled, status);

  // Répondre ou mettre à jour (si appelé depuis un composant)
  if (interaction.replied || interaction.deferred) {
    await interaction.editReply({ embeds: [embed], components });
  } else {
    await interaction.reply({ embeds: [embed], components, ephemeral: true });
  }
}

// ===================================
// Builders d'embeds et composants
// ===================================

function buildModuleEmbed(manifest, moduleConfig, status, isEnabled) {
  const embed = new EmbedBuilder()
    .setTitle(`${manifest.emoji} ${manifest.name}`)
    .setDescription(manifest.description)
    .setColor(
      status.state === 'ACTIVE'
        ? 0x57F287
        : status.state === 'ENABLED_UNCONFIGURED'
          ? 0xFEE75C
          : 0x99AAB5
    )
    .setTimestamp();

  // État
  embed.addFields({
    name: 'État',
    value: `${status.stateEmoji} ${status.stateLabel}`,
    inline: true,
  });

  // Catégorie
  const catInfo = moduleRegistry.CATEGORY_LABELS[manifest.category] || {
    label: manifest.category,
    emoji: '📦',
  };
  embed.addFields({
    name: 'Catégorie',
    value: `${catInfo.emoji} ${catInfo.label}`,
    inline: true,
  });

  // Dépendances
  if (manifest.dependencies.length > 0) {
    embed.addFields({
      name: 'Dépendances',
      value: manifest.dependencies.map((d) => `\`${d}\``).join(', '),
      inline: true,
    });
  }

  // Commandes
  if (manifest.commands.length > 0) {
    embed.addFields({
      name: '🔧 Commandes',
      value: manifest.commands.map((c) => `\`/${c}\``).join(', '),
      inline: false,
    });
  }

  // Configuration actuelle
  const schema = manifest.configSchema;
  const schemaKeys = Object.keys(schema);

  if (schemaKeys.length > 0) {
    const configLines = schemaKeys.map((key) => {
      const s = schema[key];
      const value = moduleConfig[key];
      const isRequired = s.required;
      const isMissing =
        isRequired && (value === null || value === undefined || value === '');

      let displayValue;
      if (value === null || value === undefined) {
        displayValue = '*Non défini*';
      } else if (s.type === 'channel') {
        displayValue = `<#${value}>`;
      } else if (s.type === 'role') {
        displayValue = `<@&${value}>`;
      } else if (s.type === 'boolean') {
        displayValue = value ? '✅ Activé' : '❌ Désactivé';
      } else if (
        s.type === 'json' ||
        s.type === 'channels' ||
        s.type === 'roles'
      ) {
        displayValue =
          typeof value === 'object'
            ? `\`${JSON.stringify(value).substring(0, 50)}\``
            : `\`${value}\``;
      } else {
        displayValue = `\`${String(value).substring(0, 50)}\``;
      }

      const prefix = isMissing ? '⚠️' : isRequired ? '🔹' : '▫️';
      return `${prefix} **${s.label}** : ${displayValue}`;
    });

    embed.addFields({
      name: '📋 Configuration',
      value: configLines.join('\n'),
      inline: false,
    });
  }

  // Champs manquants
  if (status.missing.length > 0) {
    embed.addFields({
      name: '⚠️ Configuration requise manquante',
      value: status.missing
        .map((m) => `→ **${m.label}** (\`${m.type}\`)`)
        .join('\n'),
      inline: false,
    });
  }

  // Permissions bot requises
  if (manifest.requiredPermissions.length > 0) {
    embed.addFields({
      name: '🔑 Permissions bot requises',
      value: manifest.requiredPermissions.map((p) => `\`${p}\``).join(', '),
      inline: false,
    });
  }

  return embed;
}

function buildModuleComponents(manifest, moduleId, isEnabled, status) {
  const rows = [];

  // Ligne 1 : Toggle + Configure + Permissions
  const row1 = new ActionRowBuilder().addComponents(
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:toggle`)
      .setLabel(isEnabled ? 'Désactiver' : 'Activer')
      .setEmoji(isEnabled ? '🔴' : '🟢')
      .setStyle(isEnabled ? ButtonStyle.Danger : ButtonStyle.Success),
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:params`)
      .setLabel('Configurer')
      .setEmoji('⚙️')
      .setStyle(ButtonStyle.Primary)
      .setDisabled(Object.keys(manifest.configSchema).length === 0),
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:perms`)
      .setLabel('Permissions')
      .setEmoji('🔒')
      .setStyle(ButtonStyle.Secondary)
  );
  rows.push(row1);

  // Ligne 2 : Templates + Export + Reset + Retour
  const row2 = new ActionRowBuilder().addComponents(
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:templates`)
      .setLabel('Messages')
      .setEmoji('💬')
      .setStyle(ButtonStyle.Secondary),
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:export`)
      .setLabel('Exporter')
      .setEmoji('📤')
      .setStyle(ButtonStyle.Secondary),
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:reset`)
      .setLabel('Réinitialiser')
      .setEmoji('🗑️')
      .setStyle(ButtonStyle.Danger),
    new ButtonBuilder()
      .setCustomId('config:back_to_global')
      .setLabel('Retour')
      .setEmoji('◀️')
      .setStyle(ButtonStyle.Secondary)
  );
  rows.push(row2);

  return rows;
}

// Exports des builders pour les component handlers
module.exports.showGlobalDashboard = showGlobalDashboard;
module.exports.showModuleDashboard = showModuleDashboard;
module.exports.buildModuleEmbed = buildModuleEmbed;
module.exports.buildModuleComponents = buildModuleComponents;