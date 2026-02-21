// ===================================
// Ultra Suite — /timer
// Minuteur / Chronomètre
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'utility',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('timer')
    .setDescription('Créer un minuteur ou chronomètre')
    .addSubcommand((s) =>
      s.setName('set').setDescription('Définir un minuteur')
        .addStringOption((o) => o.setName('duree').setDescription('Durée (ex: 5m, 1h30m, 2h)').setRequired(true))
        .addStringOption((o) => o.setName('message').setDescription('Message à afficher à la fin')),
    )
    .addSubcommand((s) =>
      s.setName('countdown').setDescription('Créer un compte à rebours')
        .addStringOption((o) => o.setName('duree').setDescription('Durée (ex: 10s, 30s)').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('stopwatch').setDescription('Démarrer un chronomètre'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();

    const parseDuration = (str) => {
      let total = 0;
      const h = str.match(/(\d+)h/); if (h) total += parseInt(h[1]) * 3600000;
      const m = str.match(/(\d+)m/); if (m) total += parseInt(m[1]) * 60000;
      const s = str.match(/(\d+)s/); if (s) total += parseInt(s[1]) * 1000;
      return total || parseInt(str) * 1000;
    };

    switch (sub) {
      case 'set': {
        const durationStr = interaction.options.getString('duree');
        const message = interaction.options.getString('message') || '⏰ Le minuteur est terminé !';
        const ms = parseDuration(durationStr);

        if (!ms || ms < 1000 || ms > 86400000) {
          return interaction.reply({ content: '❌ Durée invalide (1s à 24h).', ephemeral: true });
        }

        const endTime = Math.floor((Date.now() + ms) / 1000);

        const embed = new EmbedBuilder()
          .setTitle('⏱️ Minuteur défini')
          .setColor(0x3498DB)
          .setDescription(`Le minuteur se termine <t:${endTime}:R> (<t:${endTime}:T>)\n\n📝 Message : ${message}`)
          .setTimestamp();

        await interaction.reply({ embeds: [embed] });

        // Use reminder table for persistence
        try {
          const db = getDb();
          await db('reminders').insert({
            guild_id: interaction.guildId,
            user_id: interaction.user.id,
            channel_id: interaction.channelId,
            message: `⏰ **Minuteur terminé !** ${message}`,
            remind_at: new Date(Date.now() + ms),
          });
        } catch (e) {
          // Fallback to setTimeout if DB not available
          setTimeout(async () => {
            try {
              await interaction.channel.send(`${interaction.user} ⏰ **Minuteur terminé !** ${message}`);
            } catch (e) { /* channel inaccessible */ }
          }, ms);
        }
        break;
      }

      case 'countdown': {
        const durationStr = interaction.options.getString('duree');
        const ms = parseDuration(durationStr);

        if (!ms || ms < 1000 || ms > 60000) {
          return interaction.reply({ content: '❌ Durée invalide (1s à 60s).', ephemeral: true });
        }

        const seconds = Math.floor(ms / 1000);
        const msg = await interaction.reply({ content: `⏳ **${seconds}**`, fetchReply: true });

        const interval = setInterval(async () => {
          const remaining = Math.max(0, Math.floor((ms - (Date.now() - msg.createdTimestamp)) / 1000));
          if (remaining <= 0) {
            clearInterval(interval);
            await msg.edit('🎉 **GO !**').catch(() => {});
          } else {
            await msg.edit(`⏳ **${remaining}**`).catch(() => clearInterval(interval));
          }
        }, 1000);
        break;
      }

      case 'stopwatch': {
        const start = Date.now();

        const embed = new EmbedBuilder()
          .setTitle('⏱️ Chronomètre')
          .setColor(0x2ECC71)
          .setDescription('🟢 En cours...\nUtilisez `/timer set` pour un minuteur avec alerte.')
          .addFields({ name: 'Démarré', value: `<t:${Math.floor(start / 1000)}:T>` })
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }
    }
  },
};
