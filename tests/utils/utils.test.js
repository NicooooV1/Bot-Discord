// ===================================
// Ultra Suite — Tests: Utils
// ===================================

describe('Formatters', () => {
  let formatters;

  beforeAll(() => {
    formatters = require('../../utils/formatters');
  });

  test('should export at least one function', () => {
    const exports = Object.keys(formatters);
    expect(exports.length).toBeGreaterThan(0);
  });
});

describe('Embeds', () => {
  let embeds;

  beforeAll(() => {
    embeds = require('../../utils/embeds');
  });

  test('should export at least one function', () => {
    const exports = Object.keys(embeds);
    expect(exports.length).toBeGreaterThan(0);
  });
});

describe('Permissions', () => {
  let permissions;

  beforeAll(() => {
    permissions = require('../../utils/permissions');
  });

  test('should export at least one function', () => {
    const exports = Object.keys(permissions);
    expect(exports.length).toBeGreaterThan(0);
  });
});
