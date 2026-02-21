// ===================================
// Ultra Suite — /games
// Jeux multijoueurs
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, ComponentType } = require('discord.js');

module.exports = {
  module: 'fun',
  cooldown: 10,

  data: new SlashCommandBuilder()
    .setName('games')
    .setDescription('Jeux multijoueurs')
    .addSubcommand((s) =>
      s.setName('tictactoe').setDescription('Morpion')
        .addUserOption((o) => o.setName('adversaire').setDescription('Votre adversaire').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('rps').setDescription('Pierre-Feuille-Ciseaux')
        .addUserOption((o) => o.setName('adversaire').setDescription('Votre adversaire').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('coinflip').setDescription('Pile ou face'),
    )
    .addSubcommand((s) =>
      s.setName('roll').setDescription('Lancer des dés')
        .addStringOption((o) => o.setName('des').setDescription('Format: NdF (ex: 2d6, 1d20)'))
        .addIntegerOption((o) => o.setName('modificateur').setDescription('Modificateur (+/-)')),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();

    switch (sub) {
      case 'tictactoe': {
        const opponent = interaction.options.getUser('adversaire');
        if (opponent.id === interaction.user.id) return interaction.reply({ content: '❌ Vous ne pouvez pas jouer contre vous-même.', ephemeral: true });
        if (opponent.bot) return interaction.reply({ content: '❌ Vous ne pouvez pas jouer contre un bot.', ephemeral: true });

        const board = Array(9).fill(null);
        let currentPlayer = interaction.user.id;
        const players = { [interaction.user.id]: '❌', [opponent.id]: '⭕' };

        const renderBoard = () => {
          const rows = [];
          for (let i = 0; i < 9; i += 3) {
            const row = new ActionRowBuilder();
            for (let j = i; j < i + 3; j++) {
              row.addComponents(
                new ButtonBuilder()
                  .setCustomId(`ttt_${j}`)
                  .setLabel(board[j] || '‎')
                  .setEmoji(board[j] || '⬜')
                  .setStyle(board[j] === '❌' ? ButtonStyle.Danger : board[j] === '⭕' ? ButtonStyle.Primary : ButtonStyle.Secondary)
                  .setDisabled(!!board[j]),
              );
            }
            rows.push(row);
          }
          return rows;
        };

        const checkWin = () => {
          const lines = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];
          for (const [a, b, c] of lines) {
            if (board[a] && board[a] === board[b] && board[b] === board[c]) return board[a];
          }
          if (board.every((c) => c)) return 'draw';
          return null;
        };

        const embed = new EmbedBuilder()
          .setTitle('🎮 Morpion')
          .setColor(0x3498DB)
          .setDescription(`${interaction.user} ❌ vs ${opponent} ⭕\n\nTour de : ${interaction.user}`);

        const msg = await interaction.reply({ embeds: [embed], components: renderBoard(), fetchReply: true });

        const collector = msg.createMessageComponentCollector({ componentType: ComponentType.Button, time: 120000 });

        collector.on('collect', async (btn) => {
          if (btn.user.id !== currentPlayer) return btn.reply({ content: '❌ Ce n\'est pas votre tour.', ephemeral: true });

          const pos = parseInt(btn.customId.split('_')[1]);
          board[pos] = players[currentPlayer];

          const winner = checkWin();
          if (winner) {
            collector.stop();
            const resultEmbed = new EmbedBuilder()
              .setTitle('🎮 Morpion — Terminé')
              .setColor(winner === 'draw' ? 0xF39C12 : 0x2ECC71)
              .setDescription(winner === 'draw' ? '🤝 Égalité !' : `🏆 ${winner === '❌' ? interaction.user : opponent} a gagné !`);

            return btn.update({ embeds: [resultEmbed], components: renderBoard() });
          }

          currentPlayer = currentPlayer === interaction.user.id ? opponent.id : interaction.user.id;
          embed.setDescription(`${interaction.user} ❌ vs ${opponent} ⭕\n\nTour de : <@${currentPlayer}>`);
          return btn.update({ embeds: [embed], components: renderBoard() });
        });

        collector.on('end', async (_, reason) => {
          if (reason === 'time') {
            await msg.edit({ content: '⏰ Temps écoulé !', components: [] }).catch(() => {});
          }
        });
        break;
      }

      case 'rps': {
        const opponent = interaction.options.getUser('adversaire');
        if (opponent.bot || opponent.id === interaction.user.id) return interaction.reply({ content: '❌ Adversaire invalide.', ephemeral: true });

        const choices = {};
        const row = new ActionRowBuilder().addComponents(
          new ButtonBuilder().setCustomId('rps_rock').setLabel('Pierre').setEmoji('🪨').setStyle(ButtonStyle.Secondary),
          new ButtonBuilder().setCustomId('rps_paper').setLabel('Feuille').setEmoji('📄').setStyle(ButtonStyle.Secondary),
          new ButtonBuilder().setCustomId('rps_scissors').setLabel('Ciseaux').setEmoji('✂️').setStyle(ButtonStyle.Secondary),
        );

        const embed = new EmbedBuilder()
          .setTitle('✊ Pierre-Feuille-Ciseaux')
          .setColor(0x3498DB)
          .setDescription(`${interaction.user} vs ${opponent}\n\nChoisissez votre coup !`);

        const msg = await interaction.reply({ embeds: [embed], components: [row], fetchReply: true });
        const collector = msg.createMessageComponentCollector({ componentType: ComponentType.Button, time: 30000 });

        collector.on('collect', async (btn) => {
          if (btn.user.id !== interaction.user.id && btn.user.id !== opponent.id) return btn.reply({ content: '❌ Vous ne participez pas.', ephemeral: true });
          if (choices[btn.user.id]) return btn.reply({ content: '❌ Vous avez déjà choisi.', ephemeral: true });

          choices[btn.user.id] = btn.customId.split('_')[1];
          await btn.reply({ content: `✅ Choix enregistré !`, ephemeral: true });

          if (Object.keys(choices).length === 2) collector.stop('done');
        });

        collector.on('end', async (_, reason) => {
          if (Object.keys(choices).length < 2) return msg.edit({ content: '⏰ Temps écoulé !', components: [] }).catch(() => {});

          const emojis = { rock: '🪨', paper: '📄', scissors: '✂️' };
          const c1 = choices[interaction.user.id], c2 = choices[opponent.id];

          let result;
          if (c1 === c2) result = '🤝 Égalité !';
          else if ((c1 === 'rock' && c2 === 'scissors') || (c1 === 'paper' && c2 === 'rock') || (c1 === 'scissors' && c2 === 'paper'))
            result = `🏆 ${interaction.user} gagne !`;
          else result = `🏆 ${opponent} gagne !`;

          const resultEmbed = new EmbedBuilder()
            .setTitle('✊ Pierre-Feuille-Ciseaux — Résultat')
            .setColor(0x2ECC71)
            .setDescription(`${interaction.user} ${emojis[c1]} vs ${emojis[c2]} ${opponent}\n\n${result}`);

          await msg.edit({ embeds: [resultEmbed], components: [] }).catch(() => {});
        });
        break;
      }

      case 'coinflip': {
        const result = Math.random() < 0.5 ? 'Pile' : 'Face';
        const emoji = result === 'Pile' ? '🪙' : '💫';
        return interaction.reply({
          embeds: [new EmbedBuilder().setTitle(`${emoji} ${result} !`).setColor(0xF1C40F)],
        });
      }

      case 'roll': {
        const diceStr = interaction.options.getString('des') || '1d6';
        const mod = interaction.options.getInteger('modificateur') || 0;

        const match = diceStr.match(/^(\d+)d(\d+)$/);
        if (!match) return interaction.reply({ content: '❌ Format invalide. Utilisez NdF (ex: 2d6)', ephemeral: true });

        const [, num, faces] = match.map(Number);
        if (num > 100 || faces > 1000) return interaction.reply({ content: '❌ Maximum 100d1000.', ephemeral: true });

        const rolls = Array.from({ length: num }, () => Math.floor(Math.random() * faces) + 1);
        const total = rolls.reduce((a, b) => a + b, 0) + mod;

        return interaction.reply({
          embeds: [new EmbedBuilder()
            .setTitle('🎲 Lancer de dés')
            .setColor(0xE67E22)
            .addFields(
              { name: 'Dés', value: `${num}d${faces}${mod ? ` + ${mod}` : ''}`, inline: true },
              { name: 'Résultats', value: rolls.join(', '), inline: true },
              { name: 'Total', value: `**${total}**`, inline: true },
            )],
        });
      }
    }
  },
};
