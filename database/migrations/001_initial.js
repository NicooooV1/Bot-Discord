// ===================================
// Ultra Suite — Migration initiale
// Toutes les tables du CDC v2.0
// MySQL (mysql2) — utf8mb4
// ===================================

/**
 * @param {import('knex').Knex} knex
 */
exports.up = function (knex) {
  return knex.schema

    // ===================================
    // Guilds (config JSON par serveur)
    // ===================================
    .createTable('guilds', (t) => {
      t.string('id', 20).primary();             // Discord guild ID (snowflake)
      t.string('name', 255).notNullable();
      t.string('owner_id', 20).notNullable();
      t.json('config');                          // Toute la config du serveur
      t.json('modules_enabled');                 // { moderation: true, xp: false, ... }
      t.json('theme');                           // { primary: "#5865F2", ... }
      t.string('locale', 10).defaultTo('fr');
      t.string('api_token', 255);               // Hashé bcrypt — dashboard futur
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('updated_at').defaultTo(knex.fn.now());
    })

    // ===================================
    // Users (XP, économie, etc.)
    // ===================================
    .createTable('users', (t) => {
      t.increments('id').primary();
      t.string('user_id', 20).notNullable();    // Discord user ID
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.integer('xp').defaultTo(0);
      t.integer('level').defaultTo(0);
      t.integer('reputation').defaultTo(0);
      t.integer('balance').defaultTo(0);
      t.integer('bank').defaultTo(0);
      t.integer('total_messages').defaultTo(0);
      t.integer('voice_minutes').defaultTo(0);
      t.json('inventory');                       // []
      t.datetime('last_daily');
      t.datetime('last_weekly');
      t.datetime('last_message_xp');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('updated_at').defaultTo(knex.fn.now());
      t.unique(['user_id', 'guild_id']);
    })

    // ===================================
    // Sanctions (modération)
    // ===================================
    .createTable('sanctions', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.integer('case_number').notNullable();
      t.string('type', 20).notNullable();       // WARN, TIMEOUT, KICK, BAN, SOFTBAN, TEMPBAN, UNBAN, UNMUTE
      t.string('target_id', 20).notNullable();
      t.string('moderator_id', 20).notNullable();
      t.text('reason');
      t.integer('duration');                     // Secondes (pour timeout/tempban)
      t.boolean('active').defaultTo(true);
      t.json('evidence');                        // { links, messageIds }
      t.datetime('expires_at');
      t.datetime('revoked_at');
      t.string('revoked_by', 20);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'case_number']);
      t.index(['guild_id', 'target_id']);
      t.index(['guild_id', 'moderator_id']);
      t.index(['guild_id', 'active']);
    })

    // ===================================
    // Logs (audit interne)
    // ===================================
    .createTable('logs', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('type', 50).notNullable();       // MESSAGE_DELETE, MEMBER_JOIN, MOD_ACTION, CONFIG_CHANGE, etc.
      t.string('actor_id', 20);
      t.string('target_id', 20);
      t.string('target_type', 20);              // user, channel, role, message
      t.json('details');                         // {}
      t.datetime('timestamp').defaultTo(knex.fn.now());
      t.index(['guild_id', 'type', 'timestamp']);
      t.index(['guild_id', 'actor_id']);
      t.index(['guild_id', 'target_id']);
    })

    // ===================================
    // Tickets
    // ===================================
    .createTable('tickets', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).unique();
      t.string('opener_id', 20).notNullable();
      t.string('category', 50).defaultTo('general');
      t.string('subject', 255);
      t.string('status', 20).defaultTo('open'); // open, in_progress, waiting, resolved, closed
      t.string('priority', 20).defaultTo('normal'); // low, normal, high, urgent
      t.string('assignee_id', 20);
      t.json('form_answers');
      t.text('transcript');                      // Contenu texte du transcript
      t.integer('rating');                       // 1-5
      t.text('rating_comment');
      t.string('closed_by', 20);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('closed_at');
      t.index(['guild_id', 'status']);
      t.index(['guild_id', 'opener_id']);
    })

    // ===================================
    // Candidatures
    // ===================================
    .createTable('applications', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('applicant_id', 20).notNullable();
      t.string('form_type', 50).defaultTo('default');
      t.json('answers');                         // {}
      t.string('status', 20).defaultTo('pending'); // pending, reviewing, interview, accepted, rejected
      t.json('notes');                           // [{ staffId, note, date }]
      t.string('reviewer_id', 20);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('updated_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'status']);
    })

    // ===================================
    // Reports / Signalements
    // ===================================
    .createTable('reports', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('reporter_id', 20).notNullable();
      t.string('target_type', 20).notNullable(); // user, message
      t.string('target_id', 20).notNullable();
      t.text('reason').notNullable();
      t.string('severity', 20).defaultTo('normal'); // low, normal, high, critical
      t.string('status', 20).defaultTo('pending'); // pending, reviewing, resolved, dismissed
      t.json('evidence');                        // {}
      t.string('assignee_id', 20);
      t.text('resolution');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('resolved_at');
      t.index(['guild_id', 'status']);
    })

    // ===================================
    // Role Menus (self-roles)
    // ===================================
    .createTable('role_menus', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).notNullable();
      t.string('message_id', 20);
      t.string('type', 20).defaultTo('buttons'); // buttons, select, reactions
      t.string('mode', 20).defaultTo('multi');   // single, multi
      t.string('title', 255).notNullable();
      t.text('description');
      t.json('options');                         // [{ roleId, label, emoji, description }]
    })

    // ===================================
    // Temp Roles (rôles temporaires)
    // ===================================
    .createTable('temp_roles', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable();
      t.string('user_id', 20).notNullable();
      t.string('role_id', 20).notNullable();
      t.datetime('expires_at').notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index('expires_at');
    })

    // ===================================
    // Events (événements planifiés)
    // ===================================
    .createTable('events', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('title', 255).notNullable();
      t.text('description');
      t.string('channel_id', 20);
      t.string('creator_id', 20).notNullable();
      t.datetime('scheduled_at').notNullable();
      t.integer('duration');                     // minutes
      t.json('participants');                    // []
      t.json('reminders');                       // [1440, 60, 15]
      t.integer('max_participants').unsigned();
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'scheduled_at']);
    })

    // ===================================
    // Shop Items
    // ===================================
    .createTable('shop_items', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('name', 255).notNullable();
      t.text('description');
      t.integer('price').notNullable();
      t.string('type', 20).notNullable();       // role, item, perk
      t.json('metadata');                        // { roleId, duration, ... }
      t.integer('stock');                        // null = illimité
      t.boolean('enabled').defaultTo(true);
    })

    // ===================================
    // Inventaires
    // ===================================
    .createTable('inventories', (t) => {
      t.increments('id').primary();
      t.string('user_id', 20).notNullable();
      t.string('guild_id', 20).notNullable();
      t.integer('item_id').unsigned().notNullable().references('id').inTable('shop_items').onDelete('CASCADE');
      t.integer('quantity').defaultTo(1);
      t.datetime('acquired_at').defaultTo(knex.fn.now());
      t.unique(['user_id', 'guild_id', 'item_id']);
    })

    // ===================================
    // Transactions (ledger économie)
    // ===================================
    .createTable('transactions', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable();
      t.string('from_id', 20);                  // null = système
      t.string('to_id', 20).notNullable();
      t.integer('amount').notNullable();
      t.string('type', 30).notNullable();       // earn, spend, transfer, tax, reward, daily, weekly
      t.text('reason');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'created_at']);
    })

    // ===================================
    // Tags / FAQ
    // ===================================
    .createTable('tags', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('name', 100).notNullable();
      t.json('content').notNullable();           // { text, embeds }
      t.string('creator_id', 20).notNullable();
      t.integer('uses').defaultTo(0);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'name']);
    })

    // ===================================
    // Reminders
    // ===================================
    .createTable('reminders', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20);
      t.string('user_id', 20).notNullable();
      t.string('channel_id', 20);               // null = DM
      t.text('message').notNullable();
      t.datetime('fire_at').notNullable();
      t.boolean('fired').defaultTo(false);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['fire_at', 'fired']);
    })

    // ===================================
    // Annonces planifiées
    // ===================================
    .createTable('announcements', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).notNullable();
      t.json('content').notNullable();           // { text, embeds }
      t.string('cron_expr', 100);
      t.datetime('next_run_at');
      t.boolean('enabled').defaultTo(true);
      t.datetime('created_at').defaultTo(knex.fn.now());
    })

    // ===================================
    // Custom Commands (staff)
    // ===================================
    .createTable('custom_commands', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('name', 100).notNullable();
      t.json('response').notNullable();          // { text, embeds, ephemeral }
      t.json('required_roles');                  // []
      t.integer('cooldown').defaultTo(0);        // secondes
      t.integer('uses').defaultTo(0);
      t.boolean('enabled').defaultTo(true);
      t.string('creator_id', 20).notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'name']);
    })

    // ===================================
    // Tâches CRON persistées
    // ===================================
    .createTable('cron_tasks', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20);
      t.string('type', 50).notNullable();       // tempban_expire, temprole_expire, reminder, announcement
      t.string('cron_expr', 100);               // Expression cron (récurrent)
      t.datetime('run_at');                      // Exécution unique
      t.json('payload');                         // {}
      t.boolean('active').defaultTo(true);
      t.datetime('last_run_at');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['run_at', 'active']);
      t.index('type');
    })

    // ===================================
    // Sécurité : signaux & trust scores
    // ===================================
    .createTable('security_signals', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable();
      t.string('user_id', 20).notNullable();
      t.string('signal_type', 30).notNullable(); // SPAM, RAID, PHISHING, TOKEN_LEAK, MASS_MENTION
      t.string('severity', 20).defaultTo('medium'); // low, medium, high, critical
      t.json('evidence');                        // {}
      t.string('action_taken', 100);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'signal_type', 'created_at']);
    })

    .createTable('trust_scores', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable();
      t.string('user_id', 20).notNullable();
      t.integer('score').defaultTo(0);
      t.json('factors');                         // {}
      t.datetime('calculated_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'user_id']);
    })

    // ===================================
    // Onboarding
    // ===================================
    .createTable('onboarding_states', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable();
      t.string('user_id', 20).notNullable();
      t.string('step', 30).defaultTo('pending'); // pending, rules_accepted, quiz_passed, verified
      t.json('answers');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('verified_at');
      t.unique(['guild_id', 'user_id']);
    })

    // ===================================
    // Permissions personnalisées
    // ===================================
    .createTable('permission_rules', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('scope_type', 20).notNullable();  // role, user, channel
      t.string('scope_id', 20).notNullable();
      t.string('command_key', 100).notNullable(); // "ban", "ticket.close", "*"
      t.string('effect', 10).notNullable();      // allow, deny
      t.integer('priority').defaultTo(0);
      t.index(['guild_id', 'command_key']);
    })

    // ===================================
    // Daily Metrics (analytics)
    // ===================================
    .createTable('daily_metrics', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable();
      t.date('date').notNullable();
      t.integer('messages').defaultTo(0);
      t.integer('active_users').defaultTo(0);
      t.integer('new_members').defaultTo(0);
      t.integer('left_members').defaultTo(0);
      t.integer('voice_minutes').defaultTo(0);
      t.integer('commands_used').defaultTo(0);
      t.integer('tickets_opened').defaultTo(0);
      t.integer('sanctions_issued').defaultTo(0);
      t.unique(['guild_id', 'date']);
    })

    // ===================================
    // Temp Voice Channels
    // ===================================
    .createTable('temp_voice_channels', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable();
      t.string('channel_id', 20).notNullable().unique();
      t.string('owner_id', 20).notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());
    })

    // ===================================
    // Voice Sessions (statistiques)
    // ===================================
    .createTable('voice_sessions', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable();
      t.string('user_id', 20).notNullable();
      t.string('channel_id', 20).notNullable();
      t.datetime('joined_at').defaultTo(knex.fn.now());
      t.datetime('left_at');
      t.integer('duration');                     // secondes
      t.index(['guild_id', 'user_id']);
    })

    // ===================================
    // RP : Fiches personnages
    // ===================================
    .createTable('rp_characters', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable();
      t.string('user_id', 20).notNullable();
      t.string('first_name', 100).notNullable();
      t.string('last_name', 100).notNullable();
      t.integer('age');
      t.text('description');
      t.text('photo_url');
      t.string('status', 20).defaultTo('active'); // active, dead, archived
      t.json('inventory');                       // []
      t.json('record');                          // [] — Casier judiciaire RP
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'user_id']);
    })

    // ===================================
    // Intégrations externes
    // ===================================
    .createTable('integrations', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('provider', 50).notNullable();   // twitch, youtube, github, etc.
      t.string('channel_id', 20).notNullable(); // Discord channel destination
      t.json('settings');                        // {}
      t.boolean('enabled').defaultTo(true);
      t.string('cursor', 255);                  // Dernier event traité (polling)
      t.datetime('updated_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'provider']);
    })

    // ===================================
    // AutoMod filters custom
    // ===================================
    .createTable('automod_filters', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable();
      t.string('type', 20).notNullable();       // word, regex, domain, filetype
      t.text('pattern').notNullable();
      t.string('action', 20).defaultTo('delete'); // delete, warn, timeout, ban
      t.boolean('enabled').defaultTo(true);
      t.index(['guild_id', 'type']);
    });
};

/**
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
