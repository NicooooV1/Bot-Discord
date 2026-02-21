// ===================================
// Ultra Suite - /music
// Systeme musical via Lavalink v4 (Shoukaku)
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const lavalink = require('../../core/lavalink');
const { createModuleLogger } = require('../../core/logger');

const log = createModuleLogger('Music');

// In-memory queue per guild
const queues = new Map();

class GuildQueue {
  constructor(guildId) {
    this.guildId = guildId;
    this.tracks = [];
    this.current = null;
    this.player = null;
    this.loop = 'off';       // off | track | queue
    this.volume = 80;
    this.paused = false;
    this.autoplay = false;
    this.textChannelId = null;
  }

  destroy() {
    if (this.player) {
      try { this.player.stopTrack(); } catch {}
    }
    lavalink.destroyPlayer(this.guildId).catch(() => {});
    queues.delete(this.guildId);
  }
}

function getQueue(guildId) { return queues.get(guildId); }
function createQueue(guildId) {
  const q = new GuildQueue(guildId);
  queues.set(guildId, q);
  return q;
}

/**
 * Joue la piste suivante dans la queue.
 */
async function playNext(queue, client) {
  if (!queue.player) return;

  if (queue.loop === 'track' && queue.current) {
    // Boucle sur la piste actuelle
    await queue.player.playTrack({ track: queue.current.encoded });
    return;
  }

  if (queue.loop === 'queue' && queue.current) {
    queue.tracks.push(queue.current);
  }

  if (queue.tracks.length === 0) {
    queue.current = null;
    // Auto-disconnect apres 5 minutes d inactivite
    setTimeout(() => {
      const q = getQueue(queue.guildId);
      if (q && !q.current && q.tracks.length === 0) {
        q.destroy();
      }
    }, 300000);
    return;
  }

  const next = queue.tracks.shift();
  queue.current = next;
  await queue.player.playTrack({ track: next.encoded });
  await queue.player.setGlobalVolume(queue.volume);
}

/**
 * Formate une duree en ms vers mm:ss
 */
function formatDuration(ms) {
  if (!ms || ms <= 0) return 'LIVE';
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

module.exports = {
  module: 'music',
  cooldown: 2,

  data: new SlashCommandBuilder()
    .setName('music')
    .setDescription('Commandes musicales')
    .addSubcommand((s) =>
      s.setName('play').setDescription('Jouer une musique ou une playlist')
        .addStringOption((o) => o.setName('query').setDescription('URL ou recherche').setRequired(true)),
    )
    .addSubcommand((s) => s.setName('skip').setDescription('Passer la musique actuelle'))
    .addSubcommand((s) => s.setName('stop').setDescription('Arreter la musique et quitter'))
    .addSubcommand((s) => s.setName('pause').setDescription('Mettre en pause / reprendre'))
    .addSubcommand((s) => s.setName('queue').setDescription('Voir la file d\'attente')
      .addIntegerOption((o) => o.setName('page').setDescription('Page').setMinValue(1)))
    .addSubcommand((s) => s.setName('nowplaying').setDescription('Musique en cours'))
    .addSubcommand((s) => s.setName('volume').setDescription('Regler le volume')
      .addIntegerOption((o) => o.setName('niveau').setDescription('Volume (0-150)').setRequired(true).setMinValue(0).setMaxValue(150)))
    .addSubcommand((s) => s.setName('shuffle').setDescription('Melanger la file'))
    .addSubcommand((s) => s.setName('loop').setDescription('Mode de boucle')
      .addStringOption((o) => o.setName('mode').setDescription('Mode').setRequired(true).addChoices(
        { name: 'Desactive', value: 'off' },
        { name: 'Piste', value: 'track' },
        { name: 'File', value: 'queue' },
      )))
    .addSubcommand((s) => s.setName('remove').setDescription('Retirer une piste')
      .addIntegerOption((o) => o.setName('position').setDescription('Position').setRequired(true).setMinValue(1)))
    .addSubcommand((s) => s.setName('move').setDescription('Deplacer une piste')
      .addIntegerOption((o) => o.setName('de').setDescription('Position actuelle').setRequired(true).setMinValue(1))
      .addIntegerOption((o) => o.setName('vers').setDescription('Nouvelle position').setRequired(true).setMinValue(1)))
    .addSubcommand((s) => s.setName('clear').setDescription('Vider la file d\'attente'))
    .addSubcommand((s) => s.setName('seek').setDescription('Aller a un timestamp')
      .addStringOption((o) => o.setName('temps').setDescription('Timestamp (ex: 1:30)').setRequired(true)))
    .addSubcommand((s) => s.setName('replay').setDescription('Rejouer la piste actuelle'))
    .addSubcommand((s) => s.setName('autoplay').setDescription('Activer/desactiver l\'autoplay'))
    .addSubcommand((s) => s.setName('lyrics').setDescription('Paroles de la musique en cours'))
    .addSubcommand((s) => s.setName('filter').setDescription('Appliquer un filtre audio')
      .addStringOption((o) => o.setName('filtre').setDescription('Filtre').setRequired(true).addChoices(
        { name: 'Bassboost', value: 'bassboost' },
        { name: 'Nightcore', value: 'nightcore' },
        { name: 'Vaporwave', value: 'vaporwave' },
        { name: '8D', value: '8d' },
        { name: 'Aucun', value: 'none' },
      ))),

  async execute(interaction) {
    const shoukaku = lavalink.getShoukaku();
    if (!shoukaku) {
      return interaction.reply({ content: '\u274c Lavalink non disponible. Le module musique est desactive.', ephemeral: true });
    }

    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const member = await interaction.guild.members.fetch(interaction.user.id).catch(() => null);
    const voiceChannel = member?.voice.channel;

    if (!voiceChannel && ['play', 'skip', 'stop', 'pause', 'shuffle', 'volume', 'seek', 'replay', 'filter'].includes(sub)) {
      return interaction.reply({ content: '\u274c Vous devez etre dans un salon vocal.', ephemeral: true });
    }

    switch (sub) {
      case 'play': {
        const query = interaction.options.getString('query');
        await interaction.deferReply();

        // Rechercher via Lavalink
        const result = await lavalink.search(query);
        if (!result || result.loadType === 'empty' || result.loadType === 'error') {
          return interaction.editReply('\u274c Aucun resultat trouve.');
        }

        let tracks = [];
        if (result.loadType === 'track' || result.loadType === 'short') {
          tracks = [result.data];
        } else if (result.loadType === 'playlist') {
          tracks = result.data.tracks || [];
        } else if (result.loadType === 'search') {
          tracks = result.data.length > 0 ? [result.data[0]] : [];
        }

        if (!tracks.length) return interaction.editReply('\u274c Aucun resultat trouve.');

        // Convertir en format queue
        const queueTracks = tracks.map((t) => ({
          encoded: t.encoded,
          title: t.info?.title || 'Inconnu',
          uri: t.info?.uri || '',
          duration: t.info?.length || 0,
          author: t.info?.author || 'Inconnu',
          requester: interaction.user.id,
        }));

        let queue = getQueue(guildId);
        if (!queue) {
          queue = createQueue(guildId);
          queue.textChannelId = interaction.channelId;

          // Creer le player Lavalink
          const player = await lavalink.createPlayer(guildId, voiceChannel.id);
          if (!player) {
            queue.destroy();
            return interaction.editReply('\u274c Impossible de rejoindre le salon vocal.');
          }
          queue.player = player;

          // Event: piste terminee
          player.on('end', async (data) => {
            if (data.reason === 'replaced') return;
            await playNext(queue, interaction.client);
          });

          // Event: erreur de lecture
          player.on('exception', (error) => {
            log.error(`Erreur lecture (guild: ${guildId}): ${error.message}`);
            const ch = interaction.client.channels.cache.get(queue.textChannelId);
            if (ch) ch.send({ content: `\u274c Erreur de lecture: ${error.message}` }).catch(() => {});
            playNext(queue, interaction.client).catch(() => {});
          });

          // Event: joueur bloque
          player.on('stuck', () => {
            log.warn(`Lecteur bloque (guild: ${guildId}), passage a la suite`);
            playNext(queue, interaction.client).catch(() => {});
          });
        }

        if (queueTracks.length === 1) {
          if (!queue.current) {
            queue.current = queueTracks[0];
            await queue.player.playTrack({ track: queueTracks[0].encoded });
            await queue.player.setGlobalVolume(queue.volume);

            const embed = new EmbedBuilder().setColor(0x2ECC71).setTitle('\ud83c\udfb5 Lecture en cours')
              .setDescription(`[${queueTracks[0].title}](${queueTracks[0].uri})`)
              .addFields(
                { name: 'Duree', value: formatDuration(queueTracks[0].duration), inline: true },
                { name: 'Artiste', value: queueTracks[0].author, inline: true },
                { name: 'Demande par', value: `<@${queueTracks[0].requester}>`, inline: true },
              );
            return interaction.editReply({ embeds: [embed] });
          }
          queue.tracks.push(queueTracks[0]);
          return interaction.editReply(`\u2705 **${queueTracks[0].title}** ajoute a la file (#${queue.tracks.length}).`);
        }

        queue.tracks.push(...queueTracks);
        if (!queue.current) await playNext(queue, interaction.client);
        return interaction.editReply(`\u2705 **${queueTracks.length}** pistes ajoutees.`);
      }

      case 'skip': {
        const queue = getQueue(guildId);
        if (!queue?.current) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        await queue.player.stopTrack();
        return interaction.reply('\u23ed\ufe0f Piste passee.');
      }

      case 'stop': {
        const queue = getQueue(guildId);
        if (!queue) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        queue.tracks = [];
        queue.destroy();
        return interaction.reply('\u23f9\ufe0f Musique arretee.');
      }

      case 'pause': {
        const queue = getQueue(guildId);
        if (!queue?.player) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        if (queue.paused) {
          await queue.player.setPaused(false);
          queue.paused = false;
          return interaction.reply('\u25b6\ufe0f Reprise.');
        }
        await queue.player.setPaused(true);
        queue.paused = true;
        return interaction.reply('\u23f8\ufe0f En pause.');
      }

      case 'queue': {
        const queue = getQueue(guildId);
        if (!queue?.current && !queue?.tracks.length) return interaction.reply({ content: '\ud83d\udccb File vide.', ephemeral: true });
        const page = (interaction.options.getInteger('page') || 1) - 1;
        const perPage = 10;
        const totalPages = Math.ceil(queue.tracks.length / perPage) || 1;
        const items = queue.tracks.slice(page * perPage, (page + 1) * perPage);
        const embed = new EmbedBuilder().setTitle('\ud83c\udfb6 File d\'attente').setColor(0x3498DB)
          .setDescription(
            `**En cours:** ${queue.current?.title || 'Rien'} \`${formatDuration(queue.current?.duration)}\`\n\n` +
            (items.length
              ? items.map((t, i) => `**${page * perPage + i + 1}.** ${t.title} \`${formatDuration(t.duration)}\``).join('\n')
              : 'File vide.')
          )
          .setFooter({ text: `Page ${page + 1}/${totalPages} | ${queue.tracks.length} piste(s) | Boucle: ${queue.loop}` });
        return interaction.reply({ embeds: [embed] });
      }

      case 'nowplaying': {
        const queue = getQueue(guildId);
        if (!queue?.current) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        const pos = queue.player?.position || 0;
        const embed = new EmbedBuilder().setTitle('\ud83c\udfb5 En cours').setColor(0x9B59B6)
          .setDescription(`[${queue.current.title}](${queue.current.uri})`)
          .addFields(
            { name: 'Progression', value: `${formatDuration(pos)} / ${formatDuration(queue.current.duration)}`, inline: true },
            { name: 'Volume', value: `${queue.volume}%`, inline: true },
            { name: 'Boucle', value: queue.loop, inline: true },
            { name: 'Artiste', value: queue.current.author, inline: true },
          );
        const row = new ActionRowBuilder().addComponents(
          new ButtonBuilder().setCustomId('music_prev').setEmoji('\u23ee\ufe0f').setStyle(ButtonStyle.Secondary),
          new ButtonBuilder().setCustomId('music_pause').setEmoji(queue.paused ? '\u25b6\ufe0f' : '\u23f8\ufe0f').setStyle(ButtonStyle.Primary),
          new ButtonBuilder().setCustomId('music_skip').setEmoji('\u23ed\ufe0f').setStyle(ButtonStyle.Secondary),
          new ButtonBuilder().setCustomId('music_stop').setEmoji('\u23f9\ufe0f').setStyle(ButtonStyle.Danger),
          new ButtonBuilder().setCustomId('music_shuffle').setEmoji('\ud83d\udd00').setStyle(ButtonStyle.Secondary),
        );
        return interaction.reply({ embeds: [embed], components: [row] });
      }

      case 'volume': {
        const queue = getQueue(guildId);
        if (!queue) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        queue.volume = interaction.options.getInteger('niveau');
        if (queue.player) await queue.player.setGlobalVolume(queue.volume);
        return interaction.reply(`\ud83d\udd0a Volume: **${queue.volume}%**`);
      }

      case 'shuffle': {
        const queue = getQueue(guildId);
        if (!queue?.tracks.length) return interaction.reply({ content: '\u274c File vide.', ephemeral: true });
        for (let i = queue.tracks.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [queue.tracks[i], queue.tracks[j]] = [queue.tracks[j], queue.tracks[i]];
        }
        return interaction.reply('\ud83d\udd00 File melangee.');
      }

      case 'loop': {
        const queue = getQueue(guildId);
        if (!queue) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        queue.loop = interaction.options.getString('mode');
        return interaction.reply(`\ud83d\udd01 Boucle: **${queue.loop}**`);
      }

      case 'remove': {
        const queue = getQueue(guildId);
        const pos = interaction.options.getInteger('position') - 1;
        if (!queue?.tracks[pos]) return interaction.reply({ content: '\u274c Position invalide.', ephemeral: true });
        const removed = queue.tracks.splice(pos, 1)[0];
        return interaction.reply(`\ud83d\uddd1\ufe0f **${removed.title}** retire.`);
      }

      case 'move': {
        const queue = getQueue(guildId);
        const from = interaction.options.getInteger('de') - 1;
        const to = interaction.options.getInteger('vers') - 1;
        if (!queue?.tracks[from]) return interaction.reply({ content: '\u274c Position invalide.', ephemeral: true });
        const [track] = queue.tracks.splice(from, 1);
        queue.tracks.splice(to, 0, track);
        return interaction.reply(`\u2705 **${track.title}** -> position ${to + 1}.`);
      }

      case 'clear': {
        const queue = getQueue(guildId);
        if (!queue) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        const c = queue.tracks.length;
        queue.tracks = [];
        return interaction.reply(`\ud83d\uddd1\ufe0f ${c} piste(s) supprimee(s).`);
      }

      case 'seek': {
        const queue = getQueue(guildId);
        if (!queue?.current || !queue?.player) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        const temps = interaction.options.getString('temps');
        const parts = temps.split(':').map(Number);
        let ms = 0;
        if (parts.length === 2) ms = (parts[0] * 60 + parts[1]) * 1000;
        else if (parts.length === 3) ms = (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000;
        else ms = parts[0] * 1000;
        await queue.player.seekTo(ms);
        return interaction.reply(`\u23e9 Position: **${temps}**`);
      }

      case 'replay': {
        const queue = getQueue(guildId);
        if (!queue?.current) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        await queue.player.playTrack({ track: queue.current.encoded });
        return interaction.reply('\ud83d\udd04 Relance.');
      }

      case 'autoplay': {
        const queue = getQueue(guildId);
        if (!queue) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        queue.autoplay = !queue.autoplay;
        return interaction.reply(`Autoplay **${queue.autoplay ? 'active' : 'desactive'}**.`);
      }

      case 'lyrics': {
        const queue = getQueue(guildId);
        if (!queue?.current) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        return interaction.reply({ content: `\ud83c\udfa4 Paroles pour **${queue.current.title}** - configurez une cle API Genius.`, ephemeral: true });
      }

      case 'filter': {
        const queue = getQueue(guildId);
        if (!queue?.player) return interaction.reply({ content: '\u274c Rien en lecture.', ephemeral: true });
        const f = interaction.options.getString('filtre');

        // Appliquer les filtres via Lavalink
        const filters = {};
        switch (f) {
          case 'bassboost':
            filters.equalizer = [
              { band: 0, gain: 0.6 }, { band: 1, gain: 0.7 },
              { band: 2, gain: 0.8 }, { band: 3, gain: 0.55 },
              { band: 4, gain: 0.25 },
            ];
            break;
          case 'nightcore':
            filters.timescale = { speed: 1.3, pitch: 1.3, rate: 1.0 };
            break;
          case 'vaporwave':
            filters.timescale = { speed: 0.8, pitch: 0.8, rate: 1.0 };
            filters.tremolo = { frequency: 14.0, depth: 0.3 };
            break;
          case '8d':
            filters.rotation = { rotationHz: 0.2 };
            break;
          case 'none':
            // Vider tous les filtres
            break;
        }

        await queue.player.setFilters(filters);
        return interaction.reply(`\ud83c\udfdb\ufe0f Filtre: **${f}**`);
      }
    }
  },
};
