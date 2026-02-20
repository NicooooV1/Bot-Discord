// ===================================
// Ultra Suite — /rpprofile
// Gestion des fiches personnage RP
// /rpprofile create | view | edit | delete | list
// ===================================

const { SlashCommandBuilder, EmbedBuilder } = require('discord.js');
const { getDb } = require('../../database');

module.exports = {
  module: 'rp',
  cooldown: 5,

  data: new SlashCommandBuilder()
    .setName('rpprofile')
    .setDescription('Gérer votre fiche personnage RP')
    .addSubcommand((sub) =>
      sub.setName('create').setDescription('Créer un personnage')
        .addStringOption((opt) => opt.setName('nom').setDescription('Nom du personnage').setRequired(true))
        .addStringOption((opt) => opt.setName('race').setDescription('Race / Espèce'))
        .addStringOption((opt) => opt.setName('classe').setDescription('Classe / Métier'))
        .addIntegerOption((opt) => opt.setName('age').setDescription('Âge').setMinValue(1))
        .addStringOption((opt) => opt.setName('description').setDescription('Description / Backstory'))
        .addStringOption((opt) => opt.setName('avatar').setDescription('URL de l\'image du personnage')))
    .addSubcommand((sub) =>
      sub.setName('view').setDescription('Voir un personnage')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre (défaut: vous)'))
        .addStringOption((opt) => opt.setName('nom').setDescription('Nom du personnage')))
    .addSubcommand((sub) =>
      sub.setName('edit').setDescription('Modifier votre personnage')
        .addStringOption((opt) => opt.setName('nom').setDescription('Nom du personnage à modifier').setRequired(true))
        .addStringOption((opt) => opt.setName('champ').setDescription('Champ à modifier').setRequired(true)
          .addChoices(
            { name: 'Nom', value: 'name' },
            { name: 'Race', value: 'race' },
            { name: 'Classe', value: 'class' },
            { name: 'Âge', value: 'age' },
            { name: 'Description', value: 'description' },
            { name: 'Avatar', value: 'avatar_url' },
          ))
        .addStringOption((opt) => opt.setName('valeur').setDescription('Nouvelle valeur').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('delete').setDescription('Supprimer un personnage')
        .addStringOption((opt) => opt.setName('nom').setDescription('Nom du personnage').setRequired(true)))
    .addSubcommand((sub) =>
      sub.setName('list').setDescription('Lister les personnages')
        .addUserOption((opt) => opt.setName('membre').setDescription('Membre (défaut: vous)'))),

  async execute(interaction) {
    const sub = interaction.options.getSubcommand();
    const guildId = interaction.guildId;
    const db = getDb();

    // === CREATE ===
    if (sub === 'create') {
      const name = interaction.options.getString('nom');
      const race = interaction.options.getString('race') || null;
      const rpClass = interaction.options.getString('classe') || null;
      const age = interaction.options.getInteger('age') || null;
      const description = interaction.options.getString('description') || null;
      const avatarUrl = interaction.options.getString('avatar') || null;

      // Max 5 personnages par user
      const count = await db('rp_characters')
        .where('guild_id', guildId).where('user_id', interaction.user.id)
        .count('id as count').first();
      if ((count?.count || 0) >= 5) {
        return interaction.reply({ content: '❌ Maximum 5 personnages par membre.', ephemeral: true });
      }

      await db('rp_characters').insert({
        guild_id: guildId, user_id: interaction.user.id,
        name, race, class: rpClass, age, description, avatar_url: avatarUrl,
        health: 100, level: 1, gold: 0,
      });

      return interaction.reply({ content: `✅ Personnage **${name}** créé !`, ephemeral: true });
    }

    // === VIEW ===
    if (sub === 'view') {
      const target = interaction.options.getUser('membre') || interaction.user;
      const charName = interaction.options.getString('nom');

      let character;
      if (charName) {
        character = await db('rp_characters').where('guild_id', guildId).where('user_id', target.id).where('name', charName).first();
      } else {
        character = await db('rp_characters').where('guild_id', guildId).where('user_id', target.id).first();
      }

      if (!character) return interaction.reply({ content: '❌ Personnage introuvable.', ephemeral: true });

      const embed = new EmbedBuilder()
        .setTitle(`🎭 ${character.name}`)
        .setColor(0x9B59B6)
        .setFooter({ text: `Joueur : ${target.tag}`, iconURL: target.displayAvatarURL() })
        .setTimestamp();

      if (character.avatar_url) embed.setThumbnail(character.avatar_url);

      const fields = [];
      if (character.race) fields.push({ name: '🧬 Race', value: character.race, inline: true });
      if (character.class) fields.push({ name: '⚔️ Classe', value: character.class, inline: true });
      if (character.age) fields.push({ name: '📅 Âge', value: String(character.age), inline: true });
      fields.push({ name: '❤️ PV', value: `${character.health}/100`, inline: true });
      fields.push({ name: '⭐ Niveau', value: String(character.level || 1), inline: true });
      fields.push({ name: '💰 Or', value: String(character.gold || 0), inline: true });
      if (character.description) fields.push({ name: '📖 Description', value: character.description.slice(0, 1024), inline: false });

      embed.addFields(...fields);
      return interaction.reply({ embeds: [embed] });
    }

    // === EDIT ===
    if (sub === 'edit') {
      const charName = interaction.options.getString('nom');
      const field = interaction.options.getString('champ');
      let value = interaction.options.getString('valeur');

      const character = await db('rp_characters')
        .where('guild_id', guildId).where('user_id', interaction.user.id).where('name', charName).first();
      if (!character) return interaction.reply({ content: '❌ Personnage introuvable.', ephemeral: true });

      // Gérer le champ âge
      if (field === 'age') value = parseInt(value, 10) || null;

      const updateField = field === 'name' ? 'name' : field;
      await db('rp_characters').where('id', character.id).update({ [updateField]: value });

      return interaction.reply({ content: `✅ **${charName}** — ${field} mis à jour.`, ephemeral: true });
    }

    // === DELETE ===
    if (sub === 'delete') {
      const charName = interaction.options.getString('nom');
      const deleted = await db('rp_characters')
        .where('guild_id', guildId).where('user_id', interaction.user.id).where('name', charName).del();
      if (!deleted) return interaction.reply({ content: '❌ Introuvable.', ephemeral: true });
      return interaction.reply({ content: `✅ **${charName}** supprimé.`, ephemeral: true });
    }

    // === LIST ===
    if (sub === 'list') {
      const target = interaction.options.getUser('membre') || interaction.user;
      const characters = await db('rp_characters')
        .where('guild_id', guildId).where('user_id', target.id);

      if (characters.length === 0) {
        return interaction.reply({ content: `ℹ️ **${target.username}** n'a aucun personnage.`, ephemeral: true });
      }

      const lines = characters.map((c) => {
        const info = [c.race, c.class, c.age ? `${c.age} ans` : null].filter(Boolean).join(' • ');
        return `🎭 **${c.name}** — Niv. ${c.level || 1} — ${info || '*Pas de détails*'}`;
      });

      const embed = new EmbedBuilder()
        .setTitle(`🎭 Personnages de ${target.username}`)
        .setDescription(lines.join('\n'))
        .setColor(0x9B59B6).setTimestamp();

      return interaction.reply({ embeds: [embed], ephemeral: true });
    }
  },
};