// ===================================
// Ultra Suite — Admin: /help
// Aide interactive avec catégories
// ===================================

const {
  SlashCommandBuilder,
  StringSelectMenuBuilder,
  ActionRowBuilder,
  ComponentType,
} = require('discord.js');
const { createEmbed } = require('../../utils/embeds');
const { t } = require('../../core/i18n');

module.exports = {
  module: 'admin',
  data: new SlashCommandBuilder()
    .setName('help')
    .setDescription('Affiche l\'aide du bot')
    .addStringOption((opt) =>
      opt
        .setName('commande')
        .setDescription('Nom d\'une commande spécifique')
        .setRequired(false)
    ),

  async execute(interaction, client) {
    const specificCmd = interaction.options.getString('commande');

    // === Aide pour une commande spécifique ===
    if (specificCmd) {
      const cmd = client.commands.get(specificCmd);
      if (!cmd) {
        return interaction.reply({ content: `❌ Commande \`/${specificCmd}\` introuvable.`, ephemeral: true });
      }

      const embed = createEmbed('info')
        .setTitle(`📖 /${cmd.data.name}`)
        .setDescription(cmd.data.description || 'Aucune description')
        .addFields(
          { name: 'Module', value: cmd.module || 'N/A', inline: true },
          { name: 'Cooldown', value: cmd.cooldown ? `${cmd.cooldown}s` : 'Aucun', inline: true }
        );

      // Sous-commandes
      const subs = cmd.data.options?.filter((o) => o.toJSON?.().type === 1); // SUB_COMMAND
      if (subs?.length) {
        embed.addFields({
          name: 'Sous-commandes',
          value: subs.map((s) => {
            const json = s.toJSON();
            return `\`/${cmd.data.name} ${json.name}\` — ${json.description}`;
          }).join('\n'),
        });
      }

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }

    // === Menu d'aide par catégories ===
    const categories = new Map();
    client.commands.forEach((cmd) => {
      const mod = cmd.module || 'other';
      if (!categories.has(mod)) categories.set(mod, []);
      categories.get(mod).push(cmd);
    });

    const categoryLabels = {
      admin: '⚙️ Administration',
      moderation: '🔨 Modération',
      logs: '📋 Logs',
      security: '🛡️ Sécurité',
      onboarding: '👋 Onboarding',
      roles: '🏷️ Rôles',
      tickets: '🎫 Tickets',
      xp: '📊 XP & Niveaux',
      economy: '💰 Économie',
      utility: '🔧 Utilitaire',
      fun: '🎮 Fun',
      music: '🎵 Musique',
      tempvoice: '🔊 Vocal Temp',
      applications: '📝 Candidatures',
      tags: '🏷️ Tags',
      events: '📅 Événements',
      rp: '🎭 RP',
      other: '📦 Autres',
    };

    // Embed d'accueil
    const homeEmbed = createEmbed('info')
      .setTitle(t('admin.help.title'))
      .setDescription(t('admin.help.description'))
      .setThumbnail(client.user.displayAvatarURL())
      .addFields(
        [...categories.entries()].map(([mod, cmds]) => ({
          name: categoryLabels[mod] || mod,
          value: `${cmds.length} commande(s)`,
          inline: true,
        }))
      );

    // Select menu
    const select = new StringSelectMenuBuilder()
      .setCustomId('help_category')
      .setPlaceholder('Choisir une catégorie...')
      .addOptions(
        [...categories.entries()].map(([mod, cmds]) => ({
          label: (categoryLabels[mod] || mod).replace(/[^\w\s]/g, '').trim(),
          value: mod,
          description: `${cmds.length} commande(s)`,
          emoji: (categoryLabels[mod] || '📦').match(/[\p{Emoji_Presentation}]/u)?.[0] || '📦',
        }))
      );

    const row = new ActionRowBuilder().addComponents(select);
    const reply = await interaction.reply({ embeds: [homeEmbed], components: [row], ephemeral: true });

    // Collector pour le select
    const collector = reply.createMessageComponentCollector({
      componentType: ComponentType.StringSelect,
      time: 120_000,
    });

    collector.on('collect', async (i) => {
      const mod = i.values[0];
      const cmds = categories.get(mod) || [];

      const embed = createEmbed('info')
        .setTitle(categoryLabels[mod] || mod)
        .setDescription(
          cmds
            .map((c) => `\`/${c.data.name}\` — ${c.data.description || 'N/A'}`)
            .join('\n') || 'Aucune commande.'
        );

      await i.update({ embeds: [embed], components: [row] });
    });

    collector.on('end', () => {
      interaction.editReply({ components: [] }).catch(() => {});
    });
  },
};
