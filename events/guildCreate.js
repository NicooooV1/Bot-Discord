// ===================================
// Ultra Suite — Event: guildCreate
// Déclenché quand le bot rejoint un nouveau serveur
//
// Actions :
// 1. Créer/mettre à jour la guild en DB
// 2. Logger l'ajout
// 3. Optionnel : envoyer un message de bienvenue au owner
// ===================================

const guildQueries = require('../database/guildQueries');
const configService = require('../core/configService');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('GuildCreate');

module.exports = {
  name: 'guildCreate',
  once: false,

  /**
   * @param {import('discord.js').Guild} guild
   */
  async execute(guild) {
    log.info(
      `Bot ajouté au serveur : ${guild.name} (${guild.id}) — ` +
      `${guild.memberCount} membres, owner: ${guild.ownerId}`
    );

    // 1. Créer ou mettre à jour la guild en DB
    try {
      await guildQueries.getOrCreate(guild.id, guild.name, guild.ownerId);
      log.info(`Guild ${guild.name} initialisée en DB`);
    } catch (err) {
      log.error(`Erreur init DB pour guild ${guild.name} (${guild.id}): ${err.message}`);
    }

    // 2. Pré-charger le cache config
    try {
      await configService.get(guild.id);
    } catch {
      // Non bloquant
    }

    // 3. Envoyer un message de bienvenue au owner (optionnel)
    try {
      const owner = await guild.fetchOwner().catch(() => null);
      if (owner) {
        await owner.send({
          embeds: [{
            title: '👋 Merci d\'avoir ajouté Ultra Suite !',
            description:
              'Voici comment démarrer :\n\n' +
              '**1.** Utilisez `/module list` pour voir les modules disponibles\n' +
              '**2.** Activez les modules souhaités avec `/module enable <nom>`\n' +
              '**3.** Configurez chaque module avec `/config`\n\n' +
              '📖 Pour de l\'aide : `/help`\n' +
              '💡 Commencez par activer `moderation` et `logs` !',
            color: 0x5865F2,
            footer: { text: `Serveur : ${guild.name}` },
            timestamp: new Date().toISOString(),
          }],
        }).catch(() => {
          // Le owner a peut-être les DMs fermés — ce n'est pas grave
          log.debug(`Impossible d'envoyer le DM de bienvenue à l'owner de ${guild.name}`);
        });
      }
    } catch {
      // Non bloquant
    }

    log.info(
      `Bot opérationnel sur ${guild.client.guilds.cache.size} serveur(s) au total`
    );
  },
};