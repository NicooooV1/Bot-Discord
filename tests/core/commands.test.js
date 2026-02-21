// ===================================
// Ultra Suite — Tests: Command Handler
// ===================================

const path = require('path');
const fs = require('fs');

describe('CommandHandler', () => {
  let commandHandler;

  beforeAll(() => {
    commandHandler = require('../../core/commandHandler');
  });

  test('should export loadCommands function', () => {
    expect(typeof commandHandler.loadCommands).toBe('function');
  });
});

describe('All commands are valid', () => {
  const commandsDir = path.resolve(__dirname, '../../commands');

  function scanCommands(dir) {
    const commands = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        commands.push(...scanCommands(path.join(dir, entry.name)));
      } else if (entry.name.endsWith('.js')) {
        commands.push(path.join(dir, entry.name));
      }
    }
    return commands;
  }

  const commandFiles = scanCommands(commandsDir);

  test('has at least 20 command files', () => {
    expect(commandFiles.length).toBeGreaterThanOrEqual(20);
  });

  test.each(commandFiles)('%s loads without error', (filePath) => {
    expect(() => {
      const cmd = require(filePath);
      expect(cmd).toBeDefined();
    }).not.toThrow();
  });

  test.each(commandFiles)('%s has data and execute', (filePath) => {
    const cmd = require(filePath);
    expect(cmd.data).toBeDefined();
    expect(typeof cmd.execute).toBe('function');
  });

  test.each(commandFiles)('%s has module property', (filePath) => {
    const cmd = require(filePath);
    expect(cmd.module).toBeDefined();
    expect(typeof cmd.module).toBe('string');
  });
});
