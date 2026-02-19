const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder, ChannelType } = require('discord.js');
const { modLog, COLORS } = require('../../utils/logger');
const { errorReply } = require('../../utils/helpers');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('lock')
    .setDescription('🔒 Verrouiller / Déverrouiller un salon')
    .addSubcommand(sub =>
      sub.setName('on')
        .setDescription('🔒 Verrouiller le salon')
        .addChannelOption(opt =>
          opt.setName('salon')
            .setDescription('Le salon à verrouiller (par défaut: salon actuel)')
            .addChannelTypes(ChannelType.GuildText)
        )
        .addStringOption(opt => opt.setName('raison').setDescription('Raison du verrouillage'))
    )
    .addSubcommand(sub =>
      sub.setName('off')
        .setDescription('🔓 Déverrouiller le salon')
        .addChannelOption(opt =>
          opt.setName('salon')
            .setDescription('Le salon à déverrouiller (par défaut: salon actuel)')
            .addChannelTypes(ChannelType.GuildText)
        )
        .addStringOption(opt => opt.setName('raison').setDescription('Raison du déverrouillage'))
    )
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageChannels),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const channel = interaction.options.getChannel('salon') || interaction.channel;
    const reason = interaction.options.getString('raison') || 'Aucune raison spécifiée';
    const everyone = interaction.guild.roles.everyone;

    try {
      if (sub === 'on') {
        // Verrouiller
        await channel.permissionOverwrites.edit(everyone, {
          SendMessages: false,
          AddReactions: false,
          CreatePublicThreads: false,
        });

        const embed = new EmbedBuilder()
          .setTitle('🔒 Salon verrouillé')
          .setColor(COLORS.RED)
          .setDescription(`Ce salon a été verrouillé par ${interaction.user}.`)
          .addFields({ name: '📝 Raison', value: reason })
          .setTimestamp();

        await channel.send({ embeds: [embed] });

        await modLog(interaction.guild, {
          action: 'Salon verrouillé',
          moderator: interaction.user,
          target: { toString: () => channel.toString(), id: channel.id, displayAvatarURL: () => null },
          reason,
          color: COLORS.RED,
        });

        if (channel.id !== interaction.channel.id) {
          await interaction.reply({ content: `✅ ${channel} a été verrouillé.`, ephemeral: true });
        } else {
          await interaction.reply({ content: '✅ Salon verrouillé.', ephemeral: true });
        }

      } else {
        // Déverrouiller
        await channel.permissionOverwrites.edit(everyone, {
          SendMessages: null,
          AddReactions: null,
          CreatePublicThreads: null,
        });

        const embed = new EmbedBuilder()
          .setTitle('🔓 Salon déverrouillé')
          .setColor(COLORS.GREEN)
          .setDescription(`Ce salon a été déverrouillé par ${interaction.user}.`)
          .addFields({ name: '📝 Raison', value: reason })
          .setTimestamp();

        await channel.send({ embeds: [embed] });

        await modLog(interaction.guild, {
          action: 'Salon déverrouillé',
          moderator: interaction.user,
          target: { toString: () => channel.toString(), id: channel.id, displayAvatarURL: () => null },
          reason,
          color: COLORS.GREEN,
        });

        if (channel.id !== interaction.channel.id) {
          await interaction.reply({ content: `✅ ${channel} a été déverrouillé.`, ephemeral: true });
        } else {
          await interaction.reply({ content: '✅ Salon déverrouillé.', ephemeral: true });
        }
      }
    } catch (error) {
      console.error('[LOCK]', error);
      await interaction.reply(errorReply('❌ Impossible de modifier les permissions du salon.'));
    }
  },
};
