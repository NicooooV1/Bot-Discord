// ===================================
// Ultra Suite — /color
// Info couleur + aperçu
// ===================================

const { SlashCommandBuilder, EmbedBuilder, AttachmentBuilder } = require('discord.js');

module.exports = {
  module: 'utility',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('color')
    .setDescription('Afficher des informations sur une couleur')
    .addStringOption((o) => o.setName('couleur').setDescription('Code hex (#FF0000), RGB (255,0,0), ou nom (red)').setRequired(true)),

  async execute(interaction) {
    const input = interaction.options.getString('couleur');

    let r, g, b;
    const hexMatch = input.match(/^#?([0-9a-f]{6})$/i);
    const rgbMatch = input.match(/^(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})$/);
    const namedColors = { red: [255, 0, 0], green: [0, 128, 0], blue: [0, 0, 255], white: [255, 255, 255], black: [0, 0, 0], yellow: [255, 255, 0], cyan: [0, 255, 255], magenta: [255, 0, 255], orange: [255, 165, 0], purple: [128, 0, 128], pink: [255, 192, 203] };

    if (hexMatch) {
      const hex = hexMatch[1];
      r = parseInt(hex.substring(0, 2), 16);
      g = parseInt(hex.substring(2, 4), 16);
      b = parseInt(hex.substring(4, 6), 16);
    } else if (rgbMatch) {
      r = Math.min(255, parseInt(rgbMatch[1]));
      g = Math.min(255, parseInt(rgbMatch[2]));
      b = Math.min(255, parseInt(rgbMatch[3]));
    } else if (namedColors[input.toLowerCase()]) {
      [r, g, b] = namedColors[input.toLowerCase()];
    } else {
      return interaction.reply({ content: '❌ Couleur non reconnue. Utilisez #HEX, R,G,B ou un nom.', ephemeral: true });
    }

    const hex = `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`.toUpperCase();
    const decimal = (r << 16) + (g << 8) + b;

    // HSL conversion
    const rn = r / 255, gn = g / 255, bn = b / 255;
    const max = Math.max(rn, gn, bn), min = Math.min(rn, gn, bn);
    let h, s, l = (max + min) / 2;
    if (max === min) { h = s = 0; } else {
      const d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      switch (max) {
        case rn: h = ((gn - bn) / d + (gn < bn ? 6 : 0)) / 6; break;
        case gn: h = ((bn - rn) / d + 2) / 6; break;
        case bn: h = ((rn - gn) / d + 4) / 6; break;
      }
    }

    const files = [];
    try {
      const { createCanvas } = require('canvas');
      const canvas = createCanvas(200, 200);
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = hex;
      ctx.fillRect(0, 0, 200, 200);
      const attachment = new AttachmentBuilder(canvas.toBuffer(), { name: 'color.png' });
      files.push(attachment);
    } catch (e) { /* canvas non dispo */ }

    const embed = new EmbedBuilder()
      .setTitle(`🎨 Couleur ${hex}`)
      .setColor(decimal)
      .addFields(
        { name: 'Hex', value: hex, inline: true },
        { name: 'RGB', value: `${r}, ${g}, ${b}`, inline: true },
        { name: 'Decimal', value: String(decimal), inline: true },
        { name: 'HSL', value: `${Math.round(h * 360)}°, ${Math.round(s * 100)}%, ${Math.round(l * 100)}%`, inline: true },
        { name: 'CSS', value: `rgb(${r}, ${g}, ${b})`, inline: true },
      );

    if (files.length) embed.setThumbnail('attachment://color.png');

    return interaction.reply({ embeds: [embed], files });
  },
};
