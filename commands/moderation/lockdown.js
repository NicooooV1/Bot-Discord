// ===================================
// Ultra Suite — /lockdown
// Verrouiller/déverrouiller le serveur ou un salon
// ===================================

const { SlashCommandBuilder, PermissionFlagsBits, ChannelType, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'moderation',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('lockdown')
    .setDescription('Verrouiller ou déverrouiller des salons')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageChannels)
    .addSubcommand((s) =>
      s.setName('channel').setDescription('Verrouiller/déverrouiller un salon')
        .addChannelOption((o) => o.setName('salon').setDescription('Salon (défaut: actuel)'))
        .addStringOption((o) => o.setName('raison').setDescription('Raison')),
    )
    .addSubcommand((s) =>
      s.setName('server').setDescription('Verrouiller/déverrouiller tout le serveur')
        .addStringOption((o) => o.setName('raison').setDescription('Raison')),
    )
    .addSubcommand((s) =>
      s.setName('category').setDescription('Verrouiller une catégorie entière')
        .addChannelOption((o) => o.setName('categorie').setDescription('Catégorie').setRequired(true))
        .addStringOption((o) => o.setName('raison').setDescription('Raison')),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const reason = interaction.options.getString('raison') || 'Lockdown';
    const guildId = interaction.guildId;
    const everyoneRole = interaction.guild.roles.everyone;

    await interaction.deferReply();

    const lockChannel = async (channel) => {
      const perms = channel.permissionOverwrites.cache.get(everyoneRole.id);
      const currentDeny = perms?.deny?.has('SendMessages');

      if (currentDeny) {
        // Unlock
        await channel.permissionOverwrites.edit(everyoneRole, { SendMessages: null }, { reason: `Unlock: ${reason}` });
        return false; // was locked, now unlocked
      } else {
        // Lock
        await channel.permissionOverwrites.edit(everyoneRole, { SendMessages: false }, { reason: `Lockdown: ${reason}` });
        return true; // was unlocked, now locked
      }
    };

    switch (sub) {
      case 'channel': {
        const channel = interaction.options.getChannel('salon') || interaction.channel;
        const locked = await lockChannel(channel);
        const embed = new EmbedBuilder()
          .setColor(locked ? 0xE74C3C : 0x2ECC71)
          .setTitle(locked ? '🔒 Salon verrouillé' : '🔓 Salon déverrouillé')
          .setDescription(`${channel} a été ${locked ? 'verrouillé' : 'déverrouillé'}.`)
          .addFields({ name: 'Raison', value: reason })
          .setTimestamp();
        return interaction.editReply({ embeds: [embed] });
      }

      case 'server': {
        const channels = interaction.guild.channels.cache.filter(
          (c) => c.type === ChannelType.GuildText || c.type === ChannelType.GuildVoice,
        );
        let locked = 0;
        let unlocked = 0;
        // Determine action: if more than half are unlocked, lock all; else unlock all
        const unlockedCount = channels.filter((c) => {
          const p = c.permissionOverwrites.cache.get(everyoneRole.id);
          return !p?.deny?.has('SendMessages');
        }).size;
        const shouldLock = unlockedCount > channels.size / 2;

        for (const [, channel] of channels) {
          try {
            if (shouldLock) {
              await channel.permissionOverwrites.edit(everyoneRole, { SendMessages: false }, { reason: `Server lockdown: ${reason}` });
              locked++;
            } else {
              await channel.permissionOverwrites.edit(everyoneRole, { SendMessages: null }, { reason: `Server unlock: ${reason}` });
              unlocked++;
            }
          } catch { /* skip channels we can't edit */ }
        }

        const embed = new EmbedBuilder()
          .setColor(shouldLock ? 0xE74C3C : 0x2ECC71)
          .setTitle(shouldLock ? '🔒 Serveur verrouillé' : '🔓 Serveur déverrouillé')
          .setDescription(shouldLock ? `${locked} salon(s) verrouillé(s).` : `${unlocked} salon(s) déverrouillé(s).`)
          .addFields({ name: 'Raison', value: reason })
          .setTimestamp();
        return interaction.editReply({ embeds: [embed] });
      }

      case 'category': {
        const category = interaction.options.getChannel('categorie');
        if (category.type !== ChannelType.GuildCategory) {
          return interaction.editReply({ content: '❌ Ce n\'est pas une catégorie.' });
        }
        const children = category.children.cache;
        let count = 0;
        for (const [, channel] of children) {
          try {
            await lockChannel(channel);
            count++;
          } catch { /* skip */ }
        }
        return interaction.editReply({ content: `🔒 ${count} salon(s) dans **${category.name}** ont été basculés.` });
      }
    }
  },
};
