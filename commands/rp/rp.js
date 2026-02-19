// ===================================
// Ultra Suite — RP: /rp
// Jeu de rôle simplifié
// ===================================

const { SlashCommandBuilder } = require('discord.js');
const { getDb } = require('../../database');
const { successEmbed, errorEmbed, createEmbed } = require('../../utils/embeds');

module.exports = {
  module: 'rp',
  cooldown: 3,
  data: new SlashCommandBuilder()
    .setName('rp')
    .setDescription('Module jeu de rôle')
    .addSubcommand((sub) =>
      sub
        .setName('create')
        .setDescription('Crée ton personnage')
        .addStringOption((opt) => opt.setName('nom').setDescription('Nom du personnage').setRequired(true))
        .addStringOption((opt) => opt.setName('description').setDescription('Description'))
        .addStringOption((opt) => opt.setName('avatar').setDescription('URL de l\'avatar'))
    )
    .addSubcommand((sub) =>
      sub.setName('profile').setDescription('Voir un profil').addUserOption((opt) => opt.setName('user').setDescription('Utilisateur'))
    )
    .addSubcommand((sub) => sub.setName('delete').setDescription('Supprime ton personnage')),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const db = getDb();

    switch (sub) {
      case 'create': {
        const name = interaction.options.getString('nom');
        const description = interaction.options.getString('description') || '';
        const avatar = interaction.options.getString('avatar') || '';

        const existing = await db('rp_characters')
          .where({ guild_id: interaction.guild.id, user_id: interaction.user.id })
          .first();

        if (existing) {
          await db('rp_characters').where('id', existing.id).update({
            first_name: name,
            last_name: '',
            description,
            photo_url: avatar,
          });
          return interaction.reply({ embeds: [successEmbed(`✅ Personnage **${name}** mis à jour.`)], ephemeral: true });
        }

        await db('rp_characters').insert({
          guild_id: interaction.guild.id,
          user_id: interaction.user.id,
          first_name: name,
          last_name: '',
          description,
          photo_url: avatar,
        });

        return interaction.reply({ embeds: [successEmbed(`✅ Personnage **${name}** créé !`)], ephemeral: true });
      }

      case 'profile': {
        const user = interaction.options.getUser('user') || interaction.user;
        const character = await db('rp_characters')
          .where({ guild_id: interaction.guild.id, user_id: user.id })
          .first();

        if (!character) {
          return interaction.reply({ embeds: [errorEmbed('❌ Aucun personnage trouvé.')], ephemeral: true });
        }

        const embed = createEmbed('primary')
          .setTitle(`🎭 ${character.first_name} ${character.last_name}`.trim())
          .setDescription(character.description || '*Aucune description*')
          .addFields({ name: 'Joueur', value: `<@${user.id}>`, inline: true })
          .setFooter({ text: `Créé le ${new Date(character.created_at).toLocaleDateString('fr-FR')}` });

        if (character.photo_url) embed.setThumbnail(character.photo_url);

        return interaction.reply({ embeds: [embed] });
      }

      case 'delete': {
        const deleted = await db('rp_characters')
          .where({ guild_id: interaction.guild.id, user_id: interaction.user.id })
          .delete();

        if (!deleted) return interaction.reply({ embeds: [errorEmbed('❌ Aucun personnage à supprimer.')], ephemeral: true });

        return interaction.reply({ embeds: [successEmbed('✅ Personnage supprimé.')], ephemeral: true });
      }
    }
  },
};
