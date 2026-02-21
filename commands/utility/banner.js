// ===================================
// Ultra Suite — /banner
// Afficher la bannière (user/serveur)
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

module.exports = {
  module: 'utility',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('banner')
    .setDescription('Afficher une bannière')
    .addSubcommand((s) =>
      s.setName('user').setDescription('Bannière d\'un utilisateur')
        .addUserOption((o) => o.setName('utilisateur').setDescription('L\'utilisateur')),
    )
    .addSubcommand((s) =>
      s.setName('server').setDescription('Bannière du serveur'),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();

    if (sub === 'user') {
      const user = interaction.options.getUser('utilisateur') || interaction.user;
      const fetched = await user.fetch(true);

      if (!fetched.banner) {
        return interaction.reply({ content: '❌ Cet utilisateur n\'a pas de bannière.', ephemeral: true });
      }

      const url = fetched.bannerURL({ size: 4096, dynamic: true });
      const embed = new EmbedBuilder()
        .setTitle(`🖼️ Bannière de ${user.tag}`)
        .setColor(fetched.accentColor || 0x3498DB)
        .setImage(url)
        .setDescription(`[Lien direct](${url})`);

      return interaction.reply({ embeds: [embed] });
    }

    if (sub === 'server') {
      const guild = interaction.guild;
      const bannerUrl = guild.bannerURL({ size: 4096 });

      if (!bannerUrl) {
        return interaction.reply({ content: '❌ Ce serveur n\'a pas de bannière.', ephemeral: true });
      }

      const embed = new EmbedBuilder()
        .setTitle(`🖼️ Bannière de ${guild.name}`)
        .setColor(0x3498DB)
        .setImage(bannerUrl)
        .setDescription(`[Lien direct](${bannerUrl})`);

      return interaction.reply({ embeds: [embed] });
    }
  },
};
