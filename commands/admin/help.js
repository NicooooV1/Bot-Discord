const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { COLORS } = require('../../utils/logger');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('help')
    .setDescription('❓ Afficher la liste des commandes disponibles')
    .addStringOption(opt =>
      opt.setName('catégorie')
        .setDescription('Filtrer par catégorie')
        .addChoices(
          { name: '🔨 Modération', value: 'moderation' },
          { name: '🎫 Tickets', value: 'tickets' },
          { name: '⚙️ Administration', value: 'admin' },
        )
    ),

  async execute(interaction) {
    const category = interaction.options.getString('catégorie');

    const categories = {
      moderation: {
        emoji: '🔨',
        title: 'Modération',
        commands: [
          { name: '/ban', desc: 'Bannir un utilisateur' },
          { name: '/unban', desc: 'Débannir un utilisateur' },
          { name: '/kick', desc: 'Expulser un utilisateur' },
          { name: '/softban', desc: 'Softban (expulser + supprimer messages)' },
          { name: '/mute', desc: 'Rendre muet un utilisateur (timeout)' },
          { name: '/unmute', desc: 'Retirer le mute' },
          { name: '/warn', desc: 'Avertir un utilisateur' },
          { name: '/warnings list', desc: 'Voir les avertissements' },
          { name: '/warnings remove', desc: 'Retirer un avertissement' },
          { name: '/warnings clear', desc: 'Supprimer tous les avertissements' },
          { name: '/clear', desc: 'Supprimer des messages' },
          { name: '/lock on/off', desc: 'Verrouiller/Déverrouiller un salon' },
          { name: '/slowmode', desc: 'Définir le mode lent' },
          { name: '/nick', desc: 'Modifier un surnom' },
          { name: '/userinfo', desc: 'Infos et historique d\'un utilisateur' },
          { name: '/modlogs', desc: 'Historique de modération' },
          { name: '/banlist', desc: 'Liste des utilisateurs bannis' },
        ],
      },
      tickets: {
        emoji: '🎫',
        title: 'Tickets',
        commands: [
          { name: '/ticket panel', desc: 'Créer un panneau de tickets' },
          { name: '/ticket close', desc: 'Fermer le ticket actuel' },
          { name: '/ticket add', desc: 'Ajouter un utilisateur au ticket' },
          { name: '/ticket remove', desc: 'Retirer un utilisateur du ticket' },
        ],
      },
      admin: {
        emoji: '⚙️',
        title: 'Administration',
        commands: [
          { name: '/setup logs', desc: 'Définir le salon de logs' },
          { name: '/setup welcome', desc: 'Définir le salon de bienvenue' },
          { name: '/setup welcome-message', desc: 'Message de bienvenue personnalisé' },
          { name: '/setup leave-message', desc: 'Message de départ personnalisé' },
          { name: '/setup ticket-category', desc: 'Catégorie des tickets' },
          { name: '/setup ticket-logs', desc: 'Salon de logs des tickets' },
          { name: '/setup mod-role', desc: 'Rôle modérateur' },
          { name: '/setup antispam', desc: 'Activer/Désactiver l\'anti-spam' },
          { name: '/setup view', desc: 'Voir la configuration' },
          { name: '/serverinfo', desc: 'Informations du serveur' },
          { name: '/help', desc: 'Cette commande' },
        ],
      },
    };

    if (category && categories[category]) {
      const cat = categories[category];
      const embed = new EmbedBuilder()
        .setTitle(`${cat.emoji} ${cat.title}`)
        .setColor(COLORS.BLUE)
        .setDescription(
          cat.commands.map(c => `\`${c.name}\` — ${c.desc}`).join('\n')
        )
        .setTimestamp();

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    // Afficher toutes les catégories
    const embed = new EmbedBuilder()
      .setTitle('❓ Aide — Liste des commandes')
      .setColor(COLORS.BLUE)
      .setDescription('Utilisez `/help catégorie` pour voir les commandes d\'une catégorie spécifique.')
      .setTimestamp();

    for (const [key, cat] of Object.entries(categories)) {
      embed.addFields({
        name: `${cat.emoji} ${cat.title}`,
        value: cat.commands.map(c => `\`${c.name}\``).join(', '),
      });
    }

    embed.addFields({
      name: '📋 Logs automatiques',
      value: 'Messages supprimés/modifiés, arrivées/départs, changements de rôles, surnoms, vocaux, salons créés/supprimés, timeouts',
    });

    await interaction.reply({ embeds: [embed], ephemeral: true });
  },
};
