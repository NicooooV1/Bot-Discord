// ===================================
// Ultra Suite — /gamble
// Jeux de hasard (Slots, Blackjack, Roulette, Coinflip, Dice, Crash)
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

const SLOT_SYMBOLS = ['🍒', '🍋', '🍊', '🍇', '🍉', '💎', '7️⃣', '⭐'];
const SLOT_MULTIPLIERS = { '7️⃣': 10, '💎': 7, '⭐': 5, '🍉': 4, '🍇': 3, '🍊': 2.5, '🍋': 2, '🍒': 1.5 };
const ROULETTE_REDS = [1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36];

module.exports = {
  module: 'economy',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('gamble')
    .setDescription('Jeux de hasard')
    .addSubcommand((s) =>
      s.setName('slots').setDescription('Machine à sous')
        .addIntegerOption((o) => o.setName('mise').setDescription('Mise').setRequired(true).setMinValue(10)),
    )
    .addSubcommand((s) =>
      s.setName('coinflip').setDescription('Pile ou face')
        .addIntegerOption((o) => o.setName('mise').setDescription('Mise').setRequired(true).setMinValue(10))
        .addStringOption((o) => o.setName('choix').setDescription('Pile ou Face').setRequired(true)
          .addChoices({ name: 'Pile', value: 'pile' }, { name: 'Face', value: 'face' })),
    )
    .addSubcommand((s) =>
      s.setName('dice').setDescription('Lancer de dés — pariez sur un nombre')
        .addIntegerOption((o) => o.setName('mise').setDescription('Mise').setRequired(true).setMinValue(10))
        .addIntegerOption((o) => o.setName('nombre').setDescription('Nombre (1-6)').setRequired(true).setMinValue(1).setMaxValue(6)),
    )
    .addSubcommand((s) =>
      s.setName('roulette').setDescription('Roulette')
        .addIntegerOption((o) => o.setName('mise').setDescription('Mise').setRequired(true).setMinValue(10))
        .addStringOption((o) => o.setName('pari').setDescription('Votre pari').setRequired(true)
          .addChoices(
            { name: 'Rouge', value: 'red' },
            { name: 'Noir', value: 'black' },
            { name: 'Pair', value: 'even' },
            { name: 'Impair', value: 'odd' },
            { name: '1-12', value: 'first12' },
            { name: '13-24', value: 'second12' },
            { name: '25-36', value: 'third12' },
          )),
    )
    .addSubcommand((s) =>
      s.setName('crash').setDescription('Crash — retirez avant que ça crash !')
        .addIntegerOption((o) => o.setName('mise').setDescription('Mise').setRequired(true).setMinValue(10)),
    )
    .addSubcommand((s) =>
      s.setName('blackjack').setDescription('Blackjack simplifié')
        .addIntegerOption((o) => o.setName('mise').setDescription('Mise').setRequired(true).setMinValue(10)),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const bet = interaction.options.getInteger('mise');
    const guildId = interaction.guildId;
    const userId = interaction.user.id;
    const db = getDb();
    const config = await configService.get(guildId);
    const eco = config.economy || {};
    const symbol = eco.currencySymbol || '🪙';
    const maxBet = eco.maxBet || 100000;

    if (bet > maxBet) return interaction.reply({ content: `❌ Mise maximum: **${maxBet.toLocaleString('fr-FR')}** ${symbol}`, ephemeral: true });

    const user = await db('users').where({ guild_id: guildId, user_id: userId }).first();
    if (!user || (user.balance || 0) < bet) {
      return interaction.reply({ content: '❌ Solde insuffisant.', ephemeral: true });
    }

    const logResult = async (game, result) => {
      await db('gamble_history').insert({ guild_id: guildId, user_id: userId, game, bet, result });
      if (result > 0) {
        await db('users').where({ guild_id: guildId, user_id: userId })
          .update({ balance: db.raw('balance + ?', [result]), total_earned: db.raw('COALESCE(total_earned, 0) + ?', [result]) });
      } else {
        await db('users').where({ guild_id: guildId, user_id: userId })
          .update({ balance: db.raw('GREATEST(0, balance + ?)', [result]), total_spent: db.raw('COALESCE(total_spent, 0) + ?', [Math.abs(result)]) });
      }
      await db('transactions').insert({
        guild_id: guildId,
        from_id: result > 0 ? 'CASINO' : userId,
        to_id: result > 0 ? userId : 'CASINO',
        amount: Math.abs(result),
        type: `gamble_${game}`,
      });
    };

    switch (sub) {
      case 'slots': {
        const reels = [
          SLOT_SYMBOLS[Math.floor(Math.random() * SLOT_SYMBOLS.length)],
          SLOT_SYMBOLS[Math.floor(Math.random() * SLOT_SYMBOLS.length)],
          SLOT_SYMBOLS[Math.floor(Math.random() * SLOT_SYMBOLS.length)],
        ];
        const allSame = reels[0] === reels[1] && reels[1] === reels[2];
        const twoSame = reels[0] === reels[1] || reels[1] === reels[2] || reels[0] === reels[2];

        let multiplier = 0;
        let resultText = '';
        if (allSame) {
          multiplier = SLOT_MULTIPLIERS[reels[0]] || 2;
          resultText = `🎉 JACKPOT ! x${multiplier}`;
        } else if (twoSame) {
          multiplier = 0.5;
          resultText = '😊 Deux identiques ! x0.5';
        } else {
          multiplier = -1;
          resultText = '😢 Perdu !';
        }

        const winnings = multiplier > 0 ? Math.floor(bet * multiplier) : -bet;
        await logResult('slots', winnings);

        const embed = new EmbedBuilder()
          .setTitle('🎰 Machine à sous')
          .setColor(winnings > 0 ? 0x2ECC71 : 0xE74C3C)
          .setDescription(`\`\`\`\n╔═══════════╗\n║ ${reels.join(' │ ')} ║\n╚═══════════╝\n\`\`\``)
          .addFields(
            { name: 'Résultat', value: resultText, inline: true },
            { name: winnings > 0 ? 'Gains' : 'Pertes', value: `${winnings > 0 ? '+' : ''}${winnings.toLocaleString('fr-FR')} ${symbol}`, inline: true },
          )
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }

      case 'coinflip': {
        const choice = interaction.options.getString('choix');
        const result = Math.random() < 0.5 ? 'pile' : 'face';
        const win = choice === result;
        const winnings = win ? bet : -bet;
        await logResult('coinflip', winnings);

        const embed = new EmbedBuilder()
          .setTitle(`🪙 ${result === 'pile' ? 'Pile' : 'Face'} !`)
          .setColor(win ? 0x2ECC71 : 0xE74C3C)
          .setDescription(win
            ? `✅ Vous avez gagné **+${bet.toLocaleString('fr-FR')}** ${symbol} !`
            : `❌ Vous avez perdu **${bet.toLocaleString('fr-FR')}** ${symbol}.`)
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }

      case 'dice': {
        const chosen = interaction.options.getInteger('nombre');
        const rolled = Math.floor(Math.random() * 6) + 1;
        const win = chosen === rolled;
        const winnings = win ? bet * 5 : -bet;
        await logResult('dice', winnings);

        const diceEmojis = ['', '⚀', '⚁', '⚂', '⚃', '⚄', '⚅'];
        const embed = new EmbedBuilder()
          .setTitle(`🎲 ${diceEmojis[rolled]} — ${rolled}`)
          .setColor(win ? 0x2ECC71 : 0xE74C3C)
          .setDescription(win
            ? `🎉 Vous avez deviné ! Gains: **+${winnings.toLocaleString('fr-FR')}** ${symbol}`
            : `❌ Raté ! Vous avez perdu **${bet.toLocaleString('fr-FR')}** ${symbol}`)
          .addFields({ name: 'Votre choix', value: `${chosen}`, inline: true }, { name: 'Résultat', value: `${rolled}`, inline: true })
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }

      case 'roulette': {
        const pari = interaction.options.getString('pari');
        const number = Math.floor(Math.random() * 37); // 0-36
        const isRed = ROULETTE_REDS.includes(number);
        const color = number === 0 ? '🟢' : isRed ? '🔴' : '⚫';

        let win = false;
        let multiplier = 2;
        switch (pari) {
          case 'red': win = isRed && number !== 0; break;
          case 'black': win = !isRed && number !== 0; break;
          case 'even': win = number !== 0 && number % 2 === 0; break;
          case 'odd': win = number % 2 !== 0; break;
          case 'first12': win = number >= 1 && number <= 12; multiplier = 3; break;
          case 'second12': win = number >= 13 && number <= 24; multiplier = 3; break;
          case 'third12': win = number >= 25 && number <= 36; multiplier = 3; break;
        }

        const winnings = win ? Math.floor(bet * (multiplier - 1)) : -bet;
        await logResult('roulette', winnings);

        const pariLabels = { red: '🔴 Rouge', black: '⚫ Noir', even: 'Pair', odd: 'Impair', first12: '1-12', second12: '13-24', third12: '25-36' };
        const embed = new EmbedBuilder()
          .setTitle('🎡 Roulette')
          .setColor(win ? 0x2ECC71 : 0xE74C3C)
          .setDescription(`La bille tombe sur ${color} **${number}**\n\nVotre pari : **${pariLabels[pari]}**`)
          .addFields(
            { name: 'Résultat', value: win ? `✅ Gagné ! +${winnings.toLocaleString('fr-FR')} ${symbol}` : `❌ Perdu ! -${bet.toLocaleString('fr-FR')} ${symbol}` },
          )
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }

      case 'crash': {
        // Generate crash point
        const crashPoint = (1 / (1 - Math.random()) * 0.95).toFixed(2);
        const safeCashout = Math.min(parseFloat(crashPoint) * 0.7, parseFloat(crashPoint) - 0.1).toFixed(2);

        // Auto-play: bot picks a random cashout point for the player
        const playerCashout = (1 + Math.random() * Math.min(parseFloat(crashPoint), 5)).toFixed(2);
        const win = parseFloat(playerCashout) < parseFloat(crashPoint);
        const winnings = win ? Math.floor(bet * parseFloat(playerCashout)) - bet : -bet;
        await logResult('crash', winnings);

        const embed = new EmbedBuilder()
          .setTitle('📈 Crash')
          .setColor(win ? 0x2ECC71 : 0xE74C3C)
          .setDescription(win
            ? `Le multiplicateur a crashé à **x${crashPoint}**\nVous avez retiré à **x${playerCashout}** ! 🎉\n\nGains: **+${winnings.toLocaleString('fr-FR')}** ${symbol}`
            : `💥 Crash à **x${crashPoint}** ! Vous avez perdu **${bet.toLocaleString('fr-FR')}** ${symbol}`)
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }

      case 'blackjack': {
        const cards = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K'];
        const suits = ['♠️', '♥️', '♦️', '♣️'];

        const draw = () => {
          const card = cards[Math.floor(Math.random() * cards.length)];
          const suit = suits[Math.floor(Math.random() * suits.length)];
          return { card, suit, display: `${card}${suit}` };
        };

        const handValue = (hand) => {
          let value = 0;
          let aces = 0;
          for (const c of hand) {
            if (c.card === 'A') { aces++; value += 11; }
            else if (['J', 'Q', 'K'].includes(c.card)) { value += 10; }
            else { value += parseInt(c.card); }
          }
          while (value > 21 && aces > 0) { value -= 10; aces--; }
          return value;
        };

        const playerHand = [draw(), draw()];
        const dealerHand = [draw(), draw()];

        // Simple auto-play strategy
        while (handValue(playerHand) < 17) {
          playerHand.push(draw());
        }
        while (handValue(dealerHand) < 17) {
          dealerHand.push(draw());
        }

        const pVal = handValue(playerHand);
        const dVal = handValue(dealerHand);

        let result;
        if (pVal > 21) result = 'bust';
        else if (dVal > 21) result = 'win';
        else if (pVal === 21 && playerHand.length === 2) result = 'blackjack';
        else if (pVal > dVal) result = 'win';
        else if (pVal < dVal) result = 'lose';
        else result = 'push';

        let winnings;
        switch (result) {
          case 'blackjack': winnings = Math.floor(bet * 1.5); break;
          case 'win': winnings = bet; break;
          case 'push': winnings = 0; break;
          default: winnings = -bet;
        }

        await logResult('blackjack', winnings);

        const resultLabels = { blackjack: '🃏 BLACKJACK !', win: '✅ Victoire !', push: '🤝 Égalité !', bust: '💥 Bust !', lose: '❌ Défaite !' };
        const embed = new EmbedBuilder()
          .setTitle('🃏 Blackjack')
          .setColor(winnings > 0 ? 0x2ECC71 : winnings === 0 ? 0xF1C40F : 0xE74C3C)
          .addFields(
            { name: `Vos cartes (${pVal})`, value: playerHand.map((c) => c.display).join(' '), inline: true },
            { name: `Croupier (${dVal})`, value: dealerHand.map((c) => c.display).join(' '), inline: true },
            { name: 'Résultat', value: `${resultLabels[result]}\n${winnings >= 0 ? '+' : ''}${winnings.toLocaleString('fr-FR')} ${symbol}` },
          )
          .setTimestamp();

        return interaction.reply({ embeds: [embed] });
      }
    }
  },
};
