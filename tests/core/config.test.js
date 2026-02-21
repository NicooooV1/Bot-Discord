// ===================================
// Ultra Suite — Tests: Config Engine
// ===================================

const path = require('path');

describe('ConfigEngine', () => {
  let configEngine;

  beforeAll(() => {
    configEngine = require('../../core/configEngine');
  });

  test('should export validate function', () => {
    expect(typeof configEngine.validate).toBe('function');
  });

  test('should export mergeDefaults function', () => {
    expect(typeof configEngine.mergeDefaults).toBe('function');
  });

  test('should validate correct config', () => {
    const config = { prefix: '!', lang: 'fr' };
    const result = configEngine.validate(config);
    expect(result.valid).not.toBe(false);
  });
});

describe('ConfigService', () => {
  let configService;

  beforeAll(() => {
    configService = require('../../core/configService');
  });

  test('should export get function', () => {
    expect(typeof configService.get).toBe('function');
  });

  test('should export set function', () => {
    expect(typeof configService.set).toBe('function');
  });

  test('should export getCacheStats function', () => {
    expect(typeof configService.getCacheStats).toBe('function');
  });

  test('getCacheStats returns object', () => {
    const stats = configService.getCacheStats();
    expect(typeof stats).toBe('object');
  });
});
