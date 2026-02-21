// ===================================
// Ultra Suite — Test Setup
// ===================================

// Mock Discord.js client
jest.mock('discord.js', () => {
  const actual = jest.requireActual('discord.js');
  return {
    ...actual,
    Client: jest.fn().mockImplementation(() => ({
      commands: new Map(),
      components: new Map(),
      componentPrefixes: [],
      guilds: { cache: new Map() },
      channels: { cache: new Map() },
      user: { tag: 'TestBot#0000', id: '123456789' },
      login: jest.fn().mockResolvedValue('token'),
      destroy: jest.fn(),
      once: jest.fn(),
      on: jest.fn(),
    })),
  };
});

// Mock environment
process.env.NODE_ENV = 'test';
process.env.DB_HOST = 'localhost';
process.env.DB_PORT = '3306';
process.env.DB_USER = 'test';
process.env.DB_PASSWORD = 'testpassword';
process.env.DB_NAME = 'ultra_suite_test';
process.env.BOT_TOKEN = 'test-token';
process.env.CLIENT_ID = '000000000000000000';
process.env.API_SECRET = 'test-secret';
