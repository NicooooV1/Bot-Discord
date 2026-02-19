// ===================================
// Ultra Suite — Event: interactionCreate
// Route slash commands, buttons, selects, modals
// ===================================

const { createModuleLogger } = require('../../core/logger');
const configService = require('../../core/configService');
const { errorEmbed } = require('../../utils/embeds');
const { t, createTranslator } = require('../../core/i18n');

const log = createModuleLogger('Interaction');

// Cooldowns Map (userId -> commandName -> timestamp)
const cooldowns = new Map();

module.exports = {
  name: 'interactionCreate',

  async execute(interaction, client) {
    // ===================================
    // SLASH COMMANDS
    // ===================================
    if (interaction.isChatInputCommand()) {
      const command = client.commands.get(interaction.commandName);
      if (!command) return;

      try {
        // Vérifier si le module est activé (sauf admin)
        if (command.module && command.module !== 'admin' && interaction.guild) {
          const enabled = await configService.isModuleEnabled(interaction.guild.id, command.module);
          if (!enabled) {
            return interaction.reply({
              embeds: [errorEmbed(t('common.module_disabled'))],
              ephemeral: true,
            });
          }
        }

        // Vérifier le cooldown
        if (command.cooldown) {
          const key = `${interaction.user.id}:${command.data.name}`;
          const now = Date.now();
          const expiry = cooldowns.get(key);

          if (expiry && now < expiry) {
            const remaining = Math.ceil((expiry - now) / 1000);
            return interaction.reply({
              embeds: [errorEmbed(t('common.cooldown', undefined, { remaining }))],
              ephemeral: true,
            });
          }

          cooldowns.set(key, now + command.cooldown * 1000);
          setTimeout(() => cooldowns.delete(key), command.cooldown * 1000);
        }

        // Exécuter la commande
        await command.execute(interaction, client);
      } catch (err) {
        log.error(`Error executing /${interaction.commandName}:`, err);
        const reply = { embeds: [errorEmbed(t('common.error'))], ephemeral: true };
        if (interaction.replied || interaction.deferred) {
          await interaction.followUp(reply).catch(() => {});
        } else {
          await interaction.reply(reply).catch(() => {});
        }
      }

      return;
    }

    // ===================================
    // AUTOCOMPLETE
    // ===================================
    if (interaction.isAutocomplete()) {
      const command = client.commands.get(interaction.commandName);
      if (!command?.autocomplete) return;

      try {
        await command.autocomplete(interaction, client);
      } catch (err) {
        log.error(`Autocomplete error for /${interaction.commandName}:`, err);
      }

      return;
    }

    // ===================================
    // BUTTONS
    // ===================================
    if (interaction.isButton()) {
      // Format customId : "module_action" ou "module_action_extra"
      const [prefix] = interaction.customId.split('_');

      // Chercher un handler exact d'abord, puis par préfixe
      const handler =
        client.buttons?.get(interaction.customId) ||
        client.buttons?.find((b) => interaction.customId.startsWith(b.id));

      if (handler) {
        try {
          await handler.execute(interaction, client);
        } catch (err) {
          log.error(`Button error (${interaction.customId}):`, err);
          const reply = { embeds: [errorEmbed(t('common.error'))], ephemeral: true };
          if (!interaction.replied && !interaction.deferred) {
            await interaction.reply(reply).catch(() => {});
          }
        }
      }
      return;
    }

    // ===================================
    // SELECT MENUS
    // ===================================
    if (interaction.isAnySelectMenu()) {
      const handler =
        client.selects?.get(interaction.customId) ||
        client.selects?.find((s) => interaction.customId.startsWith(s.id));

      if (handler) {
        try {
          await handler.execute(interaction, client);
        } catch (err) {
          log.error(`Select error (${interaction.customId}):`, err);
          const reply = { embeds: [errorEmbed(t('common.error'))], ephemeral: true };
          if (!interaction.replied && !interaction.deferred) {
            await interaction.reply(reply).catch(() => {});
          }
        }
      }
      return;
    }

    // ===================================
    // MODALS
    // ===================================
    if (interaction.isModalSubmit()) {
      const handler =
        client.modals?.get(interaction.customId) ||
        client.modals?.find((m) => interaction.customId.startsWith(m.id));

      if (handler) {
        try {
          await handler.execute(interaction, client);
        } catch (err) {
          log.error(`Modal error (${interaction.customId}):`, err);
          const reply = { embeds: [errorEmbed(t('common.error'))], ephemeral: true };
          if (!interaction.replied && !interaction.deferred) {
            await interaction.reply(reply).catch(() => {});
          }
        }
      }
      return;
    }
  },
};
