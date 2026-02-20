// ===================================
// Ultra Suite — Event: guildDelete
// Déclenché quand le bot quitte un serveur
// (retiré par un admin, serveur supprimé, etc.)
//
// Actions :
// 1. Invalider le cache de cette guild
// 2. Logger la sortie
// 3. Optionnel : supprimer les données de la guild
//    (configurable via PURGE_ON_LEAVE dans .env)
// ===================================

const guildQueries = require('../database/guildQueries');
const configService = require('../core/configService');
const { createModuleLogger } = require('../core/logger');

const log = createModuleLogger('GuildDelete');

module.exports = {
  name: 'guildDelete',
  once: false,

  /**
   * @param {import('discord.js').Guild} guild
   */
  async execute(guild) {
    log.info(
      `Bot retiré du serveur : ${guild.name || 'Inconnu'} (${guild.id})`
    );

    // 1. Invalider le cache immédiatement
    // (plus besoin de garder les données en mémoire)
    configService.invalidate(guild.id);

    // 2. Supprimer les données si PURGE_ON_LEAVE est activé
    const purgeOnLeave = process.env.PURGE_ON_LEAVE === 'true';

    if (purgeOnLeave) {
      try {
        const deleted = await guildQueries.delete(guild.id);
        if (deleted) {
          log.info(
            `Données de la guild ${guild.id} supprimées (PURGE_ON_LEAVE=true). ` +
            'Les tables avec ON DELETE CASCADE ont été nettoyées.'
          );
        }
      } catch (err) {
        log.error(`Erreur suppression données guild ${guild.id}: ${err.message}`);
      }
    } else {
      log.info(
        `Données de la guild ${guild.id} conservées en DB ` +
        '(PURGE_ON_LEAVE=false). Elles seront réutilisées si le bot est ré-ajouté.'
      );
    }

    log.info(
      `Bot opérationnel sur ${guild.client.guilds.cache.size} serveur(s) au total`
    );
  },
};