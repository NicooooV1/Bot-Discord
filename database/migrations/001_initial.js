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
exports.up = function (knex) {
  return knex.schema

    // ===================================
    // Guilds (1 ligne = 1 serveur Discord)
    // Config, modules, thème — tout en JSON séparé
    // ===================================
    .createTable('guilds', (t) => {
      t.string('id', 20).primary();              // Discord guild ID (snowflake)
      t.string('name', 255).notNullable();
      t.string('owner_id', 20).notNullable();
      t.json('config');                           // Config complète du serveur (JSON)
      t.json('modules_enabled');                  // { moderation: true, xp: false, ... }
      t.json('theme');                            // { primary: "#5865F2", ... }
      t.string('locale', 10).defaultTo('fr');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('updated_at').defaultTo(knex.fn.now());

      // Index pour les requêtes admin/dashboard
      t.index('owner_id');
    })

    // ===================================
    // Users (XP, économie, stats par serveur)
    // Un user a UNE ligne PAR guild (isolation complète)
    // ===================================
    .createTable('users', (t) => {
      t.increments('id').primary();
      t.string('user_id', 20).notNullable();     // Discord user ID
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.integer('xp').unsigned().defaultTo(0);
      t.integer('level').unsigned().defaultTo(0);
      t.integer('reputation').defaultTo(0);
      t.integer('balance').defaultTo(0);
      t.integer('bank').defaultTo(0);
      t.integer('total_messages').unsigned().defaultTo(0);
      t.integer('voice_minutes').unsigned().defaultTo(0);
      t.json('inventory');                        // [] items possédés
      t.datetime('last_daily');
      t.datetime('last_weekly');
      t.datetime('last_message_xp');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('updated_at').defaultTo(knex.fn.now());

      // Contrainte unique : 1 user par guild
      t.unique(['user_id', 'guild_id']);
      // Index pour les leaderboards (XP, messages, vocal)
      t.index(['guild_id', 'xp']);
      t.index(['guild_id', 'total_messages']);
      t.index(['guild_id', 'voice_minutes']);
      // Index pour les requêtes économie
      t.index(['guild_id', 'balance']);
    })

    // ===================================
    // Sanctions (modération)
    // Case system : chaque sanction a un numéro unique par guild
    // ===================================
    .createTable('sanctions', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.integer('case_number').unsigned().notNullable();
      t.string('type', 20).notNullable();        // WARN, TIMEOUT, KICK, BAN, SOFTBAN, TEMPBAN, UNBAN, UNMUTE
      t.string('target_id', 20).notNullable();
      t.string('moderator_id', 20).notNullable();
      t.text('reason');
      t.integer('duration').unsigned();           // Secondes (pour timeout/tempban)
      t.boolean('active').defaultTo(true);
      t.json('evidence');                         // { links: [], messageIds: [] }
      t.datetime('expires_at');
      t.datetime('revoked_at');
      t.string('revoked_by', 20);
      t.datetime('created_at').defaultTo(knex.fn.now());

      // Case number unique par guild
      t.unique(['guild_id', 'case_number']);
      // Recherche sanctions par user (modlogs)
      t.index(['guild_id', 'target_id', 'active']);
      // Recherche par modérateur (stats mod)
      t.index(['guild_id', 'moderator_id']);
      // Sanctions actives (pour le scheduler d'expiration)
      t.index(['active', 'expires_at']);
      // Recherche par type (filtres modlogs)
      t.index(['guild_id', 'type']);
    })

    // ===================================
    // Logs (audit interne)
    // Tous les événements loggés par le bot
    // ===================================
    .createTable('logs', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('type', 50).notNullable();        // MESSAGE_DELETE, MEMBER_JOIN, MOD_ACTION, AUTOMOD, etc.
      t.string('actor_id', 20);                   // Qui a fait l'action
      t.string('target_id', 20);                  // Sur qui/quoi
      t.string('target_type', 20);               // user, channel, role, message
      t.json('details');                          // {} données supplémentaires
      t.datetime('timestamp').defaultTo(knex.fn.now());

      // Index principal : recherche par guild + type + date
      t.index(['guild_id', 'type', 'timestamp']);
      // Recherche par acteur (qui a fait quoi)
      t.index(['guild_id', 'actor_id', 'timestamp']);
      // Recherche par cible (historique d'un user/channel)
      t.index(['guild_id', 'target_id']);
      // Purge des vieux logs (scheduler)
      t.index('timestamp');
    })

    // ===================================
    // Tickets
    // Système de support avec assignation et transcript
    // ===================================
    .createTable('tickets', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).unique();
      t.string('opener_id', 20).notNullable();
      t.string('category', 50).defaultTo('general');
      t.string('subject', 255);
      t.string('status', 20).defaultTo('open');  // open, in_progress, waiting, resolved, closed
      t.string('priority', 20).defaultTo('normal'); // low, normal, high, urgent
      t.string('assignee_id', 20);
      t.json('form_answers');
      t.text('transcript', 'longtext');           // Longtext pour les longs tickets
      t.integer('rating').unsigned();             // 1-5
      t.text('rating_comment');
      t.string('closed_by', 20);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('closed_at');

      // Tickets ouverts par guild (liste)
      t.index(['guild_id', 'status']);
      // Tickets par user (vérifier le max)
      t.index(['guild_id', 'opener_id', 'status']);
      // Tickets par assignee (dashboard staff)
      t.index(['guild_id', 'assignee_id', 'status']);
    })

    // ===================================
    // Candidatures
    // Système de recrutement staff
    // ===================================
    .createTable('applications', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('applicant_id', 20).notNullable();
      t.string('form_type', 50).defaultTo('default');
      t.json('answers');
      t.string('status', 20).defaultTo('pending'); // pending, reviewing, interview, accepted, rejected
      t.json('notes');                            // [{ staffId, note, date }]
      t.string('reviewer_id', 20);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('updated_at').defaultTo(knex.fn.now());

      // Liste par status
      t.index(['guild_id', 'status']);
      // Vérifier si un user a déjà postulé
      t.index(['guild_id', 'applicant_id', 'status']);
    })

    // ===================================
    // Reports / Signalements
    // ===================================
    .createTable('reports', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('reporter_id', 20).notNullable();
      t.string('target_type', 20).notNullable(); // user, message
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
    })

    // ===================================
    // Role Menus (self-roles)
    // ===================================
    .createTable('role_menus', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).notNullable();
      t.string('message_id', 20);
      t.string('type', 20).defaultTo('buttons'); // buttons, select, reactions
      t.string('mode', 20).defaultTo('multi');   // single, multi
      t.string('title', 255).notNullable();
      t.text('description');
      t.json('options');                          // [{ roleId, label, emoji, description }]

      t.index('guild_id');
    })

    // ===================================
    // Temp Roles (rôles temporaires)
    // ===================================
    .createTable('temp_roles', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('role_id', 20).notNullable();
      t.datetime('expires_at').notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());

      // Expiration (scheduler)
      t.index('expires_at');
      t.index(['guild_id', 'user_id']);
    })

    // ===================================
    // Events (événements planifiés)
    // ===================================
    .createTable('events', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('title', 255).notNullable();
      t.text('description');
      t.string('channel_id', 20);
      t.string('creator_id', 20).notNullable();
      t.datetime('scheduled_at').notNullable();
      t.integer('duration').unsigned();           // minutes
      t.json('participants');                     // ["userId1", "userId2"]
      t.json('reminders');                        // [1440, 60, 15] minutes avant
      t.integer('max_participants').unsigned().defaultTo(0);
      t.string('status', 20).defaultTo('active'); // active, cancelled, completed
      t.datetime('created_at').defaultTo(knex.fn.now());

      // Liste événements par guild triés par date
      t.index(['guild_id', 'status', 'scheduled_at']);
    })

    // ===================================
    // Shop Items (boutique serveur)
    // ===================================
    .createTable('shop_items', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('name', 255).notNullable();
      t.text('description');
      t.integer('price').unsigned().notNullable();
      t.string('type', 20).notNullable();        // role, item, perk
      t.json('metadata');                         // { roleId, duration, ... }
      t.integer('stock');                         // null = illimité
      t.boolean('enabled').defaultTo(true);

      t.index(['guild_id', 'enabled']);
    })

    // ===================================
    // Inventaires (items achetés)
    // ===================================
    .createTable('inventories', (t) => {
      t.increments('id').primary();
      t.string('user_id', 20).notNullable();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.integer('item_id').unsigned().notNullable()
        .references('id').inTable('shop_items').onDelete('CASCADE');
      t.integer('quantity').unsigned().defaultTo(1);
      t.datetime('acquired_at').defaultTo(knex.fn.now());

      t.unique(['user_id', 'guild_id', 'item_id']);
    })

    // ===================================
    // Transactions (ledger économie)
    // ===================================
    .createTable('transactions', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('from_id', 20);                   // null = système
      t.string('to_id', 20).notNullable();
      t.integer('amount').notNullable();
      t.string('type', 30).notNullable();        // earn, spend, transfer, tax, reward, daily, weekly
      t.text('reason');
      t.datetime('created_at').defaultTo(knex.fn.now());

      // Historique par guild
      t.index(['guild_id', 'created_at']);
      // Historique par user
      t.index(['guild_id', 'to_id', 'created_at']);
      t.index(['guild_id', 'from_id', 'created_at']);
    })

    // ===================================
    // Tags / FAQ
    // ===================================
    .createTable('tags', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('name', 100).notNullable();
      t.json('content').notNullable();            // { text, embeds }
      t.string('creator_id', 20).notNullable();
      t.integer('uses').unsigned().defaultTo(0);
      t.datetime('created_at').defaultTo(knex.fn.now());

      // Nom unique par guild
      t.unique(['guild_id', 'name']);
      // Autocomplete (recherche par préfixe)
      t.index(['guild_id', 'uses']);
    })

    // ===================================
    // Reminders (rappels personnels)
    // ===================================
    .createTable('reminders', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20);
      t.string('user_id', 20).notNullable();
      t.string('channel_id', 20);                // null = DM
      t.text('message').notNullable();
      t.datetime('fire_at').notNullable();
      t.boolean('fired').defaultTo(false);
      t.datetime('created_at').defaultTo(knex.fn.now());

      // Scheduler : rappels non déclenchés triés par date
      t.index(['fired', 'fire_at']);
      // Liste des rappels d'un user
      t.index(['user_id', 'fired']);
    })

    // ===================================
    // Annonces planifiées
    // ===================================
    .createTable('announcements', (t) => {
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
    })

    // ===================================
    // Custom Commands (commandes perso staff)
    // ===================================
    .createTable('custom_commands', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('name', 100).notNullable();
      t.json('response').notNullable();           // { text, embeds, ephemeral }
      t.json('required_roles');                   // [] IDs de rôles requis
      t.integer('cooldown').unsigned().defaultTo(0);
      t.integer('uses').unsigned().defaultTo(0);
      t.boolean('enabled').defaultTo(true);
      t.string('creator_id', 20).notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());

      // Nom unique par guild
      t.unique(['guild_id', 'name']);
      // Recherche commandes actives
      t.index(['guild_id', 'enabled']);
    })

    // ===================================
    // Tâches CRON persistées
    // ===================================
    .createTable('cron_tasks', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20)
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('type', 50).notNullable();        // tempban_expire, temprole_expire, reminder, announcement
      t.string('cron_expr', 100);                 // Expression cron (récurrent)
      t.datetime('run_at');                       // Exécution unique
      t.json('payload');
      t.boolean('active').defaultTo(true);
      t.datetime('last_run_at');
      t.datetime('created_at').defaultTo(knex.fn.now());

      t.index(['active', 'run_at']);
      t.index(['guild_id', 'type']);
    })

    // ===================================
    // Sécurité : signaux automod
    // ===================================
    .createTable('security_signals', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('signal_type', 30).notNullable(); // SPAM, RAID, PHISHING, TOKEN_LEAK, MASS_MENTION
      t.string('severity', 20).defaultTo('medium');
      t.json('evidence');
      t.string('action_taken', 100);
      t.datetime('created_at').defaultTo(knex.fn.now());

      t.index(['guild_id', 'signal_type', 'created_at']);
      t.index(['guild_id', 'user_id']);
    })

    // ===================================
    // Trust Scores (score de confiance par user/guild)
    // ===================================
    .createTable('trust_scores', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.integer('score').defaultTo(0);
      t.json('factors');
      t.datetime('calculated_at').defaultTo(knex.fn.now());

      t.unique(['guild_id', 'user_id']);
    })

    // ===================================
    // Onboarding (parcours de vérification)
    // ===================================
    .createTable('onboarding_states', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('step', 30).defaultTo('pending'); // pending, rules_accepted, quiz_passed, verified
      t.json('answers');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('verified_at');

      t.unique(['guild_id', 'user_id']);
    })

    // ===================================
    // Permissions personnalisées (Allow/Deny)
    // Règles fine-grained par commande × rôle/user/channel
    // ===================================
    .createTable('permission_rules', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('scope_type', 20).notNullable();  // role, user, channel
      t.string('scope_id', 20).notNullable();     // ID du rôle/user/channel
      t.string('command_key', 100).notNullable(); // "ban", "ticket.close", "*", "module:moderation"
      t.string('effect', 10).notNullable();       // allow, deny
      t.integer('priority').defaultTo(0);         // Plus haut = plus prioritaire

      // Recherche des règles pour une commande
      t.index(['guild_id', 'command_key']);
      // Recherche des règles pour un scope (rôle/user)
      t.index(['guild_id', 'scope_type', 'scope_id']);
    })

    // ===================================
    // Daily Metrics (analytics par jour par guild)
    // ===================================
    .createTable('daily_metrics', (t) => {
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

      // 1 ligne par guild par jour
      t.unique(['guild_id', 'date']);
      // Requêtes historiques
      t.index(['guild_id', 'date']);
    })

    // ===================================
    // Temp Voice Channels (salons vocaux temporaires)
    // ===================================
    .createTable('temp_voice_channels', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).notNullable().unique();
      t.string('owner_id', 20).notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());

      t.index(['guild_id', 'owner_id']);
    })

    // ===================================
    // Voice Sessions (suivi temps vocal)
    // ===================================
    .createTable('voice_sessions', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('channel_id', 20).notNullable();
      t.datetime('joined_at').defaultTo(knex.fn.now());
      t.datetime('left_at');
      t.integer('duration').unsigned();           // secondes

      // Sessions ouvertes (left_at IS NULL)
      t.index(['guild_id', 'user_id', 'left_at']);
      // Stats historiques
      t.index(['guild_id', 'joined_at']);
    })

    // ===================================
    // RP : Fiches personnages
    // ===================================
    .createTable('rp_characters', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('first_name', 100).notNullable();
      t.string('last_name', 100).defaultTo('');
      t.integer('age').unsigned();
      t.text('description');
      t.text('photo_url');
      t.string('status', 20).defaultTo('active'); // active, dead, archived
      t.json('inventory');
      t.json('record');                           // Casier judiciaire RP
      t.datetime('created_at').defaultTo(knex.fn.now());

      // 1 personnage par user par guild (peut être étendu plus tard)
      t.index(['guild_id', 'user_id']);
      t.index(['guild_id', 'status']);
    })

    // ===================================
    // Intégrations externes (Twitch, GitHub, etc.)
    // ===================================
    .createTable('integrations', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('provider', 50).notNullable();    // twitch, youtube, github, etc.
      t.string('channel_id', 20).notNullable();  // Discord channel destination
      t.json('settings');
      t.boolean('enabled').defaultTo(true);
      t.string('cursor', 255);                   // Dernier event traité (polling)
      t.datetime('updated_at').defaultTo(knex.fn.now());

      // 1 intégration par provider par guild
      t.unique(['guild_id', 'provider']);
    })

    // ===================================
    // AutoMod filters (filtres personnalisés)
    // ===================================
    .createTable('automod_filters', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable()
        .references('id').inTable('guilds').onDelete('CASCADE');
      t.string('type', 20).notNullable();        // word, regex, domain, filetype
      t.text('pattern').notNullable();
      t.string('action', 20).defaultTo('delete'); // delete, warn, timeout, ban
      t.boolean('enabled').defaultTo(true);

      t.index(['guild_id', 'type', 'enabled']);
    });
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