module.exports = {
  name: 'interactionCreate',
  async execute(interaction) {
    // ===================================
    // Slash Commands
    // ===================================
    if (interaction.isChatInputCommand()) {
      const command = interaction.client.commands.get(interaction.commandName);
      if (!command) return;

      try {
        await command.execute(interaction);
      } catch (error) {
        console.error(`[CMD ERROR] ${interaction.commandName}:`, error);
        const reply = { content: '❌ Une erreur est survenue lors de l\'exécution de cette commande.', ephemeral: true };
        if (interaction.replied || interaction.deferred) {
          await interaction.followUp(reply);
        } else {
          await interaction.reply(reply);
        }
      }
      return;
    }

    // ===================================
    // Boutons
    // ===================================
    if (interaction.isButton()) {
      // Tickets
      if (interaction.customId.startsWith('ticket_')) {
        const ticketCommand = interaction.client.commands.get('ticket');
        if (ticketCommand?.handleButton) {
          try {
            await ticketCommand.handleButton(interaction);
          } catch (error) {
            console.error('[BUTTON ERROR] ticket:', error);
            if (!interaction.replied && !interaction.deferred) {
              await interaction.reply({ content: '❌ Une erreur est survenue.', ephemeral: true });
            }
          }
        }
      }
      return;
    }

    // ===================================
    // Menus déroulants (Select Menus)
    // ===================================
    if (interaction.isStringSelectMenu()) {
      if (interaction.customId === 'ticket_category_select') {
        const ticketCommand = interaction.client.commands.get('ticket');
        if (ticketCommand?.handleSelectMenu) {
          try {
            await ticketCommand.handleSelectMenu(interaction);
          } catch (error) {
            console.error('[SELECT ERROR] ticket:', error);
            if (!interaction.replied && !interaction.deferred) {
              await interaction.reply({ content: '❌ Une erreur est survenue.', ephemeral: true });
            }
          }
        }
      }
      return;
    }
  },
};
