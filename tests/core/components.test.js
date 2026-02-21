// ===================================
// Ultra Suite — Tests: Component Handler
// ===================================

const path = require('path');
const fs = require('fs');

describe('ComponentHandler', () => {
  let componentHandler;

  beforeAll(() => {
    componentHandler = require('../../core/componentHandler');
  });

  test('should export loadComponents function', () => {
    expect(typeof componentHandler.loadComponents).toBe('function');
  });
});

describe('All component handlers are valid', () => {
  const componentsDir = path.resolve(__dirname, '../../components');

  function scanComponents(dir) {
    const files = [];
    if (!fs.existsSync(dir)) return files;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        files.push(...scanComponents(path.join(dir, entry.name)));
      } else if (entry.name.endsWith('.js')) {
        files.push(path.join(dir, entry.name));
      }
    }
    return files;
  }

  const componentFiles = scanComponents(componentsDir);

  test('has component files', () => {
    expect(componentFiles.length).toBeGreaterThan(0);
  });

  test.each(componentFiles)('%s loads without error', (filePath) => {
    expect(() => require(filePath)).not.toThrow();
  });

  test.each(componentFiles)('%s has execute function', (filePath) => {
    const handler = require(filePath);
    expect(typeof handler.execute).toBe('function');
  });

  test.each(componentFiles)('%s has customId, prefix, or customIds', (filePath) => {
    const handler = require(filePath);
    const hasId = handler.customId || handler.prefix || handler.customIds;
    expect(hasId).toBeTruthy();
  });
});
