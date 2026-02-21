// ===================================
// Ultra Suite — PM2 Ecosystem Config
// Déploiement Proxmox LXC
//
// Bot : LXC 110 (192.168.1.235)
// Dashboard : LXC 115 (192.168.1.219)
// ===================================

module.exports = {
  apps: [
    {
      // === Bot Discord ===
      name: 'ultra-suite-bot',
      script: 'index.js',
      cwd: '/opt/ultra-suite',
      instances: 1,
      exec_mode: 'fork',
      autorestart: true,
      watch: false,
      max_memory_restart: '512M',

      // Environnement production
      env: {
        NODE_ENV: 'production',
      },

      // Logs
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      error_file: '/var/log/ultra-suite/bot-error.log',
      out_file: '/var/log/ultra-suite/bot-out.log',
      merge_logs: true,
      log_type: 'json',

      // Restart policy
      max_restarts: 10,
      min_uptime: '10s',
      restart_delay: 5000,

      // Graceful shutdown
      kill_timeout: 10000,
      listen_timeout: 10000,
      shutdown_with_message: true,

      // Monitoring
      node_args: '--max-old-space-size=512',
    },
  ],
};
