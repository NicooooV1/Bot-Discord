// ===================================
// Ultra Suite — /ship
// Compatibilité entre deux utilisateurs
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

module.exports = {
  module: 'fun',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('ship')
    .setDescription('Tester la compatibilité entre deux personnes')
    .addUserOption((o) => o.setName('personne1').setDescription('Première personne').setRequired(true))
    .addUserOption((o) => o.setName('personne2').setDescription('Deuxième personne')),

  async execute(interaction) {
    const user1 = interaction.options.getUser('personne1');
    const user2 = interaction.options.getUser('personne2') || interaction.user;

    // Deterministic percentage based on user IDs
    const combined = [user1.id, user2.id].sort().join('');
    let hash = 0;
    for (let i = 0; i < combined.length; i++) {
      hash = ((hash << 5) - hash) + combined.charCodeAt(i);
      hash |= 0;
    }
    const percentage = Math.abs(hash) % 101;

    // Name fusion
    const name1 = user1.username;
    const name2 = user2.username;
    const shipName = name1.substring(0, Math.ceil(name1.length / 2)) + name2.substring(Math.floor(name2.length / 2));

    let message, emoji, color;
    if (percentage >= 90) { message = '💞 Un amour parfait ! Fait l\'un pour l\'autre !'; emoji = '💕'; color = 0xFF1493; }
    else if (percentage >= 70) { message = '❤️ Excellente compatibilité ! Ça match bien !'; emoji = '❤️'; color = 0xFF4500; }
    else if (percentage >= 50) { message = '💛 Bonne compatibilité, il y a du potentiel !'; emoji = '💛'; color = 0xFFD700; }
    else if (percentage >= 30) { message = '🤔 Moyen... Ça pourrait marcher avec des efforts.'; emoji = '💔'; color = 0xFF8C00; }
    else if (percentage >= 10) { message = '😬 Pas vraiment compatible...'; emoji = '💔'; color = 0x95A5A6; }
    else { message = '💀 Fuyez. Fuyez très loin.'; emoji = '☠️'; color = 0x2F3136; }

    const bar = '█'.repeat(Math.round(percentage / 5)) + '░'.repeat(20 - Math.round(percentage / 5));

    const embed = new EmbedBuilder()
      .setTitle(`${emoji} Ship — ${shipName}`)
      .setColor(color)
      .setDescription(`**${user1}** ❤️ **${user2}**\n\n${bar} **${percentage}%**\n\n${message}`)
      .setTimestamp();

    return interaction.reply({ embeds: [embed] });
  },
};
