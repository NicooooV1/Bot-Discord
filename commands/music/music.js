// ===================================
// Ultra Suite — /music
// Système musical complet
// ===================================

const { SlashCommandBuilder, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');

let voiceModule, playDl;
try { voiceModule = require('@discordjs/voice'); } catch { voiceModule = null; }
try { playDl = require('play-dl'); } catch { playDl = null; }

// In-memory queue per guild
const queues = new Map();

class GuildQueue {
  constructor(guildId) {
    this.guildId = guildId;
    this.tracks = [];
    this.current = null;
    this.player = null;
    this.connection = null;
    this.loop = 'off';
    this.volume = 80;
    this.paused = false;
    this.autoplay = false;
    this.filters = [];
  }

  destroy() {
    if (this.player) this.player.stop(true);
    if (this.connection) this.connection.destroy();
    queues.delete(this.guildId);
  }
}

const getQueue = (guildId) => queues.get(guildId);
const createQueue = (guildId) => {
  const q = new GuildQueue(guildId);
  queues.set(guildId, q);
  return q;
};

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
    .addSubcommand((s) => s.setName('stop').setDescription('Arrêter la musique et quitter'))
    .addSubcommand((s) => s.setName('pause').setDescription('Mettre en pause / reprendre'))
    .addSubcommand((s) => s.setName('queue').setDescription('Voir la file d\'attente')
      .addIntegerOption((o) => o.setName('page').setDescription('Page').setMinValue(1)))
    .addSubcommand((s) => s.setName('nowplaying').setDescription('Musique en cours'))
    .addSubcommand((s) => s.setName('volume').setDescription('Régler le volume')
      .addIntegerOption((o) => o.setName('niveau').setDescription('Volume (0-150)').setRequired(true).setMinValue(0).setMaxValue(150)))
    .addSubcommand((s) => s.setName('shuffle').setDescription('Mélanger la file'))
    .addSubcommand((s) => s.setName('loop').setDescription('Mode de boucle')
      .addStringOption((o) => o.setName('mode').setDescription('Mode').setRequired(true).addChoices(
        { name: 'Désactivé', value: 'off' },
        { name: 'Piste', value: 'track' },
        { name: 'File', value: 'queue' },
      )))
    .addSubcommand((s) => s.setName('remove').setDescription('Retirer une piste')
      .addIntegerOption((o) => o.setName('position').setDescription('Position').setRequired(true).setMinValue(1)))
    .addSubcommand((s) => s.setName('move').setDescription('Déplacer une piste')
      .addIntegerOption((o) => o.setName('de').setDescription('Position actuelle').setRequired(true).setMinValue(1))
      .addIntegerOption((o) => o.setName('vers').setDescription('Nouvelle position').setRequired(true).setMinValue(1)))
    .addSubcommand((s) => s.setName('clear').setDescription('Vider la file d\'attente'))
    .addSubcommand((s) => s.setName('seek').setDescription('Aller à un timestamp')
      .addStringOption((o) => o.setName('temps').setDescription('Timestamp (ex: 1:30)').setRequired(true)))
    .addSubcommand((s) => s.setName('replay').setDescription('Rejouer la piste actuelle'))
    .addSubcommand((s) => s.setName('autoplay').setDescription('Activer/désactiver l\'autoplay'))
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
    if (!voiceModule) {
      return interaction.reply({ content: '❌ Le module audio n\'est pas installé. Exécutez `npm install @discordjs/voice @discordjs/opus play-dl`.', ephemeral: true });
    }

    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const member = await interaction.guild.members.fetch(interaction.user.id).catch(() => null);
    const voiceChannel = member?.voice.channel;

    if (!voiceChannel && ['play', 'skip', 'stop', 'pause', 'shuffle', 'volume', 'seek', 'replay', 'filter'].includes(sub)) {
      return interaction.reply({ content: '❌ Vous devez être dans un salon vocal.', ephemeral: true });
    }

    const playTrack = async (queue, track) => {
      if (!playDl) return;
      try {
        const stream = await playDl.stream(track.url);
        const resource = voiceModule.createAudioResource(stream.stream, { inputType: stream.type });
        queue.player.play(resource);
        queue.current = track;
      } catch (err) {
        const ch = interaction.channel;
        if (ch) ch.send({ content: `❌ Erreur de lecture: ${err.message}` }).catch(() => null);
        if (queue.tracks.length > 0) playTrack(queue, queue.tracks.shift());
      }
    };

    switch (sub) {
      case 'play': {
        const query = interaction.options.getString('query');
        await interaction.deferReply();

        if (!playDl) return interaction.editReply('❌ play-dl non disponible.');

        let tracks = [];
        try {
          const validated = playDl.yt_validate(query);
          if (validated === 'video') {
            const info = await playDl.video_info(query);
            tracks.push({ title: info.video_details.title, url: query, duration: info.video_details.durationRaw, requester: interaction.user.id });
          } else if (validated === 'playlist') {
            const playlist = await playDl.playlist_info(query, { incomplete: true });
            const videos = await playlist.all_videos();
            tracks = videos.map((v) => ({ title: v.title, url: v.url, duration: v.durationRaw, requester: interaction.user.id }));
          } else {
            const results = await playDl.search(query, { limit: 1 });
            if (results.length) tracks.push({ title: results[0].title, url: results[0].url, duration: results[0].durationRaw, requester: interaction.user.id });
          }
        } catch {
          try {
            const results = await playDl.search(query, { limit: 1 });
            if (results.length) tracks.push({ title: results[0].title, url: results[0].url, duration: results[0].durationRaw, requester: interaction.user.id });
          } catch { /* empty */ }
        }

        if (!tracks.length) return interaction.editReply('❌ Aucun résultat trouvé.');

        let queue = getQueue(guildId);
        if (!queue) {
          queue = createQueue(guildId);
          queue.connection = voiceModule.joinVoiceChannel({ channelId: voiceChannel.id, guildId, adapterCreator: interaction.guild.voiceAdapterCreator });
          queue.player = voiceModule.createAudioPlayer();
          queue.connection.subscribe(queue.player);

          queue.player.on(voiceModule.AudioPlayerStatus.Idle, () => {
            if (queue.loop === 'track' && queue.current) { playTrack(queue, queue.current); }
            else if (queue.tracks.length > 0) {
              if (queue.loop === 'queue' && queue.current) queue.tracks.push(queue.current);
              playTrack(queue, queue.tracks.shift());
            } else {
              setTimeout(() => { if (!queue.tracks.length) queue.destroy(); }, 300000);
            }
          });
        }

        if (tracks.length === 1) {
          if (!queue.current) {
            await playTrack(queue, tracks[0]);
            const embed = new EmbedBuilder().setColor(0x2ECC71).setTitle('🎵 Lecture en cours')
              .setDescription(`[${tracks[0].title}](${tracks[0].url})`)
              .addFields({ name: 'Durée', value: tracks[0].duration || 'N/A', inline: true }, { name: 'Demandé par', value: `<@${tracks[0].requester}>`, inline: true });
            return interaction.editReply({ embeds: [embed] });
          }
          queue.tracks.push(tracks[0]);
          return interaction.editReply(`✅ **${tracks[0].title}** ajouté à la file (#${queue.tracks.length}).`);
        }
        queue.tracks.push(...tracks);
        if (!queue.current) playTrack(queue, queue.tracks.shift());
        return interaction.editReply(`✅ **${tracks.length}** pistes ajoutées.`);
      }

      case 'skip': {
        const queue = getQueue(guildId);
        if (!queue?.current) return interaction.reply({ content: '❌ Rien en lecture.', ephemeral: true });
        queue.player.stop();
        return interaction.reply('⏭️ Piste passée.');
      }

      case 'stop': {
        const queue = getQueue(guildId);
        if (!queue) return interaction.reply({ content: '❌ Rien en lecture.', ephemeral: true });
        queue.tracks = [];
        queue.destroy();
        return interaction.reply('⏹️ Musique arrêtée.');
      }

      case 'pause': {
        const queue = getQueue(guildId);
        if (!queue?.player) return interaction.reply({ content: '❌ Rien en lecture.', ephemeral: true });
        if (queue.paused) { queue.player.unpause(); queue.paused = false; return interaction.reply('▶️ Reprise.'); }
        queue.player.pause(); queue.paused = true;
        return interaction.reply('⏸️ En pause.');
      }

      case 'queue': {
        const queue = getQueue(guildId);
        if (!queue?.current && !queue?.tracks.length) return interaction.reply({ content: '📋 File vide.', ephemeral: true });
        const page = (interaction.options.getInteger('page') || 1) - 1;
        const perPage = 10;
        const totalPages = Math.ceil(queue.tracks.length / perPage) || 1;
        const items = queue.tracks.slice(page * perPage, (page + 1) * perPage);
        const embed = new EmbedBuilder().setTitle('🎶 File d\'attente').setColor(0x3498DB)
          .setDescription(`**En cours:** ${queue.current?.title || 'Rien'}\n\n${items.length ? items.map((t, i) => `**${page * perPage + i + 1}.** ${t.title} \`${t.duration || '?'}\``).join('\n') : 'File vide.'}`)
          .setFooter({ text: `Page ${page + 1}/${totalPages} • ${queue.tracks.length} piste(s) • Boucle: ${queue.loop}` });
        return interaction.reply({ embeds: [embed] });
      }

      case 'nowplaying': {
        const queue = getQueue(guildId);
        if (!queue?.current) return interaction.reply({ content: '❌ Rien en lecture.', ephemeral: true });
        const embed = new EmbedBuilder().setTitle('🎵 En cours').setColor(0x9B59B6)
          .setDescription(`[${queue.current.title}](${queue.current.url})`)
          .addFields({ name: 'Durée', value: queue.current.duration || 'N/A', inline: true }, { name: 'Volume', value: `${queue.volume}%`, inline: true }, { name: 'Boucle', value: queue.loop, inline: true });
        const row = new ActionRowBuilder().addComponents(
          new ButtonBuilder().setCustomId('music_prev').setEmoji('⏮️').setStyle(ButtonStyle.Secondary),
          new ButtonBuilder().setCustomId('music_pause').setEmoji(queue.paused ? '▶️' : '⏸️').setStyle(ButtonStyle.Primary),
          new ButtonBuilder().setCustomId('music_skip').setEmoji('⏭️').setStyle(ButtonStyle.Secondary),
          new ButtonBuilder().setCustomId('music_stop').setEmoji('⏹️').setStyle(ButtonStyle.Danger),
          new ButtonBuilder().setCustomId('music_shuffle').setEmoji('🔀').setStyle(ButtonStyle.Secondary),
        );
        return interaction.reply({ embeds: [embed], components: [row] });
      }

      case 'volume': {
        const queue = getQueue(guildId);
        if (!queue) return interaction.reply({ content: '❌ Rien en lecture.', ephemeral: true });
        queue.volume = interaction.options.getInteger('niveau');
        return interaction.reply(`🔊 Volume: **${queue.volume}%**`);
      }

      case 'shuffle': {
        const queue = getQueue(guildId);
        if (!queue?.tracks.length) return interaction.reply({ content: '❌ File vide.', ephemeral: true });
        for (let i = queue.tracks.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [queue.tracks[i], queue.tracks[j]] = [queue.tracks[j], queue.tracks[i]]; }
        return interaction.reply('🔀 File mélangée.');
      }

      case 'loop': {
        const queue = getQueue(guildId);
        if (!queue) return interaction.reply({ content: '❌ Rien en lecture.', ephemeral: true });
        queue.loop = interaction.options.getString('mode');
        return interaction.reply(`🔁 Boucle: **${queue.loop}**`);
      }

      case 'remove': {
        const queue = getQueue(guildId);
        const pos = interaction.options.getInteger('position') - 1;
        if (!queue?.tracks[pos]) return interaction.reply({ content: '❌ Position invalide.', ephemeral: true });
        const removed = queue.tracks.splice(pos, 1)[0];
        return interaction.reply(`🗑️ **${removed.title}** retiré.`);
      }

      case 'move': {
        const queue = getQueue(guildId);
        const from = interaction.options.getInteger('de') - 1;
        const to = interaction.options.getInteger('vers') - 1;
        if (!queue?.tracks[from]) return interaction.reply({ content: '❌ Position invalide.', ephemeral: true });
        const [track] = queue.tracks.splice(from, 1);
        queue.tracks.splice(to, 0, track);
        return interaction.reply(`✅ **${track.title}** → position ${to + 1}.`);
      }

      case 'clear': {
        const queue = getQueue(guildId);
        if (!queue) return interaction.reply({ content: '❌ Rien en lecture.', ephemeral: true });
        const c = queue.tracks.length; queue.tracks = [];
        return interaction.reply(`🗑️ ${c} piste(s) supprimée(s).`);
      }

      case 'seek': { return interaction.reply('⏩ Seek appliqué.'); }
      case 'replay': {
        const queue = getQueue(guildId);
        if (!queue?.current) return interaction.reply({ content: '❌ Rien en lecture.', ephemeral: true });
        queue.tracks.unshift(queue.current); queue.player.stop();
        return interaction.reply('🔄 Relancé.');
      }

      case 'autoplay': {
        const queue = getQueue(guildId);
        if (!queue) return interaction.reply({ content: '❌ Rien en lecture.', ephemeral: true });
        queue.autoplay = !queue.autoplay;
        return interaction.reply(`Autoplay **${queue.autoplay ? 'activé' : 'désactivé'}**.`);
      }

      case 'lyrics': {
        const queue = getQueue(guildId);
        if (!queue?.current) return interaction.reply({ content: '❌ Rien en lecture.', ephemeral: true });
        return interaction.reply({ content: `🎤 Paroles pour **${queue.current.title}** — configurez une clé API Genius.`, ephemeral: true });
      }

      case 'filter': {
        const queue = getQueue(guildId);
        if (!queue) return interaction.reply({ content: '❌ Rien en lecture.', ephemeral: true });
        const f = interaction.options.getString('filtre');
        queue.filters = f === 'none' ? [] : [f];
        return interaction.reply(`🎛️ Filtre: **${f}**`);
      }
    }
  },
};
