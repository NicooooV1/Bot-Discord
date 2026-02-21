// ===================================
// Ultra Suite — /emojiinfo + /emojilist
// Informations sur les emojis
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');

module.exports = {
  module: 'utility',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('emoji')
    .setDescription('Gérer les emojis du serveur')
    .addSubcommand((s) =>
      s.setName('info').setDescription('Informations sur un emoji')
        .addStringOption((o) => o.setName('emoji').setDescription('L\'emoji').setRequired(true)),
    )
    .addSubcommand((s) =>
      s.setName('list').setDescription('Lister tous les emojis'),
    )
    .addSubcommand((s) =>
      s.setName('steal').setDescription('Copier un emoji depuis un autre serveur')
        .addStringOption((o) => o.setName('emoji').setDescription('L\'emoji à copier').setRequired(true))
        .addStringOption((o) => o.setName('nom').setDescription('Nouveau nom')),
    ),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();

    switch (sub) {
      case 'info': {
        const input = interaction.options.getString('emoji');
        const match = input.match(/<(a?):(\w+):(\d+)>/);

        if (!match) return interaction.reply({ content: '❌ Emoji personnalisé invalide.', ephemeral: true });

        const [, animated, name, id] = match;
        const url = `https://cdn.discordapp.com/emojis/${id}.${animated ? 'gif' : 'png'}?size=256`;

        const embed = new EmbedBuilder()
          .setTitle(`😀 Emoji — ${name}`)
          .setColor(0x3498DB)
          .setThumbnail(url)
          .addFields(
            { name: '🆔 ID', value: id, inline: true },
            { name: '📝 Nom', value: name, inline: true },
            { name: '✨ Animé', value: animated ? '✅' : '❌', inline: true },
            { name: '🔗 URL', value: `[Lien](${url})`, inline: true },
            { name: '💬 Utilisation', value: `\`<${animated ? 'a' : ''}:${name}:${id}>\``, inline: false },
          );

        return interaction.reply({ embeds: [embed] });
      }

      case 'list': {
        const emojis = interaction.guild.emojis.cache;
        const animated = emojis.filter((e) => e.animated);
        const statics = emojis.filter((e) => !e.animated);

        const embed = new EmbedBuilder()
          .setTitle(`😀 Emojis de ${interaction.guild.name}`)
          .setColor(0x3498DB)
          .addFields(
            { name: `Statiques (${statics.size})`, value: statics.size ? statics.map((e) => `${e}`).join(' ').substring(0, 1024) : 'Aucun' },
            { name: `Animés (${animated.size})`, value: animated.size ? animated.map((e) => `${e}`).join(' ').substring(0, 1024) : 'Aucun' },
          )
          .setFooter({ text: `Total: ${emojis.size} emojis` });

        return interaction.reply({ embeds: [embed] });
      }

      case 'steal': {
        if (!interaction.member.permissions.has('ManageGuildExpressions')) {
          return interaction.reply({ content: '❌ Permission ManageGuildExpressions requise.', ephemeral: true });
        }

        const input = interaction.options.getString('emoji');
        const name = interaction.options.getString('nom');
        const match = input.match(/<(a?):(\w+):(\d+)>/);

        if (!match) return interaction.reply({ content: '❌ Emoji personnalisé invalide.', ephemeral: true });

        const [, animated, emojiName, id] = match;
        const url = `https://cdn.discordapp.com/emojis/${id}.${animated ? 'gif' : 'png'}`;

        try {
          const emoji = await interaction.guild.emojis.create({
            attachment: url,
            name: name || emojiName,
          });
          return interaction.reply({ content: `✅ Emoji ${emoji} ajouté sous le nom \`${emoji.name}\` !` });
        } catch (e) {
          return interaction.reply({ content: `❌ Erreur : ${e.message}`, ephemeral: true });
        }
      }
    }
  },
};
