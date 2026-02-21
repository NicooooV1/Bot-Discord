// ===================================
// Ultra Suite — /giveaway
// Système de giveaways complet
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ActionRowBuilder, ButtonBuilder, ButtonStyle } = require('discord.js');
const configService = require('../../core/configService');
const { getDb } = require('../../database');

module.exports = {
  module: 'giveaway',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('giveaway')
    .setDescription('Gérer les giveaways')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addSubcommand((s) =>
      s.setName('start').setDescription('Lancer un giveaway')
        .addStringOption((o) => o.setName('prix').setDescription('Prix à gagner').setRequired(true))
        .addStringOption((o) => o.setName('duree').setDescription('Durée (ex: 1h, 1d, 7d)').setRequired(true))
        .addIntegerOption((o) => o.setName('gagnants').setDescription('Nombre de gagnants').setMinValue(1).setMaxValue(20))
        .addChannelOption((o) => o.setName('salon').setDescription('Salon pour le giveaway'))
        .addRoleOption((o) => o.setName('role_requis').setDescription('Rôle requis pour participer'))
        .addIntegerOption((o) => o.setName('niveau_requis').setDescription('Niveau minimum requis').setMinValue(0))
        .addIntegerOption((o) => o.setName('messages_requis').setDescription('Messages minimum requis').setMinValue(0)),
    )
    .addSubcommand((s) =>
      s.setName('end').setDescription('Terminer un giveaway maintenant')
        .addStringOption((o) => o.setName('message_id').setDescription('ID du message du giveaway').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('reroll').setDescription('Relancer les gagnants')
        .addStringOption((o) => o.setName('message_id').setDescription('ID du message du giveaway').setRequired(true))
        .addIntegerOption((o) => o.setName('gagnants').setDescription('Nombre de gagnants').setMinValue(1)),
    )
    .addSubcommand((s) =>
      s.setName('pause').setDescription('Mettre en pause / reprendre')
        .addStringOption((o) => o.setName('message_id').setDescription('ID du message').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('delete').setDescription('Supprimer un giveaway')
        .addStringOption((o) => o.setName('message_id').setDescription('ID du message').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('list').setDescription('Voir les giveaways actifs'),
    )
    .addSubcommand((s) =>
      s.setName('edit').setDescription('Modifier un giveaway')
        .addStringOption((o) => o.setName('message_id').setDescription('ID du message').setRequired(true))
        .addStringOption((o) => o.setName('prix').setDescription('Nouveau prix'))
        .addStringOption((o) => o.setName('duree').setDescription('Nouvelle durée'))
        .addIntegerOption((o) => o.setName('gagnants').setDescription('Nombre de gagnants').setMinValue(1)),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();
    const config = await configService.get(guildId);

    const parseDuration = (str) => {
      const match = str.match(/^(\d+)(m|h|d|w)$/);
      if (!match) return null;
      const val = parseInt(match[1]);
      const ms = { m: 60000, h: 3600000, d: 86400000, w: 604800000 };
      return val * (ms[match[2]] || 0);
    };

    const buildGiveawayEmbed = (giveaway, remaining) => {
      const embed = new EmbedBuilder()
        .setTitle('🎉 GIVEAWAY 🎉')
        .setDescription(`**${giveaway.prize}**`)
        .setColor(giveaway.status === 'ended' ? 0x95A5A6 : giveaway.status === 'paused' ? 0xF1C40F : 0xFF73FA)
        .addFields(
          { name: '🏆 Gagnant(s)', value: `${giveaway.winners_count}`, inline: true },
          { name: '👥 Participants', value: `${(JSON.parse(giveaway.participants || '[]')).length}`, inline: true },
          { name: '🎫 Organisateur', value: `<@${giveaway.host_id}>`, inline: true },
        )
        .setTimestamp(new Date(giveaway.ends_at));

      const reqs = giveaway.requirements ? JSON.parse(giveaway.requirements) : {};
      if (reqs.roles?.length || reqs.level || reqs.messages) {
        const reqText = [];
        if (reqs.roles?.length) reqText.push(`Rôle: ${reqs.roles.map((r) => `<@&${r}>`).join(', ')}`);
        if (reqs.level) reqText.push(`Niveau: ${reqs.level}+`);
        if (reqs.messages) reqText.push(`Messages: ${reqs.messages}+`);
        embed.addFields({ name: '📋 Conditions', value: reqText.join('\n') });
      }

      if (giveaway.status === 'paused') embed.addFields({ name: '⏸️', value: 'Giveaway en pause' });
      else if (giveaway.status !== 'ended') embed.addFields({ name: '⏰ Fin', value: `<t:${Math.floor(new Date(giveaway.ends_at).getTime() / 1000)}:R>` });

      return embed;
    };

    switch (sub) {
      case 'start': {
        const prize = interaction.options.getString('prix');
        const durationStr = interaction.options.getString('duree');
        const winnersCount = interaction.options.getInteger('gagnants') || 1;
        const channel = interaction.options.getChannel('salon') || interaction.channel;
        const requiredRole = interaction.options.getRole('role_requis');
        const requiredLevel = interaction.options.getInteger('niveau_requis') || 0;
        const requiredMessages = interaction.options.getInteger('messages_requis') || 0;

        const durationMs = parseDuration(durationStr);
        if (!durationMs) return interaction.reply({ content: '❌ Format de durée invalide. Utilisez: 1m, 1h, 1d, 1w', ephemeral: true });

        const endsAt = new Date(Date.now() + durationMs);
        const requirements = {};
        if (requiredRole) requirements.roles = [requiredRole.id];
        if (requiredLevel) requirements.level = requiredLevel;
        if (requiredMessages) requirements.messages = requiredMessages;

        const giveawayData = {
          guild_id: guildId,
          channel_id: channel.id,
          host_id: interaction.user.id,
          prize,
          winners_count: winnersCount,
          requirements: JSON.stringify(requirements),
          participants: JSON.stringify([]),
          status: 'active',
          ends_at: endsAt,
        };

        const [id] = await db('giveaways').insert(giveawayData);
        giveawayData.id = id;

        const embed = buildGiveawayEmbed(giveawayData);
        const row = new ActionRowBuilder().addComponents(
          new ButtonBuilder().setCustomId(`giveaway_join_${id}`).setLabel('🎉 Participer').setStyle(ButtonStyle.Primary),
          new ButtonBuilder().setCustomId(`giveaway_leave_${id}`).setLabel('Quitter').setStyle(ButtonStyle.Secondary),
        );

        const msg = await channel.send({ embeds: [embed], components: [row] });
        await db('giveaways').where({ id }).update({ message_id: msg.id });

        return interaction.reply({ content: `✅ Giveaway lancé dans ${channel} ! (ID: ${id})`, ephemeral: true });
      }

      case 'end': {
        const messageId = interaction.options.getString('message_id');
        const giveaway = await db('giveaways').where({ guild_id: guildId, message_id: messageId, status: 'active' }).first();
        if (!giveaway) return interaction.reply({ content: '❌ Giveaway introuvable ou déjà terminé.', ephemeral: true });

        const participants = JSON.parse(giveaway.participants || '[]');
        const winnersCount = Math.min(giveaway.winners_count, participants.length);
        const winners = [];
        const pool = [...participants];
        for (let i = 0; i < winnersCount; i++) {
          const idx = Math.floor(Math.random() * pool.length);
          winners.push(pool.splice(idx, 1)[0]);
        }

        await db('giveaways').where({ id: giveaway.id }).update({
          status: 'ended',
          winner_ids: JSON.stringify(winners),
          ended_at: new Date(),
        });

        const channel = await interaction.guild.channels.fetch(giveaway.channel_id).catch(() => null);
        if (channel) {
          const msg = await channel.messages.fetch(messageId).catch(() => null);
          if (msg) {
            const embed = buildGiveawayEmbed({ ...giveaway, status: 'ended', participants: giveaway.participants });
            embed.addFields({ name: '🏆 Gagnant(s)', value: winners.length ? winners.map((w) => `<@${w}>`).join(', ') : 'Aucun participant' });
            await msg.edit({ embeds: [embed], components: [] });
          }
          if (winners.length) {
            await channel.send({ content: `🎉 Félicitations ${winners.map((w) => `<@${w}>`).join(', ')} ! Vous avez gagné **${giveaway.prize}** !` });
          } else {
            await channel.send({ content: `😢 Personne n'a participé au giveaway **${giveaway.prize}**.` });
          }
        }

        return interaction.reply({ content: `✅ Giveaway terminé ! ${winners.length} gagnant(s).`, ephemeral: true });
      }

      case 'reroll': {
        const messageId = interaction.options.getString('message_id');
        const count = interaction.options.getInteger('gagnants') || 1;
        const giveaway = await db('giveaways').where({ guild_id: guildId, message_id: messageId, status: 'ended' }).first();
        if (!giveaway) return interaction.reply({ content: '❌ Giveaway terminé introuvable.', ephemeral: true });

        const participants = JSON.parse(giveaway.participants || '[]');
        const oldWinners = JSON.parse(giveaway.winner_ids || '[]');
        const pool = participants.filter((p) => !oldWinners.includes(p));

        const newWinners = [];
        for (let i = 0; i < Math.min(count, pool.length); i++) {
          const idx = Math.floor(Math.random() * pool.length);
          newWinners.push(pool.splice(idx, 1)[0]);
        }

        if (!newWinners.length) return interaction.reply({ content: '❌ Pas assez de participants pour un reroll.', ephemeral: true });

        await db('giveaways').where({ id: giveaway.id }).update({ winner_ids: JSON.stringify(newWinners) });

        const channel = await interaction.guild.channels.fetch(giveaway.channel_id).catch(() => null);
        if (channel) {
          await channel.send({ content: `🎉 Reroll ! Nouveau(x) gagnant(s) : ${newWinners.map((w) => `<@${w}>`).join(', ')} pour **${giveaway.prize}** !` });
        }

        return interaction.reply({ content: `✅ Reroll effectué ! ${newWinners.length} nouveau(x) gagnant(s).`, ephemeral: true });
      }

      case 'pause': {
        const messageId = interaction.options.getString('message_id');
        const giveaway = await db('giveaways').where({ guild_id: guildId, message_id: messageId }).whereIn('status', ['active', 'paused']).first();
        if (!giveaway) return interaction.reply({ content: '❌ Giveaway introuvable.', ephemeral: true });
        const newStatus = giveaway.status === 'active' ? 'paused' : 'active';
        await db('giveaways').where({ id: giveaway.id }).update({ status: newStatus });
        return interaction.reply({ content: `✅ Giveaway ${newStatus === 'paused' ? 'mis en pause' : 'repris'}.`, ephemeral: true });
      }

      case 'delete': {
        const messageId = interaction.options.getString('message_id');
        const giveaway = await db('giveaways').where({ guild_id: guildId, message_id: messageId }).first();
        if (!giveaway) return interaction.reply({ content: '❌ Giveaway introuvable.', ephemeral: true });

        const channel = await interaction.guild.channels.fetch(giveaway.channel_id).catch(() => null);
        if (channel) {
          const msg = await channel.messages.fetch(messageId).catch(() => null);
          if (msg) await msg.delete().catch(() => null);
        }
        await db('giveaways').where({ id: giveaway.id }).delete();

        return interaction.reply({ content: '✅ Giveaway supprimé.', ephemeral: true });
      }

      case 'list': {
        const giveaways = await db('giveaways').where({ guild_id: guildId }).whereIn('status', ['active', 'paused']).orderBy('ends_at', 'asc');
        if (!giveaways.length) return interaction.reply({ content: '📋 Aucun giveaway actif.', ephemeral: true });

        const embed = new EmbedBuilder()
          .setTitle('🎉 Giveaways actifs')
          .setColor(0xFF73FA)
          .setDescription(giveaways.map((g) => {
            const participants = JSON.parse(g.participants || '[]').length;
            const status = g.status === 'paused' ? '⏸️' : '🟢';
            return `${status} **${g.prize}** — ${participants} participant(s)\n> <#${g.channel_id}> • Fin: <t:${Math.floor(new Date(g.ends_at).getTime() / 1000)}:R>`;
          }).join('\n\n'))
          .setTimestamp();

        return interaction.reply({ embeds: [embed], ephemeral: true });
      }

      case 'edit': {
        const messageId = interaction.options.getString('message_id');
        const giveaway = await db('giveaways').where({ guild_id: guildId, message_id: messageId }).whereIn('status', ['active', 'paused']).first();
        if (!giveaway) return interaction.reply({ content: '❌ Giveaway introuvable.', ephemeral: true });

        const updates = {};
        const prize = interaction.options.getString('prix');
        const durationStr = interaction.options.getString('duree');
        const winners = interaction.options.getInteger('gagnants');
        if (prize) updates.prize = prize;
        if (winners) updates.winners_count = winners;
        if (durationStr) {
          const ms = parseDuration(durationStr);
          if (ms) updates.ends_at = new Date(Date.now() + ms);
        }

        await db('giveaways').where({ id: giveaway.id }).update(updates);

        // Update message
        const channel = await interaction.guild.channels.fetch(giveaway.channel_id).catch(() => null);
        if (channel) {
          const msg = await channel.messages.fetch(messageId).catch(() => null);
          if (msg) {
            const updated = { ...giveaway, ...updates };
            const embed = buildGiveawayEmbed(updated);
            await msg.edit({ embeds: [embed] });
          }
        }

        return interaction.reply({ content: '✅ Giveaway modifié.', ephemeral: true });
      }
    }
  },
};
