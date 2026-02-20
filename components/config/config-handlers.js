// ===================================
// Ultra Suite — Config Component Handlers
// Gère TOUTES les interactions du dashboard /config
//
// Custom IDs :
//   config:select_module          → Select menu global
//   config:back_to_global         → Retour au dashboard
//   config:<moduleId>:toggle      → Activer/désactiver
//   config:<moduleId>:params      → Ouvrir sélection paramètre
//   config:<moduleId>:param_set   → Select d'un paramètre
//   config:<moduleId>:param_modal → Modal de saisie
//   config:<moduleId>:perms       → Gérer permissions
//   config:<moduleId>:templates   → Gérer templates
//   config:<moduleId>:export      → Exporter config module
//   config:<moduleId>:reset       → Reset config module
//   config:<moduleId>:confirm_reset → Confirmation reset
//   config:global:export          → Export global
//   config:global:import          → Import global
//   config:global:reset_all       → Reset all
//   config:global:confirm_reset   → Confirmation reset global
// ===================================

const {
  EmbedBuilder,
  ActionRowBuilder,
  ButtonBuilder,
  ButtonStyle,
  StringSelectMenuBuilder,
  ModalBuilder,
  TextInputBuilder,
  TextInputStyle,
  PermissionFlagsBits,
} = require('discord.js');

const configService = require('../../core/configService');
const moduleRegistry = require('../../core/moduleRegistry');
const configEngine = require('../../core/configEngine');
const {
  showGlobalDashboard,
  showModuleDashboard,
  buildModuleEmbed,
  buildModuleComponents,
} = require('../../commands/admin/config');

module.exports = {
  prefix: 'config:',
  type: 'any',
  module: 'admin',

  async execute(interaction) {
    // Seuls les admins peuvent interagir
    if (!interaction.memberPermissions?.has(PermissionFlagsBits.Administrator)) {
      return interaction.reply({
        content: '❌ Seuls les administrateurs peuvent modifier la configuration.',
        ephemeral: true,
      });
    }

    const customId = interaction.customId;
    const guildId = interaction.guildId;

    // ===================================
    // Routing principal
    // ===================================

    // Select menu : sélection d'un module
    if (customId === 'config:select_module') {
      return handleSelectModule(interaction, guildId);
    }

    // Retour au dashboard global
    if (customId === 'config:back_to_global') {
      return handleBackToGlobal(interaction, guildId);
    }

    // Actions globales
    if (customId.startsWith('config:global:')) {
      return handleGlobalAction(interaction, guildId, customId);
    }

    // Parse module ID et action depuis le customId
    // Format: config:<moduleId>:<action>
    const parts = customId.split(':');
    if (parts.length < 3) return;

    const moduleId = parts[1];
    const action = parts.slice(2).join(':'); // Préserver les sous-actions

    // Vérifier que le module existe
    if (!moduleRegistry.has(moduleId)) {
      return interaction.reply({
        content: `❌ Module inconnu : \`${moduleId}\``,
        ephemeral: true,
      });
    }

    // Handle dynamic sub-actions (set_bool:key:val, set_enum, param_modal:key)
    if (action.startsWith('set_bool:')) {
      // Format: set_bool:<key>:<true|false>
      const boolParts = action.split(':');
      const key = boolParts[1];
      const value = boolParts[2] === 'true';
      return handleSetValue(interaction, guildId, moduleId, key, value);
    }

    if (action === 'set_enum') {
      // Select menu value format: <key>:<value>
      const enumVal = interaction.values[0];
      const colonIdx = enumVal.indexOf(':');
      const key = enumVal.substring(0, colonIdx);
      const value = enumVal.substring(colonIdx + 1);
      return handleSetValue(interaction, guildId, moduleId, key, value);
    }

    if (action.startsWith('param_modal:')) {
      return handleParamModal(interaction, guildId, moduleId);
    }

    if (action === 'back_to_module') {
      await interaction.deferUpdate();
      return showModuleDashboard(interaction, guildId, moduleId);
    }

    switch (action) {
      case 'toggle':
        return handleToggle(interaction, guildId, moduleId);
      case 'params':
        return handleParamsMenu(interaction, guildId, moduleId);
      case 'param_set':
        return handleParamSelect(interaction, guildId, moduleId);
      case 'perms':
        return handlePermissions(interaction, guildId, moduleId);
      case 'templates':
        return handleTemplates(interaction, guildId, moduleId);
      case 'export':
        return handleModuleExport(interaction, guildId, moduleId);
      case 'reset':
        return handleResetConfirm(interaction, guildId, moduleId);
      case 'confirm_reset':
        return handleResetExecute(interaction, guildId, moduleId);
      case 'cancel_reset':
        return showModuleDashboard(interaction, guildId, moduleId);
      default:
        // Actions inconnues — ignorer silencieusement
        break;
    }
  },
};

// ===================================
// Select Module (dashboard global → module)
// ===================================

async function handleSelectModule(interaction, guildId) {
  const moduleId = interaction.values[0];
  await interaction.deferUpdate();
  return showModuleDashboard(interaction, guildId, moduleId);
}

// ===================================
// Retour au dashboard global
// ===================================

async function handleBackToGlobal(interaction, guildId) {
  await interaction.deferUpdate();

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

  // Stats
  let active = 0, unconfigured = 0, disabled = 0;
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
      `⚫ Désactivés : **${disabled}** / **${allManifests.length}**`,
    inline: false,
  });

  for (const [catId, catModules] of categories) {
    const catInfo = moduleRegistry.CATEGORY_LABELS[catId] || { label: catId, emoji: '📦' };
    const lines = catModules.map((m) => {
      const s = configEngine.getModuleStatus(m.id, { modules: config?.modules || {} }, modules?.[m.id] || false);
      return `${s.stateEmoji} ${m.emoji} **${m.name}** — ${s.stateLabel}`;
    });
    embed.addFields({ name: `${catInfo.emoji} ${catInfo.label}`, value: lines.join('\n') || '*Aucun*', inline: false });
  }

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

  const buttonRow = new ActionRowBuilder().addComponents(
    new ButtonBuilder().setCustomId('config:global:export').setLabel('Exporter').setEmoji('📤').setStyle(ButtonStyle.Secondary),
    new ButtonBuilder().setCustomId('config:global:import').setLabel('Importer').setEmoji('📥').setStyle(ButtonStyle.Secondary),
    new ButtonBuilder().setCustomId('config:global:reset_all').setLabel('Tout réinitialiser').setEmoji('🗑️').setStyle(ButtonStyle.Danger),
  );

  await interaction.editReply({ embeds: [embed], components: [selectRow, buttonRow] });
}

// ===================================
// Toggle Enable/Disable
// ===================================

async function handleToggle(interaction, guildId, moduleId) {
  await interaction.deferUpdate();

  const modules = await configService.getModules(guildId);
  const isCurrentlyEnabled = modules?.[moduleId] || false;
  const newState = !isCurrentlyEnabled;

  // Vérifier les dépendances avant d'activer
  if (newState) {
    const depCheck = moduleRegistry.checkDependencies(moduleId, modules || {});
    if (!depCheck.satisfied) {
      return interaction.followUp({
        content: `⚠️ Impossible d'activer **${moduleId}** — dépendances manquantes : ${depCheck.missing.map(d => `\`${d}\``).join(', ')}`,
        ephemeral: true,
      });
    }
  }

  // Vérifier qu'aucun autre module ne dépend de celui-ci avant de désactiver
  if (!newState) {
    const allModules = moduleRegistry.getAll();
    const dependents = allModules.filter(
      (m) => m.dependencies.includes(moduleId) && (modules?.[m.id] || false)
    );
    if (dependents.length > 0) {
      return interaction.followUp({
        content: `⚠️ Impossible de désactiver **${moduleId}** — requis par : ${dependents.map(d => `\`${d.id}\``).join(', ')}`,
        ephemeral: true,
      });
    }
  }

  await configService.setModule(guildId, moduleId, newState);

  // Rafraîchir le dashboard
  return showModuleDashboard(interaction, guildId, moduleId);
}

// ===================================
// Params Menu (select pour choisir quel param modifier)
// ===================================

async function handleParamsMenu(interaction, guildId, moduleId) {
  const manifest = moduleRegistry.get(moduleId);
  const config = await configService.get(guildId);
  const moduleConfig = configEngine.getModuleConfig(
    { modules: config?.modules || {} },
    moduleId
  );

  const schemaKeys = Object.keys(manifest.configSchema);
  if (schemaKeys.length === 0) {
    return interaction.reply({
      content: '💡 Ce module n\'a aucun paramètre configurable.',
      ephemeral: true,
    });
  }

  // Créer le select menu avec tous les params du module
  const options = schemaKeys.slice(0, 25).map((key) => {
    const schema = manifest.configSchema[key];
    const value = moduleConfig[key];
    const isMissing = schema.required && (value === null || value === undefined || value === '');

    let description = schema.description || schema.label;
    if (description.length > 100) description = description.substring(0, 97) + '...';

    return {
      label: `${isMissing ? '⚠️ ' : ''}${schema.label}`,
      description,
      value: key,
      emoji: schema.required ? '🔹' : '▫️',
    };
  });

  const selectRow = new ActionRowBuilder().addComponents(
    new StringSelectMenuBuilder()
      .setCustomId(`config:${moduleId}:param_set`)
      .setPlaceholder('📝 Sélectionner un paramètre à modifier...')
      .addOptions(options)
  );

  const backRow = new ActionRowBuilder().addComponents(
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:back_to_module`)
      .setLabel('Retour')
      .setEmoji('◀️')
      .setStyle(ButtonStyle.Secondary)
  );

  // Hack : back_to_module = refresh le dashboard module
  // On va vérifier dans le routing principal

  await interaction.update({
    embeds: [
      new EmbedBuilder()
        .setTitle(`⚙️ ${manifest.emoji} ${manifest.name} — Configuration`)
        .setDescription('Sélectionnez un paramètre à modifier.')
        .setColor(0x5865F2),
    ],
    components: [selectRow, backRow],
  });
}

// ===================================
// Param Select → Ouvre un modal pour saisir la valeur
// ===================================

async function handleParamSelect(interaction, guildId, moduleId) {
  const key = interaction.values[0];
  const manifest = moduleRegistry.get(moduleId);
  const schema = manifest.configSchema[key];

  if (!schema) {
    return interaction.reply({ content: '❌ Paramètre inconnu.', ephemeral: true });
  }

  // Pour les types spéciaux, on utilise un select menu au lieu d'un modal
  if (schema.type === 'boolean') {
    return handleBooleanSelect(interaction, guildId, moduleId, key, schema);
  }

  if (schema.type === 'enum') {
    return handleEnumSelect(interaction, guildId, moduleId, key, schema);
  }

  // Pour les autres types : ouvrir un modal
  const modal = new ModalBuilder()
    .setCustomId(`config:${moduleId}:param_modal:${key}`)
    .setTitle(`${manifest.emoji} ${schema.label}`);

  // Description du champ
  let placeholder = '';
  switch (schema.type) {
    case 'channel':
      placeholder = 'ID du salon (ex: 123456789012345678)';
      break;
    case 'role':
      placeholder = 'ID du rôle (ex: 123456789012345678)';
      break;
    case 'integer':
    case 'number':
      placeholder = `Nombre${schema.min !== undefined ? ` (min: ${schema.min}` : ''}${schema.max !== undefined ? `, max: ${schema.max})` : ')'}`;
      break;
    case 'json':
      placeholder = 'Données JSON valides';
      break;
    case 'channels':
    case 'roles':
      placeholder = 'IDs séparés par des virgules';
      break;
    default:
      placeholder = schema.description || 'Entrez une valeur';
  }

  const input = new TextInputBuilder()
    .setCustomId('value')
    .setLabel(schema.label)
    .setPlaceholder(placeholder.substring(0, 100))
    .setRequired(schema.required || false)
    .setStyle(schema.type === 'json' || (schema.maxLength && schema.maxLength > 100)
      ? TextInputStyle.Paragraph
      : TextInputStyle.Short
    );

  // Valeur actuelle comme défaut
  const config = await configService.get(guildId);
  const moduleConfig = configEngine.getModuleConfig({ modules: config?.modules || {} }, moduleId);
  const current = moduleConfig[key];
  if (current !== null && current !== undefined && current !== '') {
    const displayCurrent = typeof current === 'object' ? JSON.stringify(current) : String(current);
    if (displayCurrent.length <= 4000) {
      input.setValue(displayCurrent);
    }
  }

  modal.addComponents(new ActionRowBuilder().addComponents(input));
  await interaction.showModal(modal);
}

// ===================================
// Boolean Select (boutons Activé/Désactivé)
// ===================================

async function handleBooleanSelect(interaction, guildId, moduleId, key, schema) {
  const config = await configService.get(guildId);
  const moduleConfig = configEngine.getModuleConfig({ modules: config?.modules || {} }, moduleId);
  const current = moduleConfig[key];

  const row = new ActionRowBuilder().addComponents(
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:set_bool:${key}:true`)
      .setLabel('✅ Activé')
      .setStyle(current === true ? ButtonStyle.Success : ButtonStyle.Secondary),
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:set_bool:${key}:false`)
      .setLabel('❌ Désactivé')
      .setStyle(current === false ? ButtonStyle.Danger : ButtonStyle.Secondary),
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:params`)
      .setLabel('Retour')
      .setEmoji('◀️')
      .setStyle(ButtonStyle.Secondary)
  );

  await interaction.update({
    embeds: [
      new EmbedBuilder()
        .setTitle(`🔀 ${schema.label}`)
        .setDescription(schema.description || 'Choisissez une valeur.')
        .setColor(0x5865F2),
    ],
    components: [row],
  });
}

// ===================================
// Enum Select
// ===================================

async function handleEnumSelect(interaction, guildId, moduleId, key, schema) {
  const config = await configService.get(guildId);
  const moduleConfig = configEngine.getModuleConfig({ modules: config?.modules || {} }, moduleId);
  const current = moduleConfig[key];

  const options = schema.values.map((val) => ({
    label: val,
    value: `${key}:${val}`,
    default: current === val,
  }));

  const selectRow = new ActionRowBuilder().addComponents(
    new StringSelectMenuBuilder()
      .setCustomId(`config:${moduleId}:set_enum`)
      .setPlaceholder(`Choisir ${schema.label}`)
      .addOptions(options)
  );

  const backRow = new ActionRowBuilder().addComponents(
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:params`)
      .setLabel('Retour')
      .setEmoji('◀️')
      .setStyle(ButtonStyle.Secondary)
  );

  await interaction.update({
    embeds: [
      new EmbedBuilder()
        .setTitle(`📋 ${schema.label}`)
        .setDescription(schema.description || 'Sélectionnez une valeur.')
        .setColor(0x5865F2),
    ],
    components: [selectRow, backRow],
  });
}

// ===================================
// Modal Submit → Sauvegarder la valeur
// ===================================

async function handleParamModal(interaction, guildId, moduleId) {
  // CustomId format: config:<moduleId>:param_modal:<key>
  const key = interaction.customId.split(':')[3];
  if (!key) return;

  let value = interaction.fields.getTextInputValue('value')?.trim();
  const manifest = moduleRegistry.get(moduleId);
  const schema = manifest.configSchema[key];

  // Nettoyage
  if (!value || ['none', 'null', 'aucun', 'reset', ''].includes(value.toLowerCase())) {
    value = null;
  }

  // Conversion de type
  if (value !== null) {
    // Extraire les IDs des mentions Discord
    value = value.replace(/[<#@&!>]/g, '').trim();

    switch (schema.type) {
      case 'integer':
        value = parseInt(value, 10);
        if (isNaN(value)) {
          return interaction.reply({ content: '❌ Veuillez entrer un nombre entier.', ephemeral: true });
        }
        break;
      case 'number':
        value = parseFloat(value);
        if (isNaN(value)) {
          return interaction.reply({ content: '❌ Veuillez entrer un nombre.', ephemeral: true });
        }
        break;
      case 'json':
        try {
          value = JSON.parse(value);
        } catch {
          return interaction.reply({ content: '❌ JSON invalide.', ephemeral: true });
        }
        break;
      case 'channels':
      case 'roles':
        value = value.split(/[,\s]+/).filter(Boolean);
        break;
      case 'boolean':
        value = ['true', '1', 'oui', 'yes', 'on'].includes(value.toLowerCase());
        break;
    }
  }

  // Validation via le registry
  const validation = moduleRegistry.validateConfigValue(moduleId, key, value);
  if (!validation.valid) {
    return interaction.reply({ content: `❌ ${validation.error}`, ephemeral: true });
  }

  // Sauvegarder
  await saveModuleConfigValue(guildId, moduleId, key, value, interaction.user.id);

  await interaction.reply({
    content: `✅ **${schema.label}** mis à jour${value !== null ? ` → \`${typeof value === 'object' ? JSON.stringify(value) : value}\`` : ' → *Réinitialisé*'}`,
    ephemeral: true,
  });
}

// ===================================
// Reset Module Config (confirmation)
// ===================================

async function handleResetConfirm(interaction, guildId, moduleId) {
  const manifest = moduleRegistry.get(moduleId);

  const row = new ActionRowBuilder().addComponents(
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:confirm_reset`)
      .setLabel('Confirmer la réinitialisation')
      .setEmoji('⚠️')
      .setStyle(ButtonStyle.Danger),
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:cancel_reset`)
      .setLabel('Annuler')
      .setStyle(ButtonStyle.Secondary)
  );

  await interaction.update({
    embeds: [
      new EmbedBuilder()
        .setTitle(`🗑️ Réinitialiser ${manifest.emoji} ${manifest.name} ?`)
        .setDescription(
          'Cette action va :\n' +
          '• Effacer toute la configuration de ce module\n' +
          '• Remettre les valeurs par défaut\n' +
          '• **Ne désactive PAS** le module\n\n' +
          '⚠️ **Action irréversible.**'
        )
        .setColor(0xED4245),
    ],
    components: [row],
  });
}

async function handleResetExecute(interaction, guildId, moduleId) {
  await interaction.deferUpdate();

  // Reset la config du module
  const config = await configService.get(guildId);
  const guildConfig = { modules: config?.modules || {} };
  configEngine.resetModuleConfig(guildConfig, moduleId);

  // Sauvegarder
  await configService.set(guildId, { modules: guildConfig.modules });

  // Retour au dashboard module
  return showModuleDashboard(interaction, guildId, moduleId);
}

// ===================================
// Permissions
// ===================================

async function handlePermissions(interaction, guildId, moduleId) {
  const manifest = moduleRegistry.get(moduleId);

  const embed = new EmbedBuilder()
    .setTitle(`🔒 ${manifest.emoji} ${manifest.name} — Permissions`)
    .setDescription(
      'Les permissions permettent de restreindre l\'accès à ce module par :\n\n' +
      '🏷️ **Rôles** — Autoriser ou bloquer certains rôles\n' +
      '💬 **Salons** — Restreindre à certains salons\n' +
      '🔑 **Permissions Discord** — Exiger des permissions\n\n' +
      '*Configurez via `/config module:' + moduleId + '` et les paramètres de permissions.*'
    )
    .setColor(0x5865F2);

  // Afficher les permissions actuelles
  const config = await configService.get(guildId);
  const permRules = config?.permissions?.modules?.[moduleId];

  if (permRules) {
    const lines = [];
    if (permRules.allowedRoles?.length) {
      lines.push(`✅ Rôles autorisés : ${permRules.allowedRoles.map(r => `<@&${r}>`).join(', ')}`);
    }
    if (permRules.deniedRoles?.length) {
      lines.push(`❌ Rôles bloqués : ${permRules.deniedRoles.map(r => `<@&${r}>`).join(', ')}`);
    }
    if (permRules.allowedChannels?.length) {
      lines.push(`✅ Salons autorisés : ${permRules.allowedChannels.map(c => `<#${c}>`).join(', ')}`);
    }
    if (permRules.deniedChannels?.length) {
      lines.push(`❌ Salons bloqués : ${permRules.deniedChannels.map(c => `<#${c}>`).join(', ')}`);
    }

    if (lines.length > 0) {
      embed.addFields({ name: 'Règles actuelles', value: lines.join('\n'), inline: false });
    } else {
      embed.addFields({ name: 'Règles actuelles', value: '*Aucune restriction — accessible à tous.*', inline: false });
    }
  } else {
    embed.addFields({ name: 'Règles actuelles', value: '*Aucune restriction — accessible à tous.*', inline: false });
  }

  // Permissions bot requises
  if (manifest.requiredPermissions.length > 0) {
    embed.addFields({
      name: '🤖 Permissions bot requises',
      value: manifest.requiredPermissions.map(p => `\`${p}\``).join(', '),
      inline: false,
    });
  }

  const backRow = new ActionRowBuilder().addComponents(
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:params`)
      .setLabel('Configurer')
      .setEmoji('⚙️')
      .setStyle(ButtonStyle.Primary),
    new ButtonBuilder()
      .setCustomId(`config:back_to_global`)
      .setLabel('Retour')
      .setEmoji('◀️')
      .setStyle(ButtonStyle.Secondary)
  );

  await interaction.update({ embeds: [embed], components: [backRow] });
}

// ===================================
// Templates
// ===================================

async function handleTemplates(interaction, guildId, moduleId) {
  const manifest = moduleRegistry.get(moduleId);

  const embed = new EmbedBuilder()
    .setTitle(`💬 ${manifest.emoji} ${manifest.name} — Messages & Templates`)
    .setDescription(
      'Personnalisez les messages envoyés par ce module.\n\n' +
      '**Variables disponibles :**\n' +
      '`{user.mention}` `{user.tag}` `{user.name}` `{user.id}`\n' +
      '`{guild.name}` `{guild.memberCount}`\n' +
      '`{channel.name}` `{channel.mention}`\n' +
      '`{date}` `{time}` `{timestamp}`\n\n' +
      '*Les templates sont configurables dans les paramètres du module.*'
    )
    .setColor(0x5865F2);

  const backRow = new ActionRowBuilder().addComponents(
    new ButtonBuilder()
      .setCustomId(`config:${moduleId}:params`)
      .setLabel('Configurer')
      .setEmoji('⚙️')
      .setStyle(ButtonStyle.Primary),
    new ButtonBuilder()
      .setCustomId(`config:back_to_global`)
      .setLabel('Retour')
      .setEmoji('◀️')
      .setStyle(ButtonStyle.Secondary)
  );

  await interaction.update({ embeds: [embed], components: [backRow] });
}

// ===================================
// Export Module Config
// ===================================

async function handleModuleExport(interaction, guildId, moduleId) {
  const manifest = moduleRegistry.get(moduleId);
  const config = await configService.get(guildId);
  const modules = await configService.getModules(guildId);
  const moduleConfig = configEngine.getModuleConfig({ modules: config?.modules || {} }, moduleId);

  const exported = {
    module: moduleId,
    version: 2,
    exportedAt: new Date().toISOString(),
    enabled: modules?.[moduleId] || false,
    config: moduleConfig,
  };

  const jsonStr = JSON.stringify(exported, null, 2);

  await interaction.reply({
    content: `📤 **Export de ${manifest.emoji} ${manifest.name}** :\n\`\`\`json\n${jsonStr}\n\`\`\``,
    ephemeral: true,
  });
}

// ===================================
// Global Actions
// ===================================

async function handleGlobalAction(interaction, guildId, customId) {
  const action = customId.replace('config:global:', '');

  switch (action) {
    case 'export': {
      const config = await configService.get(guildId);
      const modules = await configService.getModules(guildId);
      const exported = configEngine.exportConfig(
        { modules: config?.modules || {}, global: { locale: config?.locale, prefix: config?.prefix } },
        modules
      );
      const jsonStr = JSON.stringify(exported, null, 2);

      // Si trop long pour un message, tronquer
      if (jsonStr.length > 1900) {
        await interaction.reply({
          content: `📤 **Export complet** (tronqué car > 2000 caractères):\n\`\`\`json\n${jsonStr.substring(0, 1800)}...\n\`\`\``,
          ephemeral: true,
        });
      } else {
        await interaction.reply({
          content: `📤 **Export complet** :\n\`\`\`json\n${jsonStr}\n\`\`\``,
          ephemeral: true,
        });
      }
      break;
    }

    case 'import': {
      await interaction.reply({
        content: '📥 Pour importer une config, utilisez le format JSON exporté.\n*Fonctionnalité d\'import via modal bientôt disponible.*',
        ephemeral: true,
      });
      break;
    }

    case 'reset_all': {
      const row = new ActionRowBuilder().addComponents(
        new ButtonBuilder()
          .setCustomId('config:global:confirm_reset')
          .setLabel('⚠️ Confirmer le reset total')
          .setStyle(ButtonStyle.Danger),
        new ButtonBuilder()
          .setCustomId('config:back_to_global')
          .setLabel('Annuler')
          .setStyle(ButtonStyle.Secondary)
      );

      await interaction.update({
        embeds: [
          new EmbedBuilder()
            .setTitle('🗑️ Réinitialisation totale')
            .setDescription(
              '**ATTENTION** — Cette action va :\n' +
              '• Effacer TOUTE la configuration\n' +
              '• Désactiver TOUS les modules\n' +
              '• Supprimer toutes les permissions\n' +
              '• Supprimer tous les templates\n\n' +
              '⚠️ **Action irréversible.**'
            )
            .setColor(0xED4245),
        ],
        components: [row],
      });
      break;
    }

    case 'confirm_reset': {
      await interaction.deferUpdate();
      await configService.reset(guildId);
      await configService.resetModules(guildId);
      return handleBackToGlobal(interaction, guildId);
    }
  }
}

// ===================================
// Helper : Sauvegarder une valeur de config module
// ===================================

async function saveModuleConfigValue(guildId, moduleId, key, value, userId) {
  const config = await configService.get(guildId);
  const guildConfig = { modules: config?.modules || {} };

  // Ancien value pour l'audit
  const oldValue = guildConfig.modules?.[moduleId]?.[key];

  // Appliquer via l'engine
  configEngine.setModuleConfigValue(guildConfig, moduleId, key, value);

  // Sauvegarder
  await configService.set(guildId, { modules: guildConfig.modules });

  // Log de changement (audit trail)
  try {
    const { getDb } = require('../../database');
    const db = getDb();
    if (db) {
      await db('config_history').insert({
        guild_id: guildId,
        module_id: moduleId,
        config_key: key,
        old_value: oldValue !== undefined ? String(oldValue) : null,
        new_value: value !== null && value !== undefined ? (typeof value === 'object' ? JSON.stringify(value) : String(value)) : null,
        changed_by: userId,
        action: 'SET',
      }).catch(() => {
        // Table n'existe peut-être pas encore, ne pas crasher
      });
    }
  } catch {
    // Silently ignore audit log errors
  }
}

// ===================================
// Helper : Set value (boolean/enum) et retour au param menu
// ===================================

async function handleSetValue(interaction, guildId, moduleId, key, value) {
  const manifest = moduleRegistry.get(moduleId);
  const schema = manifest?.configSchema?.[key];

  // Validation
  const validation = moduleRegistry.validateConfigValue(moduleId, key, value);
  if (!validation.valid) {
    return interaction.reply({ content: `❌ ${validation.error}`, ephemeral: true });
  }

  await saveModuleConfigValue(guildId, moduleId, key, value, interaction.user.id);

  // Retour au menu des paramètres
  await interaction.deferUpdate();
  return handleParamsMenu(
    { ...interaction, update: interaction.editReply.bind(interaction) },
    guildId,
    moduleId
  );
}
