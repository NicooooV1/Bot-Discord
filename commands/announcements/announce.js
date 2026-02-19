// ===================================
// Ultra Suite — Announcements: /announce
// ===================================

const {
  SlashCommandBuilder,
  PermissionFlagsBits,
} = require('discord.js');
const { createEmbed, successEmbed, errorEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'announcements',
  data: new SlashCommandBuilder()
    .setName('announce')
    .setDescription('Envoie une annonce')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageMessages)
    .addStringOption((opt) => opt.setName('titre').setDescription('Titre').setRequired(true))
    .addStringOption((opt) => opt.setName('message').setDescription('Contenu').setRequired(true))
    .addChannelOption((opt) => opt.setName('salon').setDescription('Salon cible'))
    .addStringOption((opt) =>
      opt
        .setName('couleur')
        .setDescription('Couleur')
        .addChoices(
          { name: '🔵 Bleu', value: '#5865F2' },
          { name: '🟢 Vert', value: '#57F287' },
          { name: '🟡 Jaune', value: '#FEE75C' },
          { name: '🔴 Rouge', value: '#ED4245' },
          { name: '⚪ Blanc', value: '#FFFFFF' }
        )
    )
    .addBooleanOption((opt) => opt.setName('mention').setDescription('Mentionner @everyone ?'))
    .addStringOption((opt) => opt.setName('image').setDescription('URL d\'une image'))
    .addStringOption((opt) => opt.setName('thumbnail').setDescription('URL d\'une miniature')),

  async execute(interaction) {
    const title = interaction.options.getString('titre');
    const message = interaction.options.getString('message');
    const channel = interaction.options.getChannel('salon') || interaction.channel;
    const color = interaction.options.getString('couleur') || '#5865F2';
    const mention = interaction.options.getBoolean('mention') || false;
    const image = interaction.options.getString('image');
    const thumbnail = interaction.options.getString('thumbnail');

    const embed = createEmbed('primary')
      .setColor(color)
      .setTitle(title)
      .setDescription(message)
      .setTimestamp()
      .setFooter({ text: interaction.guild.name, iconURL: interaction.guild.iconURL() });

    if (image) embed.setImage(image);
    if (thumbnail) embed.setThumbnail(thumbnail);

    try {
      await channel.send({
        content: mention ? '@everyone' : undefined,
        embeds: [embed],
      });
      return interaction.reply({ embeds: [successEmbed(`✅ Annonce envoyée dans ${channel}`)], ephemeral: true });
    } catch {
      return interaction.reply({ embeds: [errorEmbed('❌ Impossible d\'envoyer dans ce salon.')], ephemeral: true });
    }
  },
};
