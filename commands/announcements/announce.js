// ===================================
// Ultra Suite — /announce
// Envoyer une annonce dans un channel
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits, ChannelType } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'announcements',
  adminOnly: true,
  cooldown: 10,

  data: new SlashCommandBuilder()
    .setName('announce')
    .setDescription('Envoyer une annonce')
    .setDefaultMemberPermissions(PermissionFlagsBits.Administrator)
    .addStringOption((opt) => opt.setName('message').setDescription('Contenu de l\'annonce').setRequired(true))
    .addChannelOption((opt) => opt.setName('channel').setDescription('Channel cible').setRequired(true).addChannelTypes(ChannelType.GuildText, ChannelType.GuildAnnouncement))
    .addStringOption((opt) => opt.setName('titre').setDescription('Titre de l\'annonce'))
    .addStringOption((opt) => opt.setName('couleur').setDescription('Couleur hex (ex: #FFD700)'))
    .addStringOption((opt) => opt.setName('image').setDescription('URL de l\'image'))
    .addStringOption((opt) => opt.setName('ping').setDescription('Rôle ou @everyone à mentionner')
      .addChoices(
        { name: '@everyone', value: 'everyone' },
        { name: '@here', value: 'here' },
        { name: 'Aucun ping', value: 'none' },
      ))
    .addBooleanOption((opt) => opt.setName('crosspost').setDescription('Publier l\'annonce (channels d\'annonces uniquement)')),

  async execute(interaction) {
    const message = interaction.options.getString('message');
    const channel = interaction.options.getChannel('channel');
    const titre = interaction.options.getString('titre') || '📢 Annonce';
    const couleur = interaction.options.getString('couleur');
    const image = interaction.options.getString('image');
    const ping = interaction.options.getString('ping') || 'none';
    const crosspost = interaction.options.getBoolean('crosspost') || false;

    await interaction.deferReply({ ephemeral: true });

    let color = 0xFEE75C;
    if (couleur) {
      const parsed = parseInt(couleur.replace('#', ''), 16);
      if (!isNaN(parsed)) color = parsed;
    }

    const embed = new EmbedBuilder()
      .setTitle(titre)
      .setDescription(message)
      .setColor(color)
      .setFooter({ text: `Annonce par ${interaction.user.tag}`, iconURL: interaction.user.displayAvatarURL() })
      .setTimestamp();

    if (image) embed.setImage(image);

    // Construire le contenu ping
    let content = '';
    if (ping === 'everyone') content = '@everyone';
    else if (ping === 'here') content = '@here';

    try {
      const sent = await channel.send({
        content: content || undefined,
        embeds: [embed],
      });

      // Crosspost si c'est un channel d'annonces
      if (crosspost && channel.type === ChannelType.GuildAnnouncement) {
        await sent.crosspost().catch(() => {});
      }

      // Log en DB
      try {
        const db = getDb();
        await db('logs').insert({
          guild_id: interaction.guildId,
          type: 'ANNOUNCEMENT',
          actor_id: interaction.user.id,
          target_id: channel.id,
          target_type: 'channel',
          details: JSON.stringify({ title: titre, content: message.slice(0, 200) }),
        });
      } catch { /* Non critique */ }

      return interaction.editReply({
        content: `✅ Annonce envoyée dans ${channel}.${crosspost ? ' (publiée)' : ''}`,
      });
    } catch {
      return interaction.editReply({ content: `❌ Impossible d'envoyer dans ${channel}.` });
    }
  },
};