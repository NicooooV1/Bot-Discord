// ===================================
// Ultra Suite — /embed
// Créer et envoyer un embed personnalisé
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ChannelType } = require('discord.js');

module.exports = {
  module: 'utility',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('embed')
    .setDescription('Créer et envoyer un embed personnalisé')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageMessages)
    .addStringOption((opt) => opt.setName('titre').setDescription('Titre de l\'embed'))
    .addStringOption((opt) => opt.setName('description').setDescription('Description (supporte le markdown)').setRequired(true))
    .addStringOption((opt) => opt.setName('couleur').setDescription('Couleur hex (ex: #FF5733)'))
    .addStringOption((opt) => opt.setName('image').setDescription('URL de l\'image'))
    .addStringOption((opt) => opt.setName('thumbnail').setDescription('URL du thumbnail'))
    .addStringOption((opt) => opt.setName('footer').setDescription('Texte du footer'))
    .addStringOption((opt) => opt.setName('url').setDescription('URL du titre'))
    .addChannelOption((opt) => opt.setName('channel').setDescription('Channel cible').addChannelTypes(ChannelType.GuildText)),

  async execute(interaction) {
    const titre = interaction.options.getString('titre');
    const description = interaction.options.getString('description');
    const couleur = interaction.options.getString('couleur');
    const image = interaction.options.getString('image');
    const thumbnail = interaction.options.getString('thumbnail');
    const footer = interaction.options.getString('footer');
    const url = interaction.options.getString('url');
    const channel = interaction.options.getChannel('channel') || interaction.channel;

    // Parser la couleur
    let color = 0x5865F2;
    if (couleur) {
      const parsed = parseInt(couleur.replace('#', ''), 16);
      if (!isNaN(parsed) && parsed >= 0 && parsed <= 0xFFFFFF) color = parsed;
    }

    const embed = new EmbedBuilder()
      .setDescription(description)
      .setColor(color)
      .setTimestamp();

    if (titre) embed.setTitle(titre);
    if (url) embed.setURL(url);
    if (image) embed.setImage(image);
    if (thumbnail) embed.setThumbnail(thumbnail);
    if (footer) embed.setFooter({ text: footer });

    try {
      await channel.send({ embeds: [embed] });

      if (channel.id === interaction.channel.id) {
        return interaction.reply({ content: '✅ Embed envoyé.', ephemeral: true });
      }
      return interaction.reply({ content: `✅ Embed envoyé dans ${channel}.`, ephemeral: true });
    } catch (err) {
      return interaction.reply({ content: `❌ Impossible d'envoyer dans ${channel}.`, ephemeral: true });
    }
  },
};