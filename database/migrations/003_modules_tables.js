// ===================================
// Ultra Suite — Migration 003
// Tables modules : applications, events,
// custom commands, RP, reminders, temp voice
// ===================================

exports.up = async function (knex) {
  // === Candidatures ===
  await knex.schema.createTable('applications', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('user_id', 20).notNullable().index();
    t.enum('status', ['PENDING', 'ACCEPTED', 'REJECTED']).defaultTo('PENDING').index();
    t.json('answers');
    t.string('reviewed_by', 20);
    t.datetime('reviewed_at');
    t.timestamps(true, true);

    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Événements serveur ===
  await knex.schema.createTable('server_events', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('title', 256).notNullable();
    t.text('description');
    t.datetime('event_date').notNullable().index();
    t.string('created_by', 20).notNullable();
    t.string('channel_id', 20);
    t.string('message_id', 20);
    t.integer('max_participants');
    t.json('participants'); // ["userId1", "userId2"]
    t.enum('status', ['ACTIVE', 'CANCELLED', 'COMPLETED']).defaultTo('ACTIVE');
    t.timestamps(true, true);

    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Commandes personnalisées ===
  await knex.schema.createTable('custom_commands', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('trigger', 50).notNullable();
    t.text('response').notNullable();
    t.boolean('use_embed').defaultTo(false);
    t.string('created_by', 20);
    t.integer('uses').defaultTo(0);
    t.timestamps(true, true);

    t.unique(['guild_id', 'trigger']);
    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Personnages RP ===
  await knex.schema.createTable('rp_characters', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('user_id', 20).notNullable().index();
    t.string('name', 100).notNullable();
    t.string('race', 50);
    t.string('class', 50);
    t.integer('age');
    t.text('description');
    t.string('avatar_url', 512);
    t.integer('health').defaultTo(100);
    t.integer('level').defaultTo(1);
    t.integer('gold').defaultTo(0);
    t.timestamps(true, true);

    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Inventaire RP ===
  await knex.schema.createTable('rp_inventory', (t) => {
    t.increments('id').primary();
    t.integer('character_id').unsigned().notNullable().index();
    t.string('name', 100).notNullable();
    t.string('description', 256);
    t.integer('quantity').defaultTo(1);
    t.timestamps(true, true);

    t.foreign('character_id').references('rp_characters.id').onDelete('CASCADE');
  });

  // === Rappels ===
  await knex.schema.createTable('reminders', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('user_id', 20).notNullable().index();
    t.string('channel_id', 20).notNullable();
    t.text('message').notNullable();
    t.datetime('fire_at').notNullable().index();
    t.boolean('fired').defaultTo(false);
    t.timestamps(true, true);

    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Salons vocaux temporaires ===
  await knex.schema.createTable('temp_voice_channels', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('channel_id', 20).notNullable().unique();
    t.string('owner_id', 20).notNullable();
    t.timestamps(true, true);

    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });

  // === Logs table (pour announce et autres) ===
  await knex.schema.createTable('logs', (t) => {
    t.increments('id').primary();
    t.string('guild_id', 20).notNullable().index();
    t.string('type', 50).notNullable().index();
    t.string('actor_id', 20);
    t.string('target_id', 20);
    t.string('target_type', 20); // user, channel, role, message
    t.json('details');
    t.timestamps(true, true);

    t.foreign('guild_id').references('guilds.guild_id').onDelete('CASCADE');
  });
};

exports.down = async function (knex) {
  await knex.schema.dropTableIfExists('logs');
  await knex.schema.dropTableIfExists('temp_voice_channels');
  await knex.schema.dropTableIfExists('reminders');
  await knex.schema.dropTableIfExists('rp_inventory');
  await knex.schema.dropTableIfExists('rp_characters');
  await knex.schema.dropTableIfExists('custom_commands');
  await knex.schema.dropTableIfExists('server_events');
  await knex.schema.dropTableIfExists('applications');
};