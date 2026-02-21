module.exports = {
  testEnvironment: 'node',
  testMatch: ['**/tests/**/*.test.js'],
  collectCoverageFrom: [
    'core/**/*.js',
    'commands/**/*.js',
    'utils/**/*.js',
    'database/**/*.js',
    '!database/migrations/**',
  ],
  coverageDirectory: 'coverage',
  coverageReporters: ['text', 'lcov', 'clover'],
  coverageThresholds: {
    global: {
      branches: 30,
      functions: 40,
      lines: 40,
      statements: 40,
    },
  },
  setupFilesAfterSetup: ['./tests/setup.js'],
  testTimeout: 10000,
};
