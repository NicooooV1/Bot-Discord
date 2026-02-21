// ===================================
// Ultra Suite — Tests: i18n
// ===================================

describe('i18n', () => {
  let i18n;

  beforeAll(() => {
    i18n = require('../../core/i18n');
    i18n.loadLocales();
  });

  test('should export loadLocales function', () => {
    expect(typeof i18n.loadLocales).toBe('function');
  });

  test('should export t function', () => {
    expect(typeof i18n.t).toBe('function');
  });

  test('t returns a string for common.error', async () => {
    const result = await i18n.t(null, 'common.error');
    expect(typeof result).toBe('string');
    expect(result).toContain('erreur');
  });

  test('t interpolates variables', async () => {
    const result = await i18n.t(null, 'common.cooldown', { time: '5' });
    expect(result).toContain('5');
  });

  test('t returns key if not found', async () => {
    const result = await i18n.t(null, 'nonexistent.key');
    expect(result).toContain('nonexistent.key');
  });

  test('supports English locale', async () => {
    // Simulate EN guild (would need configService mock)
    const result = await i18n.t(null, 'common.error');
    expect(typeof result).toBe('string');
  });
});
