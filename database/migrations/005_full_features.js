// ===================================
// Ultra Suite — Migration 005
// Tables complètes pour toutes les fonctionnalités 95%
// Giveaways, Starboard, Verification, Polls, Suggestions,
// Social Profiles, Forums, Backup, Premium, Anti-nuke,
// Music playlists, Enhanced economy, etc.
// ===================================

exports.up = async function (knex) {

  // === Giveaways ===
  if (!(await knex.schema.hasTable('giveaways'))) {
    await knex.schema.createTable('giveaways', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).notNullable();
      t.string('message_id', 20);
      t.string('host_id', 20).notNullable();
      t.string('prize', 500).notNullable();
      t.integer('winners_count').unsigned().defaultTo(1);
      t.json('requirements'); // { roles: [], level: 0, messages: 0, age_days: 0 }
      t.json('bonus_entries'); // { roleId: multiplier }
      t.json('participants'); // [userId, ...]
      t.json('winner_ids');
      t.string('status', 20).defaultTo('active'); // active, paused, ended
      t.boolean('recurring').defaultTo(false);
      t.string('recurrence_cron', 100);
      t.datetime('ends_at').notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('ended_at');
      t.index(['guild_id', 'status']);
      t.index(['status', 'ends_at']);
    });
  }

  // === Starboard ===
  if (!(await knex.schema.hasTable('starboard_entries'))) {
    await knex.schema.createTable('starboard_entries', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('original_message_id', 20).notNullable();
      t.string('original_channel_id', 20).notNullable();
      t.string('starboard_message_id', 20);
      t.string('author_id', 20).notNullable();
      t.integer('star_count').unsigned().defaultTo(0);
      t.json('star_users'); // [userId, ...]
      t.string('board_name', 50).defaultTo('default');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'original_message_id', 'board_name']);
      t.index(['guild_id', 'star_count']);
    });
  }

  // === Starboard Config (multi-boards) ===
  if (!(await knex.schema.hasTable('starboard_config'))) {
    await knex.schema.createTable('starboard_config', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('board_name', 50).defaultTo('default');
      t.string('channel_id', 20).notNullable();
      t.string('emoji', 100).defaultTo('⭐');
      t.integer('threshold').unsigned().defaultTo(3);
      t.boolean('self_star').defaultTo(false);
      t.boolean('auto_pin').defaultTo(false);
      t.boolean('notify_author').defaultTo(false);
      t.json('blacklisted_channels');
      t.json('blacklisted_roles');
      t.boolean('enabled').defaultTo(true);
      t.unique(['guild_id', 'board_name']);
    });
  }

  // === Verification ===
  if (!(await knex.schema.hasTable('verification_config'))) {
    await knex.schema.createTable('verification_config', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().unique().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('type', 30).defaultTo('button'); // button, reaction, captcha, question, rules
      t.string('channel_id', 20);
      t.string('verified_role_id', 20);
      t.string('unverified_role_id', 20);
      t.integer('timeout_minutes').unsigned().defaultTo(30);
      t.integer('min_account_age_days').unsigned().defaultTo(0);
      t.string('question', 500);
      t.string('answer', 255);
      t.text('rules_text');
      t.string('message_id', 20);
      t.boolean('dm_welcome').defaultTo(false);
      t.text('dm_message');
      t.boolean('enabled').defaultTo(true);
    });
  }

  // === Polls ===
  if (!(await knex.schema.hasTable('polls'))) {
    await knex.schema.createTable('polls', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).notNullable();
      t.string('message_id', 20);
      t.string('creator_id', 20).notNullable();
      t.string('question', 500).notNullable();
      t.json('options'); // [{label, emoji, votes: [userId]}]
      t.boolean('multiple_choice').defaultTo(false);
      t.boolean('anonymous').defaultTo(false);
      t.string('status', 20).defaultTo('active');
      t.datetime('ends_at');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'status']);
    });
  }

  // === Suggestions ===
  if (!(await knex.schema.hasTable('suggestions'))) {
    await knex.schema.createTable('suggestions', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20);
      t.string('message_id', 20);
      t.string('author_id', 20).notNullable();
      t.text('content').notNullable();
      t.string('status', 20).defaultTo('pending'); // pending, accepted, rejected, in_progress
      t.text('staff_response');
      t.string('reviewer_id', 20);
      t.integer('upvotes').unsigned().defaultTo(0);
      t.integer('downvotes').unsigned().defaultTo(0);
      t.json('upvote_users');
      t.json('downvote_users');
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.datetime('reviewed_at');
      t.index(['guild_id', 'status']);
    });
  }

  // === Social Profiles ===
  if (!(await knex.schema.hasTable('social_profiles'))) {
    await knex.schema.createTable('social_profiles', (t) => {
      t.increments('id').primary();
      t.string('user_id', 20).notNullable();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.text('bio');
      t.string('title', 100);
      t.string('color', 7); // hex color
      t.string('background', 255);
      t.json('social_links'); // { twitter, youtube, twitch, github, ... }
      t.json('badges'); // [{ id, name, emoji, earned_at }]
      t.date('birthday');
      t.string('custom_status', 255);
      t.string('partner_id', 20); // marriage
      t.datetime('married_at');
      t.integer('reputation').defaultTo(0);
      t.json('rep_givers'); // { date: [userId] } - daily tracking
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['user_id', 'guild_id']);
      t.index(['guild_id', 'reputation']);
    });
  }

  // === Music Playlists ===
  if (!(await knex.schema.hasTable('playlists'))) {
    await knex.schema.createTable('playlists', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20);
      t.string('name', 100).notNullable();
      t.boolean('is_server').defaultTo(false);
      t.json('tracks'); // [{ title, url, duration }]
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['user_id']);
      t.index(['guild_id', 'is_server']);
    });
  }

  // === Backup ===
  if (!(await knex.schema.hasTable('backups'))) {
    await knex.schema.createTable('backups', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('creator_id', 20).notNullable();
      t.string('backup_id', 50).notNullable().unique();
      t.json('data'); // full server structure
      t.json('bot_config'); // bot configuration
      t.boolean('includes_messages').defaultTo(false);
      t.integer('size_bytes').unsigned();
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'created_at']);
    });
  }

  // === Premium ===
  if (!(await knex.schema.hasTable('premium_guilds'))) {
    await knex.schema.createTable('premium_guilds', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('tier', 20).defaultTo('bronze'); // bronze, silver, gold
      t.string('activated_by', 20);
      t.string('payment_provider', 30); // stripe, paypal, patreon, manual
      t.string('subscription_id', 255);
      t.datetime('started_at').defaultTo(knex.fn.now());
      t.datetime('expires_at');
      t.boolean('active').defaultTo(true);
      t.unique('guild_id');
    });
  }

  // === Premium Codes ===
  if (!(await knex.schema.hasTable('promo_codes'))) {
    await knex.schema.createTable('promo_codes', (t) => {
      t.increments('id').primary();
      t.string('code', 50).notNullable().unique();
      t.string('tier', 20).defaultTo('bronze');
      t.integer('duration_days').unsigned().defaultTo(30);
      t.integer('discount_percent').unsigned().defaultTo(0);
      t.integer('max_uses').unsigned().defaultTo(1);
      t.integer('current_uses').unsigned().defaultTo(0);
      t.datetime('expires_at');
      t.datetime('created_at').defaultTo(knex.fn.now());
    });
  }

  // === Anti-nuke Log ===
  if (!(await knex.schema.hasTable('antinuke_log'))) {
    await knex.schema.createTable('antinuke_log', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('action_type', 50).notNullable();
      t.string('executor_id', 20).notNullable();
      t.integer('action_count').unsigned().defaultTo(1);
      t.json('details');
      t.string('action_taken', 100);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'executor_id', 'created_at']);
    });
  }

  // === Anti-nuke Whitelist ===
  if (!(await knex.schema.hasTable('antinuke_whitelist'))) {
    await knex.schema.createTable('antinuke_whitelist', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'user_id']);
    });
  }

  // === AFK ===
  if (!(await knex.schema.hasTable('afk_users'))) {
    await knex.schema.createTable('afk_users', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('reason', 500).defaultTo('AFK');
      t.datetime('since').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'user_id']);
    });
  }

  // === Sticky Messages ===
  if (!(await knex.schema.hasTable('sticky_messages'))) {
    await knex.schema.createTable('sticky_messages', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('channel_id', 20).notNullable();
      t.string('message_id', 20);
      t.text('content').notNullable();
      t.json('embed_data');
      t.boolean('enabled').defaultTo(true);
      t.unique(['guild_id', 'channel_id']);
    });
  }

  // === Auto-responders / Triggers ===
  if (!(await knex.schema.hasTable('auto_responders'))) {
    await knex.schema.createTable('auto_responders', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('name', 100).notNullable();
      t.string('trigger_type', 20).notNullable(); // contains, exact, startsWith, endsWith, regex
      t.text('trigger_pattern').notNullable();
      t.text('response');
      t.json('response_embed');
      t.json('actions'); // [{ type: 'add_role', value: roleId }, { type: 'dm', value: 'msg' }]
      t.json('conditions'); // { roles: [], channels: [], probability: 100 }
      t.integer('cooldown').unsigned().defaultTo(0);
      t.integer('uses').unsigned().defaultTo(0);
      t.boolean('enabled').defaultTo(true);
      t.string('creator_id', 20);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'enabled']);
    });
  }

  // === Invite Tracking ===
  if (!(await knex.schema.hasTable('invite_tracking'))) {
    await knex.schema.createTable('invite_tracking', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('inviter_id', 20).notNullable();
      t.string('invited_id', 20).notNullable();
      t.string('invite_code', 20);
      t.datetime('joined_at').defaultTo(knex.fn.now());
      t.boolean('fake').defaultTo(false);
      t.boolean('left').defaultTo(false);
      t.index(['guild_id', 'inviter_id']);
      t.index(['guild_id', 'invited_id']);
    });
  }

  // === Persistent Roles ===
  if (!(await knex.schema.hasTable('persistent_roles'))) {
    await knex.schema.createTable('persistent_roles', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.json('roles'); // [roleId, ...]
      t.datetime('left_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'user_id']);
    });
  }

  // === Warn Decay ===
  if (!(await knex.schema.hasTable('warn_config'))) {
    await knex.schema.createTable('warn_config', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().unique().references('id').inTable('guilds').onDelete('CASCADE');
      t.boolean('decay_enabled').defaultTo(false);
      t.integer('decay_days').unsigned().defaultTo(30);
      t.boolean('appeal_enabled').defaultTo(false);
      t.string('appeal_channel_id', 20);
      t.boolean('points_enabled').defaultTo(false);
      t.json('point_values'); // { WARN: 1, TIMEOUT: 2, KICK: 3, BAN: 5 }
      t.integer('points_decay_per_day').unsigned().defaultTo(0);
    });
  }

  // === Forum Management ===
  if (!(await knex.schema.hasTable('forum_config'))) {
    await knex.schema.createTable('forum_config', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('forum_channel_id', 20).notNullable();
      t.json('auto_tags'); // [{ keyword, tagId }]
      t.text('template'); // required post template
      t.boolean('auto_react').defaultTo(false);
      t.string('auto_react_emojis', 255).defaultTo('👍,👎');
      t.integer('auto_archive_days').unsigned().defaultTo(7);
      t.boolean('notify_new_post').defaultTo(false);
      t.string('notify_channel_id', 20);
      t.boolean('enabled').defaultTo(true);
      t.unique(['guild_id', 'forum_channel_id']);
    });
  }

  // === Moderation points ===
  if (!(await knex.schema.hasTable('mod_points'))) {
    await knex.schema.createTable('mod_points', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.integer('points').defaultTo(0);
      t.datetime('last_decay').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'user_id']);
    });
  }

  // === Quarantine ===
  if (!(await knex.schema.hasTable('quarantine'))) {
    await knex.schema.createTable('quarantine', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('moderator_id', 20).notNullable();
      t.text('reason');
      t.json('original_roles'); // roles before quarantine
      t.datetime('expires_at');
      t.boolean('active').defaultTo(true);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'user_id', 'active']);
    });
  }

  // Add daily_streak to users table if not exists
  const hasStreak = await knex.schema.hasColumn('users', 'daily_streak');
  if (!hasStreak) {
    await knex.schema.alterTable('users', (t) => {
      t.integer('daily_streak').unsigned().defaultTo(0);
      t.integer('total_earned').unsigned().defaultTo(0);
      t.integer('total_spent').unsigned().defaultTo(0);
    });
  }

  // Add ticket enhancements
  const hasTicketPriority = await knex.schema.hasColumn('tickets', 'tags');
  if (!hasTicketPriority) {
    await knex.schema.alterTable('tickets', (t) => {
      t.json('tags');
      t.text('close_reason');
      t.boolean('blacklisted_opener').defaultTo(false);
    });
  }

  // === Ticket Blacklist ===
  if (!(await knex.schema.hasTable('ticket_blacklist'))) {
    await knex.schema.createTable('ticket_blacklist', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('reason', 500);
      t.string('moderator_id', 20);
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.unique(['guild_id', 'user_id']);
    });
  }

  // === Economy: Work cooldowns ===
  if (!(await knex.schema.hasTable('work_cooldowns'))) {
    await knex.schema.createTable('work_cooldowns', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.datetime('last_work');
      t.datetime('last_crime');
      t.datetime('last_rob');
      t.unique(['guild_id', 'user_id']);
    });
  }

  // === Gamble History ===
  if (!(await knex.schema.hasTable('gamble_history'))) {
    await knex.schema.createTable('gamble_history', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.string('game', 30).notNullable();
      t.integer('bet').unsigned();
      t.integer('result').notNullable(); // positive = win, negative = loss
      t.datetime('created_at').defaultTo(knex.fn.now());
      t.index(['guild_id', 'user_id']);
    });
  }

  // === Daily gambling limits ===
  if (!(await knex.schema.hasTable('gambling_limits'))) {
    await knex.schema.createTable('gambling_limits', (t) => {
      t.increments('id').primary();
      t.string('guild_id', 20).notNullable().references('id').inTable('guilds').onDelete('CASCADE');
      t.string('user_id', 20).notNullable();
      t.date('date').notNullable();
      t.integer('total_bet').unsigned().defaultTo(0);
      t.integer('games_played').unsigned().defaultTo(0);
      t.unique(['guild_id', 'user_id', 'date']);
    });
  }
};

exports.down = async function (knex) {
  const tables = [
    'gambling_limits', 'gamble_history', 'work_cooldowns', 'ticket_blacklist',
    'mod_points', 'quarantine', 'forum_config', 'warn_config', 'persistent_roles',
    'invite_tracking', 'auto_responders', 'sticky_messages', 'afk_users',
    'antinuke_whitelist', 'antinuke_log', 'promo_codes', 'premium_guilds',
    'backups', 'playlists', 'social_profiles', 'suggestions', 'polls',
    'verification_config', 'starboard_config', 'starboard_entries', 'giveaways'
  ];
  for (const table of tables) {
    await knex.schema.dropTableIfExists(table);
  }
};
