// ===================================
// Ultra Suite — Tests: Module Manifests
// ===================================

const fs = require('fs');
const path = require('path');

describe('Module Manifests', () => {
  const modulesDir = path.resolve(__dirname, '../../modules');
  const moduleFiles = fs.readdirSync(modulesDir).filter(f => f.endsWith('.js'));

  test('has at least 15 module files', () => {
    expect(moduleFiles.length).toBeGreaterThanOrEqual(15);
  });

  test.each(moduleFiles)('%s loads without error', (file) => {
    expect(() => require(path.join(modulesDir, file))).not.toThrow();
  });

  test.each(moduleFiles)('%s has required properties', (file) => {
    const mod = require(path.join(modulesDir, file));
    expect(mod.id).toBeDefined();
    expect(mod.name).toBeDefined();
    expect(typeof mod.id).toBe('string');
    expect(typeof mod.name).toBe('string');
  });

  test.each(moduleFiles)('%s has valid category', (file) => {
    const mod = require(path.join(modulesDir, file));
    if (mod.category) {
      expect(typeof mod.category).toBe('string');
    }
  });

  test('no duplicate module IDs', () => {
    const ids = moduleFiles.map(f => require(path.join(modulesDir, f)).id);
    const unique = new Set(ids);
    expect(unique.size).toBe(ids.length);
  });
});
