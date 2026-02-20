// ===================================
// Ultra Suite — Migration 004
// Tables pour le nouveau système de config :
//   - config_history  (audit log des changements)
//   - message_templates (templates custom par module)
// ===================================

exports.up = async function (knex) {
  // === Historique des changements de config ===
  if (!(await knex.schema.hasTable('config_history'))) {
    await knex.schema.createTable('config_history', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().index();
      t.string('module_id', 50).notNullable().index();
      t.string('config_key', 100).notNullable();
      t.text('old_value');
      t.text('new_value');
      t.string('changed_by', 20).notNullable(); // Discord user ID
      t.string('action', 20).notNullable().defaultTo('SET'); // SET, RESET, ENABLE, DISABLE, IMPORT
      t.timestamp('changed_at').defaultTo(knex.fn.now()).index();

      t.foreign('guild_id').references('id').inTable('guilds').onDelete('CASCADE');

      // Index composite pour requêtes fréquentes
      t.index(['guild_id', 'module_id']);
      t.index(['guild_id', 'changed_at']);
    });
  }

  // === Templates de messages personnalisés ===
  if (!(await knex.schema.hasTable('message_templates'))) {
    await knex.schema.createTable('message_templates', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().index();
      t.string('module_id', 50).notNullable();
      t.string('template_name', 100).notNullable(); // welcome, goodbye, levelUp, sanction, etc.
      t.text('content');                              // Texte brut avec variables
      t.json('embed_data');                           // Données embed JSON
      t.boolean('enabled').defaultTo(true);
      t.timestamps(true, true);

      t.foreign('guild_id').references('id').inTable('guilds').onDelete('CASCADE');

      // Un seul template par nom par module par guild
      t.unique(['guild_id', 'module_id', 'template_name']);
    });
  }
};

exports.down = async function (knex) {
  await knex.schema.dropTableIfExists('message_templates');
  await knex.schema.dropTableIfExists('config_history');
};
