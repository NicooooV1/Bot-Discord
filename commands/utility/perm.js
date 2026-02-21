// ===================================
// Ultra Suite — /permissions
// Vérifier les permissions d'un utilisateur
// ===================================

const { SlashCommandBuilder, EmbedBuilder, PermissionFlagsBits } = require('discord.js');

const PERM_NAMES = {
  Administrator: '⚙️ Administrateur',
  ManageGuild: '🏠 Gérer le serveur',
  ManageRoles: '🏷️ Gérer les rôles',
  ManageChannels: '📺 Gérer les salons',
  ManageMessages: '💬 Gérer les messages',
  BanMembers: '🔨 Bannir',
  KickMembers: '🦵 Expulser',
  MentionEveryone: '📢 Mentionner everyone',
  ManageWebhooks: '🔗 Gérer les webhooks',
  ManageNicknames: '✏️ Gérer les pseudos',
  ManageGuildExpressions: '😀 Gérer les emojis',
  ManageEvents: '📅 Gérer les events',
  ManageThreads: '🧵 Gérer les threads',
  ModerateMembers: '⏰ Timeout',
  ViewAuditLog: '📋 Logs d\'audit',
  ViewGuildInsights: '📊 Insights',
  MoveMembers: '🔀 Déplacer (vocal)',
  MuteMembers: '🔇 Mute (vocal)',
  DeafenMembers: '🔕 Sourd (vocal)',
  SendMessages: '💬 Envoyer des messages',
  EmbedLinks: '🔗 Liens intégrés',
  AttachFiles: '📎 Joindre des fichiers',
  AddReactions: '👍 Ajouter des réactions',
  UseExternalEmojis: '😀 Emojis externes',
  Connect: '🔊 Se connecter (vocal)',
  Speak: '🎤 Parler (vocal)',
};

module.exports = {
  module: 'utility',
  cooldown: 3,

  data: new SlashCommandBuilder()
    .setName('permissions')
    .setDescription('Voir les permissions d\'un utilisateur')
    .addUserOption((o) => o.setName('utilisateur').setDescription('L\'utilisateur'))
    .addChannelOption((o) => o.setName('salon').setDescription('Vérifier dans un salon spécifique')),

  async execute(interaction) {
    const user = interaction.options.getUser('utilisateur') || interaction.user;
    const channel = interaction.options.getChannel('salon');
    const member = await interaction.guild.members.fetch(user.id).catch(() => null);

    if (!member) return interaction.reply({ content: '❌ Membre introuvable.', ephemeral: true });

    let perms;
    let context;
    if (channel) {
      perms = channel.permissionsFor(member);
      context = `dans #${channel.name}`;
    } else {
      perms = member.permissions;
      context = 'sur le serveur';
    }

    const permArray = perms.toArray();
    const isAdmin = permArray.includes('Administrator');

    const granted = [];
    const denied = [];

    for (const [key, label] of Object.entries(PERM_NAMES)) {
      if (permArray.includes(key)) {
        granted.push(`✅ ${label}`);
      } else {
        denied.push(`❌ ${label}`);
      }
    }

    const embed = new EmbedBuilder()
      .setTitle(`🔑 Permissions de ${member.displayName} ${context}`)
      .setColor(isAdmin ? 0xE74C3C : 0x3498DB)
      .setThumbnail(user.displayAvatarURL())
      .setTimestamp();

    if (isAdmin) {
      embed.setDescription('⚠️ **Administrateur** — Toutes les permissions sont accordées.');
    }

    if (granted.length) embed.addFields({ name: `Accordées (${granted.length})`, value: granted.join('\n').substring(0, 1024) });
    if (denied.length && !isAdmin) embed.addFields({ name: `Refusées (${denied.length})`, value: denied.join('\n').substring(0, 1024) });

    return interaction.reply({ embeds: [embed], ephemeral: true });
  },
};
