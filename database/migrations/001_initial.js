// ===================================
// Ultra Suite — Migration initiale
// Toutes les tables du CDC v2.0
// MySQL (mysql2) — utf8mb4
//
// Architecture multi-serveur :
// Chaque table utilise guild_id pour séparer
// les données de chaque serveur Discord.
// Les FK vers guilds utilisent ON DELETE CASCADE
// pour nettoyer automatiquement quand une guild est supprimée.
// ===================================

/**
 * @param {import('knex').Knex} knex
 */
exports.up = async function (knex) {

  // ===================================
  // Guilds (1 ligne = 1 serveur Discord)
  // ===================================
  if (!(await knex.schema.hasTable('guilds'))) {
    await knex.schema.createTable('guilds', (t) => {
      t.string('id', 20).primary();
      t.string('name', 255).notNullable();
      t.string('owner_id', 20).notNullable();
      t.json('config');
      t.json('modules_enabled');
      t.json('theme');
      t.string('locale', 10).defaultTo('fr');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('updated_at').defaultTo(knex.fn.now());
      t.index('owner_id');
    });
  }

  // ===================================
  // Users (XP, économie, stats par serveur)
  // ===================================
  if (!(await knex.schema.hasTable('users'))) {
    await knex.schema.createTable('users', (t) => {
      t.increments('id').primary();
      t.string('user_id', 20).notNullable();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.integer('xp').unsigned().defaultTo(0);
      t.integer('level').unsigned().defaultTo(0);
      t.integer('reputation').defaultTo(0);
      t.integer('balance').defaultTo(0);
      t.integer('bank').defaultTo(0);
      t.integer('total_messages').unsigned().defaultTo(0);
      t.integer('voice_minutes').unsigned().defaultTo(0);
      t.json('inventory');
      t.datetime('last_daily');
      t.datetime('last_weekly');
      t.datetime('last_message_xp');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('updated_at').defaultTo(knex.fn.now());
      t.unique(['user_id', 'guild_id']);
      t.index(['guild_id', 'xp']);
      t.index(['guild_id', 'total_messages']);
      t.index(['guild_id', 'voice_minutes']);
      t.index(['guild_id', 'balance']);
    });
  }

  // ===================================
  // Sanctions (modération)
  // ===================================
  if (!(await knex.schema.hasTable('sanctions'))) {
    await knex.schema.createTable('sanctions', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.integer('case_number').unsigned().notNullable();
      t.string('type', 20).notNullable();
      t.string('target_id', 20).notNullable();
      t.string('moderator_id', 20).notNullable();
      t.text('reason');
      t.integer('duration').unsigned();
      t.boolean('active').defaultTo(true);
      t.json('evidence');
      t.datetime('expires_at');
      t.datetime('revoked_at');
      t.string('revoked_by', 20);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'case_number']);
      t.index(['guild_id', 'target_id', 'active']);
      t.index(['guild_id', 'moderator_id']);
      t.index(['active', 'expires_at']);
      t.index(['guild_id', 'type']);
    });
  }

  // ===================================
  // Logs (audit interne)
  // ===================================
  if (!(await knex.schema.hasTable('logs'))) {
    await knex.schema.createTable('logs', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('type', 50).notNullable();
      t.string('actor_id', 20);
      t.string('target_id', 20);
      t.string('target_type', 20);
      t.json('details');
      t.datetime('timestamp').defaultTo(knex.fn.now());
      t.index(['guild_id', 'type', 'timestamp']);
      t.index(['guild_id', 'actor_id', 'timestamp']);
      t.index(['guild_id', 'target_id']);
      t.index('timestamp');
    });
  }

  // ===================================
  // Tickets
  // ===================================
  if (!(await knex.schema.hasTable('tickets'))) {
    await knex.schema.createTable('tickets', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).unique();
      t.string('opener_id', 20).notNullable();
      t.string('category', 50).defaultTo('general');
      t.string('subject', 255);
      t.string('status', 20).defaultTo('open');
      t.string('priority', 20).defaultTo('normal');
      t.string('assignee_id', 20);
      t.json('form_answers');
      t.text('transcript', 'longtext');
      t.integer('rating').unsigned();
      t.text('rating_comment');
      t.string('closed_by', 20);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('closed_at');
      t.index(['guild_id', 'status']);
      t.index(['guild_id', 'opener_id', 'status']);
      t.index(['guild_id', 'assignee_id', 'status']);
    });
  }

  // ===================================
  // Candidatures
  // ===================================
  if (!(await knex.schema.hasTable('applications'))) {
    await knex.schema.createTable('applications', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('applicant_id', 20).notNullable();
      t.string('form_type', 50).defaultTo('default');
      t.json('answers');
      t.string('status', 20).defaultTo('pending');
      t.json('notes');
      t.string('reviewer_id', 20);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('updated_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'status']);
      t.index(['guild_id', 'applicant_id', 'status']);
    });
  }

  // ===================================
  // Reports / Signalements
  // ===================================
  if (!(await knex.schema.hasTable('reports'))) {
    await knex.schema.createTable('reports', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('reporter_id', 20).notNullable();
      t.string('target_type', 20).notNullable();
      t.string('target_id', 20).notNullable();
      t.text('reason').notNullable();
      t.string('severity', 20).defaultTo('normal');
      t.string('status', 20).defaultTo('pending');
      t.json('evidence');
      t.string('assignee_id', 20);
      t.text('resolution');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('resolved_at');
      t.index(['guild_id', 'status']);
      t.index(['guild_id', 'target_id']);
    });
  }

  // ===================================
  // Role Menus (self-roles)
  // ===================================
  if (!(await knex.schema.hasTable('role_menus'))) {
    await knex.schema.createTable('role_menus', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).notNullable();
      t.string('message_id', 20);
      t.string('type', 20).defaultTo('buttons');
      t.string('mode', 20).defaultTo('multi');
      t.string('title', 255).notNullable();
      t.text('description');
      t.json('options');
      t.index('guild_id');
    });
  }

  // ===================================
  // Temp Roles
  // ===================================
  if (!(await knex.schema.hasTable('temp_roles'))) {
    await knex.schema.createTable('temp_roles', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('role_id', 20).notNullable();
      t.datetime('expires_at').notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index('expires_at');
      t.index(['guild_id', 'user_id']);
    });
  }

  // ===================================
  // Events
  // ===================================
  if (!(await knex.schema.hasTable('events'))) {
    await knex.schema.createTable('events', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('title', 255).notNullable();
      t.text('description');
      t.string('channel_id', 20);
      t.string('creator_id', 20).notNullable();
      t.datetime('scheduled_at').notNullable();
      t.integer('duration').unsigned();
      t.json('participants');
      t.json('reminders');
      t.integer('max_participants').unsigned().defaultTo(0);
      t.string('status', 20).defaultTo('active');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'status', 'scheduled_at']);
    });
  }

  // ===================================
  // Shop Items
  // ===================================
  if (!(await knex.schema.hasTable('shop_items'))) {
    await knex.schema.createTable('shop_items', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('name', 255).notNullable();
      t.text('description');
      t.integer('price').unsigned().notNullable();
      t.string('type', 20).notNullable();
      t.json('metadata');
      t.integer('stock');
      t.boolean('enabled').defaultTo(true);
      t.index(['guild_id', 'enabled']);
    });
  }

  // ===================================
  // Inventaires
  // ===================================
  if (!(await knex.schema.hasTable('inventories'))) {
    await knex.schema.createTable('inventories', (t) => {
      t.increments('id').primary();
      t.string('user_id', 20).notNullable();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.integer('item_id').unsigned().notNullable()
        .references('id').inTable('shop_items').onDelete('CASCADE');
      t.integer('quantity').unsigned().defaultTo(1);
      t.datetime('acquired_at').defaultTo(knex.fn.now());
      t.unique(['user_id', 'guild_id', 'item_id']);
    });
  }

  // ===================================
  // Transactions
  // ===================================
  if (!(await knex.schema.hasTable('transactions'))) {
    await knex.schema.createTable('transactions', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('from_id', 20);
      t.string('to_id', 20).notNullable();
      t.integer('amount').notNullable();
      t.string('type', 30).notNullable();
      t.text('reason');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'created_at']);
      t.index(['guild_id', 'to_id', 'created_at']);
      t.index(['guild_id', 'from_id', 'created_at']);
    });
  }

  // ===================================
  // Tags / FAQ
  // ===================================
  if (!(await knex.schema.hasTable('tags'))) {
    await knex.schema.createTable('tags', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('name', 100).notNullable();
      t.json('content').notNullable();
      t.string('creator_id', 20).notNullable();
      t.integer('uses').unsigned().defaultTo(0);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'name']);
      t.index(['guild_id', 'uses']);
    });
  }

  // ===================================
  // Reminders
  // ===================================
  if (!(await knex.schema.hasTable('reminders'))) {
    await knex.schema.createTable('reminders', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20);
      t.string('user_id', 20).notNullable();
      t.string('channel_id', 20);
      t.text('message').notNullable();
      t.datetime('fire_at').notNullable();
      t.boolean('fired').defaultTo(false);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['fired', 'fire_at']);
      t.index(['user_id', 'fired']);
    });
  }

  // ===================================
  // Annonces planifiées
  // ===================================
  if (!(await knex.schema.hasTable('announcements'))) {
    await knex.schema.createTable('announcements', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).notNullable();
      t.json('content').notNullable();
      t.string('cron_expr', 100);
      t.datetime('next_run_at');
      t.boolean('enabled').defaultTo(true);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'enabled']);
      t.index(['enabled', 'next_run_at']);
    });
  }

  // ===================================
  // Custom Commands
  // ===================================
  if (!(await knex.schema.hasTable('custom_commands'))) {
    await knex.schema.createTable('custom_commands', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('name', 100).notNullable();
      t.json('response').notNullable();
      t.json('required_roles');
      t.integer('cooldown').unsigned().defaultTo(0);
      t.integer('uses').unsigned().defaultTo(0);
      t.boolean('enabled').defaultTo(true);
      t.string('creator_id', 20).notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'name']);
      t.index(['guild_id', 'enabled']);
    });
  }

  // ===================================
  // Tâches CRON persistées
  // ===================================
  if (!(await knex.schema.hasTable('cron_tasks'))) {
    await knex.schema.createTable('cron_tasks', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20)
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('type', 50).notNullable();
      t.string('cron_expr', 100);
      t.datetime('run_at');
      t.json('payload');
      t.boolean('active').defaultTo(true);
      t.datetime('last_run_at');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['active', 'run_at']);
      t.index(['guild_id', 'type']);
    });
  }

  // ===================================
  // Sécurité : signaux automod
  // ===================================
  if (!(await knex.schema.hasTable('security_signals'))) {
    await knex.schema.createTable('security_signals', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('signal_type', 30).notNullable();
      t.string('severity', 20).defaultTo('medium');
      t.json('evidence');
      t.string('action_taken', 100);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'signal_type', 'created_at']);
      t.index(['guild_id', 'user_id']);
    });
  }

  // ===================================
  // Trust Scores
  // ===================================
  if (!(await knex.schema.hasTable('trust_scores'))) {
    await knex.schema.createTable('trust_scores', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.integer('score').defaultTo(0);
      t.json('factors');
      t.datetime('calculated_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'user_id']);
    });
  }

  // ===================================
  // Onboarding
  // ===================================
  if (!(await knex.schema.hasTable('onboarding_states'))) {
    await knex.schema.createTable('onboarding_states', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('step', 30).defaultTo('pending');
      t.json('answers');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('verified_at');
      t.unique(['guild_id', 'user_id']);
    });
  }

  // ===================================
  // Permission Rules
  // ===================================
  if (!(await knex.schema.hasTable('permission_rules'))) {
    await knex.schema.createTable('permission_rules', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('scope_type', 20).notNullable();
      t.string('scope_id', 20).notNullable();
      t.string('command_key', 100).notNullable();
      t.string('effect', 10).notNullable();
      t.integer('priority').defaultTo(0);
      t.index(['guild_id', 'command_key']);
      t.index(['guild_id', 'scope_type', 'scope_id']);
    });
  }

  // ===================================
  // Daily Metrics
  // ===================================
  if (!(await knex.schema.hasTable('daily_metrics'))) {
    await knex.schema.createTable('daily_metrics', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.date('date').notNullable();
      t.integer('messages').unsigned().defaultTo(0);
      t.integer('active_users').unsigned().defaultTo(0);
      t.integer('new_members').unsigned().defaultTo(0);
      t.integer('left_members').unsigned().defaultTo(0);
      t.integer('voice_minutes').unsigned().defaultTo(0);
      t.integer('commands_used').unsigned().defaultTo(0);
      t.integer('tickets_opened').unsigned().defaultTo(0);
      t.integer('sanctions_issued').unsigned().defaultTo(0);
      t.unique(['guild_id', 'date']);
      t.index(['guild_id', 'date']);
    });
  }

  // ===================================
  // Temp Voice Channels
  // ===================================
  if (!(await knex.schema.hasTable('temp_voice_channels'))) {
    await knex.schema.createTable('temp_voice_channels', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).notNullable().unique();
      t.string('owner_id', 20).notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'owner_id']);
    });
  }

  // ===================================
  // Voice Sessions
  // ===================================
  if (!(await knex.schema.hasTable('voice_sessions'))) {
    await knex.schema.createTable('voice_sessions', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('channel_id', 20).notNullable();
      t.datetime('joined_at').defaultTo(knex.fn.now());
      t.datetime('left_at');
      t.integer('duration').unsigned();
      t.index(['guild_id', 'user_id', 'left_at']);
      t.index(['guild_id', 'joined_at']);
    });
  }

  // ===================================
  // RP : Fiches personnages
  // ===================================
  if (!(await knex.schema.hasTable('rp_characters'))) {
    await knex.schema.createTable('rp_characters', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('first_name', 100).notNullable();
      t.string('last_name', 100).defaultTo('');
      t.integer('age').unsigned();
      t.text('description');
      t.text('photo_url');
      t.string('status', 20).defaultTo('active');
      t.json('inventory');
      t.json('record');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'user_id']);
      t.index(['guild_id', 'status']);
    });
  }

  // ===================================
  // Intégrations externes
  // ===================================
  if (!(await knex.schema.hasTable('integrations'))) {
    await knex.schema.createTable('integrations', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('provider', 50).notNullable();
      t.string('channel_id', 20).notNullable();
      t.json('settings');
      t.boolean('enabled').defaultTo(true);
      t.string('cursor', 255);
      t.datetime('updated_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'provider']);
    });
  }

  // ===================================
  // AutoMod filters
  // ===================================
  if (!(await knex.schema.hasTable('automod_filters'))) {
    await knex.schema.createTable('automod_filters', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('type', 20).notNullable();
      t.text('pattern').notNullable();
      t.string('action', 20).defaultTo('delete');
      t.boolean('enabled').defaultTo(true);
      t.index(['guild_id', 'type', 'enabled']);
    });
  }
};

/**
 * Rollback : supprime toutes les tables dans l'ordre inverse
 * (respect des FK)
 *
 * @param {import('knex').Knex} knex
 */
exports.down = function (knex) {
  return knex.schema
    .dropTableIfExists('automod_filters')
    .dropTableIfExists('integrations')
    .dropTableIfExists('rp_characters')
    .dropTableIfExists('voice_sessions')
    .dropTableIfExists('temp_voice_channels')
    .dropTableIfExists('daily_metrics')
    .dropTableIfExists('permission_rules')
    .dropTableIfExists('onboarding_states')
    .dropTableIfExists('trust_scores')
    .dropTableIfExists('security_signals')
    .dropTableIfExists('cron_tasks')
    .dropTableIfExists('custom_commands')
    .dropTableIfExists('announcements')
    .dropTableIfExists('reminders')
    .dropTableIfExists('tags')
    .dropTableIfExists('transactions')
    .dropTableIfExists('inventories')
    .dropTableIfExists('shop_items')
    .dropTableIfExists('events')
    .dropTableIfExists('temp_roles')
    .dropTableIfExists('role_menus')
    .dropTableIfExists('reports')
    .dropTableIfExists('applications')
    .dropTableIfExists('tickets')
    .dropTableIfExists('logs')
    .dropTableIfExists('sanctions')
    .dropTableIfExists('users')
    .dropTableIfExists('guilds');
};