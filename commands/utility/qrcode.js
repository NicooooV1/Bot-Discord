// ===================================
// Ultra Suite — /qrcode
// Générateur QR Code
// ===================================

const { SlashCommandBuilder, EmbedBuilder, AttachmentBuilder } = require('discord.js');

module.exports = {
  module: 'utility',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('qrcode')
    .setDescription('Générer un QR Code')
    .addStringOption((o) => o.setName('texte').setDescription('Texte ou URL à encoder').setRequired(true))
    .addStringOption((o) => o.setName('couleur').setDescription('Couleur du code (hex, défaut: #000000)'))
    .addStringOption((o) => o.setName('fond').setDescription('Couleur de fond (hex, défaut: #FFFFFF)')),

  async execute(interaction) {
    const text = interaction.options.getString('texte');
    const color = interaction.options.getString('couleur') || '#000000';
    const bgColor = interaction.options.getString('fond') || '#FFFFFF';

    try {
      const QRCode = require('qrcode');
      const buffer = await QRCode.toBuffer(text, {
        width: 512,
        margin: 2,
        color: { dark: color, light: bgColor },
      });

      const attachment = new AttachmentBuilder(buffer, { name: 'qrcode.png' });
      const embed = new EmbedBuilder()
        .setTitle('📱 QR Code')
        .setColor(0x3498DB)
        .setDescription(`Contenu : \`${text.substring(0, 100)}\``)
        .setImage('attachment://qrcode.png')
        .setTimestamp();

      return interaction.reply({ embeds: [embed], files: [attachment] });
    } catch (e) {
      return interaction.reply({ content: `❌ Erreur : ${e.message}`, ephemeral: true });
    }
  },
};
