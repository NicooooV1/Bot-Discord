// ===================================
// Ultra Suite — Tests: Module Registry
// ===================================

describe('ModuleRegistry', () => {
  let registry;

  beforeAll(() => {
    registry = require('../../core/moduleRegistry');
  });

  test('should export loadAll function', () => {
    expect(typeof registry.loadAll).toBe('function');
  });

  test('should export getAll function', () => {
    expect(typeof registry.getAll).toBe('function');
  });

  test('should export get function', () => {
    expect(typeof registry.get).toBe('function');
  });

  test('loadAll loads module manifests', () => {
    registry.loadAll();
    const modules = registry.getAll();
    expect(Array.isArray(modules)).toBe(true);
    expect(modules.length).toBeGreaterThan(0);
  });

  test('each module has required properties', () => {
    registry.loadAll();
    const modules = registry.getAll();
    for (const mod of modules) {
      expect(mod).toHaveProperty('id');
      expect(mod).toHaveProperty('name');
      expect(typeof mod.id).toBe('string');
      expect(typeof mod.name).toBe('string');
    }
  });

  test('get returns a specific module', () => {
    registry.loadAll();
    const mod = registry.get('moderation');
    if (mod) {
      expect(mod.id).toBe('moderation');
    }
  });
});
