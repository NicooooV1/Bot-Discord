// ===================================
// Ultra Suite — Migration 002
// Tables étendues : tags, shop, role menus,
// mod notes, automod filters, security signals
// ===================================

exports.up = async function (knex) {
  // === Tags / FAQ ===
  await knex.schema.createTable('tags', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('name', 50).notNullable();
    t.string('title', 256);
    t.text('content').notNullable();
    t.string('author_id', 20);
    t.integer('uses').defaultTo(0);
    t.timestamps(true, true);

    t.unique(['guild_id', 'name']);
    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Boutique ===
  await knex.schema.createTable('shop_items', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('name', 100).notNullable();
    t.text('description');
    t.integer('price').notNullable();
    t.string('role_id', 20);
    t.integer('stock').defaultTo(-1); // -1 = illimité
    t.boolean('enabled').defaultTo(true);
    t.timestamps(true, true);

    t.unique(['guild_id', 'name']);
    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Menus de rôles ===
  await knex.schema.createTable('role_menus', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('name', 50).notNullable();
    t.string('description', 256);
    t.boolean('multiple').defaultTo(true);
    t.json('roles'); // [{id, label, emoji}]
    t.string('message_id', 20); // ID du message envoyé
    t.string('channel_id', 20);
    t.timestamps(true, true);

    t.unique(['guild_id', 'name']);
    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Notes de modération ===
  await knex.schema.createTable('mod_notes', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('target_id', 20).notNullable().index();
    t.string('author_id', 20).notNullable();
    t.text('content').notNullable();
    t.timestamps(true, true);

    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Filtres automod ===
  await knex.schema.createTable('automod_filters', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.enum('type', ['word', 'regex', 'domain']).notNullable();
    t.string('pattern', 256).notNullable();
    t.enum('action', ['delete', 'warn', 'log']).defaultTo('delete');
    t.boolean('enabled').defaultTo(true);
    t.timestamps(true, true);

    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Signaux de sécurité ===
  await knex.schema.createTable('security_signals', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('user_id', 20).notNullable().index();
    t.string('signal_type', 50).notNullable(); // SPAM, MASS_MENTION, RAID, etc.
    t.enum('severity', ['low', 'medium', 'high', 'critical']).defaultTo('low');
    t.json('evidence');
    t.string('action_taken', 50);
    t.timestamps(true, true);

    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Ajout colonnes manquantes à users ===
  await knex.schema.alterTable('users', (t) => {
    t.integer('daily_streak').defaultTo(0);
    t.datetime('last_daily');
    t.datetime('last_weekly');
  });

  // === Ajout colonnes manquantes à sanctions ===
  await knex.schema.alterTable('sanctions', (t) => {
    t.datetime('revoked_at');
    t.string('revoked_by', 20);
  });
};

exports.down = async function (knex) {
  // Retirer colonnes ajoutées
  await knex.schema.alterTable('sanctions', (t) => {
    t.dropColumn('revoked_at');
    t.dropColumn('revoked_by');
  });

  await knex.schema.alterTable('users', (t) => {
    t.dropColumn('daily_streak');
    t.dropColumn('last_daily');
    t.dropColumn('last_weekly');
  });

  // Supprimer les tables
  await knex.schema.dropTableIfExists('security_signals');
  await knex.schema.dropTableIfExists('automod_filters');
  await knex.schema.dropTableIfExists('mod_notes');
  await knex.schema.dropTableIfExists('role_menus');
  await knex.schema.dropTableIfExists('shop_items');
  await knex.schema.dropTableIfExists('tags');
};