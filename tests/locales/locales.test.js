// ===================================
// Ultra Suite — Tests: Locales
// ===================================

const fs = require('fs');
const path = require('path');

describe('Locale files', () => {
  const localesDir = path.resolve(__dirname, '../../locales');

  test('fr.json exists and is valid JSON', () => {
    const content = fs.readFileSync(path.join(localesDir, 'fr.json'), 'utf-8');
    expect(() => JSON.parse(content)).not.toThrow();
  });

  test('en.json exists and is valid JSON', () => {
    const content = fs.readFileSync(path.join(localesDir, 'en.json'), 'utf-8');
    expect(() => JSON.parse(content)).not.toThrow();
  });

  test('fr.json and en.json have the same top-level keys', () => {
    const fr = JSON.parse(fs.readFileSync(path.join(localesDir, 'fr.json'), 'utf-8'));
    const en = JSON.parse(fs.readFileSync(path.join(localesDir, 'en.json'), 'utf-8'));

    const frKeys = Object.keys(fr).sort();
    const enKeys = Object.keys(en).sort();
    expect(frKeys).toEqual(enKeys);
  });

  test('both locales have common section', () => {
    const fr = JSON.parse(fs.readFileSync(path.join(localesDir, 'fr.json'), 'utf-8'));
    const en = JSON.parse(fs.readFileSync(path.join(localesDir, 'en.json'), 'utf-8'));

    expect(fr.common).toBeDefined();
    expect(en.common).toBeDefined();
    expect(fr.common.error).toBeDefined();
    expect(en.common.error).toBeDefined();
  });

  test('all locale sections are objects', () => {
    const fr = JSON.parse(fs.readFileSync(path.join(localesDir, 'fr.json'), 'utf-8'));
    for (const [key, value] of Object.entries(fr)) {
      expect(typeof value).toBe('object');
    }
  });

  test('fr has all required sections', () => {
    const fr = JSON.parse(fs.readFileSync(path.join(localesDir, 'fr.json'), 'utf-8'));
    const required = ['common', 'moderation', 'tickets', 'xp', 'economy', 'onboarding', 'logs', 'music', 'fun', 'social', 'verify', 'utility', 'giveaway', 'starboard'];
    for (const section of required) {
      expect(fr[section]).toBeDefined();
    }
  });
});
