// ===================================
// Ultra Suite — Migration initiale
// Toutes les tables du CDC v2.0
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
      t.text('id').primary();                    // Discord guild ID
      t.text('name').notNullable();
      t.text('owner_id').notNullable();
      t.json('config').defaultTo('{}');          // Toute la config du serveur
      t.json('modules_enabled').defaultTo('{}'); // { moderation: true, xp: false, ... }
      t.json('theme').defaultTo('{}');           // { primary: "#5865F2", ... }
      t.text('locale').defaultTo('fr');
      t.text('api_token');                       // Hashé bcrypt — dashboard futur
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('updated_at').defaultTo(knex.fn.now());
    })

    // ===================================
    // Users (XP, économie, etc.)
    // ===================================
    .createTable('users', (t) => {
      t.increments('id').primary();
      t.text('user_id').notNullable();           // Discord user ID
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.integer('xp').defaultTo(0);
      t.integer('level').defaultTo(0);
      t.integer('reputation').defaultTo(0);
      t.integer('balance').defaultTo(0);
      t.integer('bank').defaultTo(0);
      t.integer('total_messages').defaultTo(0);
      t.integer('voice_minutes').defaultTo(0);
      t.json('inventory').defaultTo('[]');
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
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.integer('case_number').notNullable();
      t.text('type').notNullable();              // WARN, TIMEOUT, KICK, BAN, SOFTBAN, TEMPBAN, UNBAN, UNMUTE
      t.text('target_id').notNullable();
      t.text('moderator_id').notNullable();
      t.text('reason');
      t.integer('duration');                     // Secondes (pour timeout/tempban)
      t.boolean('active').defaultTo(true);
      t.json('evidence');                        // { links, messageIds }
      t.datetime('expires_at');
      t.datetime('revoked_at');
      t.text('revoked_by');
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
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('type').notNullable();              // MESSAGE_DELETE, MEMBER_JOIN, MOD_ACTION, CONFIG_CHANGE, etc.
      t.text('actor_id');
      t.text('target_id');
      t.text('target_type');                     // user, channel, role, message
      t.json('details').defaultTo('{}');
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
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('channel_id').unique();
      t.text('opener_id').notNullable();
      t.text('category').defaultTo('general');
      t.text('subject');
      t.text('status').defaultTo('open');        // open, in_progress, waiting, resolved, closed
      t.text('priority').defaultTo('normal');     // low, normal, high, urgent
      t.text('assignee_id');
      t.json('form_answers');
      t.text('transcript');                      // Contenu texte du transcript
      t.integer('rating');                       // 1-5
      t.text('rating_comment');
      t.text('closed_by');
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
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('applicant_id').notNullable();
      t.text('form_type').defaultTo('default');
      t.json('answers').defaultTo('{}');
      t.text('status').defaultTo('pending');     // pending, reviewing, interview, accepted, rejected
      t.json('notes').defaultTo('[]');           // [{ staffId, note, date }]
      t.text('reviewer_id');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('updated_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'status']);
    })

    // ===================================
    // Reports / Signalements
    // ===================================
    .createTable('reports', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('reporter_id').notNullable();
      t.text('target_type').notNullable();       // user, message
      t.text('target_id').notNullable();
      t.text('reason').notNullable();
      t.text('severity').defaultTo('normal');    // low, normal, high, critical
      t.text('status').defaultTo('pending');     // pending, reviewing, resolved, dismissed
      t.json('evidence').defaultTo('{}');
      t.text('assignee_id');
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
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('channel_id').notNullable();
      t.text('message_id');
      t.text('type').defaultTo('buttons');       // buttons, select, reactions
      t.text('mode').defaultTo('multi');         // single, multi
      t.text('title').notNullable();
      t.text('description');
      t.json('options').defaultTo('[]');         // [{ roleId, label, emoji, description }]
    })

    // ===================================
    // Temp Roles (rôles temporaires)
    // ===================================
    .createTable('temp_roles', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable();
      t.text('user_id').notNullable();
      t.text('role_id').notNullable();
      t.datetime('expires_at').notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index('expires_at');
    })

    // ===================================
    // Events (événements planifiés)
    // ===================================
    .createTable('events', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('title').notNullable();
      t.text('description');
      t.text('channel_id');
      t.text('creator_id').notNullable();
      t.datetime('scheduled_at').notNullable();
      t.integer('duration');                     // minutes
      t.json('participants').defaultTo('[]');
      t.json('reminders').defaultTo('[1440, 60, 15]'); // minutes avant
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'scheduled_at']);
    })

    // ===================================
    // Shop Items
    // ===================================
    .createTable('shop_items', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('name').notNullable();
      t.text('description');
      t.integer('price').notNullable();
      t.text('type').notNullable();              // role, item, perk
      t.json('metadata').defaultTo('{}');        // { roleId, duration, ... }
      t.integer('stock');                        // null = illimité
      t.boolean('enabled').defaultTo(true);
    })

    // ===================================
    // Inventaires
    // ===================================
    .createTable('inventories', (t) => {
      t.increments('id').primary();
      t.text('user_id').notNullable();
      t.text('guild_id').notNullable();
      t.integer('item_id').notNullable().references('id').inTable('shop_items').onDelete('CASCADE');
      t.integer('quantity').defaultTo(1);
      t.datetime('acquired_at').defaultTo(knex.fn.now());
      t.unique(['user_id', 'guild_id', 'item_id']);
    })

    // ===================================
    // Transactions (ledger économie)
    // ===================================
    .createTable('transactions', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable();
      t.text('from_id');                         // null = système
      t.text('to_id').notNullable();
      t.integer('amount').notNullable();
      t.text('type').notNullable();              // earn, spend, transfer, tax, reward, daily, weekly
      t.text('reason');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'created_at']);
    })

    // ===================================
    // Tags / FAQ
    // ===================================
    .createTable('tags', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('name').notNullable();
      t.json('content').notNullable();           // { text, embeds }
      t.text('creator_id').notNullable();
      t.integer('uses').defaultTo(0);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'name']);
    })

    // ===================================
    // Reminders
    // ===================================
    .createTable('reminders', (t) => {
      t.increments('id').primary();
      t.text('guild_id');
      t.text('user_id').notNullable();
      t.text('channel_id');                      // null = DM
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
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('channel_id').notNullable();
      t.json('content').notNullable();           // { text, embeds }
      t.text('cron_expr');
      t.datetime('next_run_at');
      t.boolean('enabled').defaultTo(true);
      t.datetime('created_at').defaultTo(knex.fn.now());
    })

    // ===================================
    // Custom Commands (staff)
    // ===================================
    .createTable('custom_commands', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('name').notNullable();
      t.json('response').notNullable();          // { text, embeds, ephemeral }
      t.json('required_roles').defaultTo('[]');
      t.integer('cooldown').defaultTo(0);        // secondes
      t.integer('uses').defaultTo(0);
      t.boolean('enabled').defaultTo(true);
      t.text('creator_id').notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'name']);
    })

    // ===================================
    // Tâches CRON persistées
    // ===================================
    .createTable('cron_tasks', (t) => {
      t.increments('id').primary();
      t.text('guild_id');
      t.text('type').notNullable();              // tempban_expire, temprole_expire, reminder, announcement
      t.text('cron_expr');                       // Expression cron (récurrent)
      t.datetime('run_at');                      // Exécution unique
      t.json('payload').defaultTo('{}');
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
      t.text('guild_id').notNullable();
      t.text('user_id').notNullable();
      t.text('signal_type').notNullable();       // SPAM, RAID, PHISHING, TOKEN_LEAK, MASS_MENTION
      t.text('severity').defaultTo('medium');    // low, medium, high, critical
      t.json('evidence').defaultTo('{}');
      t.text('action_taken');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'signal_type', 'created_at']);
    })

    .createTable('trust_scores', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable();
      t.text('user_id').notNullable();
      t.integer('score').defaultTo(0);
      t.json('factors').defaultTo('{}');
      t.datetime('calculated_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'user_id']);
    })

    // ===================================
    // Onboarding
    // ===================================
    .createTable('onboarding_states', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable();
      t.text('user_id').notNullable();
      t.text('step').defaultTo('pending');       // pending, rules_accepted, quiz_passed, verified
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
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('scope_type').notNullable();        // role, user, channel
      t.text('scope_id').notNullable();
      t.text('command_key').notNullable();        // "ban", "ticket.close", "*"
      t.text('effect').notNullable();            // allow, deny
      t.integer('priority').defaultTo(0);
      t.index(['guild_id', 'command_key']);
    })

    // ===================================
    // Daily Metrics (analytics)
    // ===================================
    .createTable('daily_metrics', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable();
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
      t.text('guild_id').notNullable();
      t.text('channel_id').notNullable().unique();
      t.text('owner_id').notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());
    })

    // ===================================
    // Voice Sessions (statistiques)
    // ===================================
    .createTable('voice_sessions', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable();
      t.text('user_id').notNullable();
      t.text('channel_id').notNullable();
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
      t.text('guild_id').notNullable();
      t.text('user_id').notNullable();
      t.text('first_name').notNullable();
      t.text('last_name').notNullable();
      t.integer('age');
      t.text('description');
      t.text('photo_url');
      t.text('status').defaultTo('active');      // active, dead, archived
      t.json('inventory').defaultTo('[]');
      t.json('record').defaultTo('[]');          // Casier judiciaire RP
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'user_id']);
    })

    // ===================================
    // Intégrations externes
    // ===================================
    .createTable('integrations', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('provider').notNullable();          // twitch, youtube, github, etc.
      t.text('channel_id').notNullable();        // Discord channel destination
      t.json('settings').defaultTo('{}');
      t.boolean('enabled').defaultTo(true);
      t.text('cursor');                          // Dernier event traité (polling)
      t.datetime('updated_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'provider']);
    })

    // ===================================
    // AutoMod filters custom
    // ===================================
    .createTable('automod_filters', (t) => {
      t.increments('id').primary();
      t.text('guild_id').notNullable();
      t.text('type').notNullable();              // word, regex, domain, filetype
      t.text('pattern').notNullable();
      t.text('action').defaultTo('delete');      // delete, warn, timeout, ban
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
