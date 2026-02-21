module.exports = {
  env: {
    node: true,
    es2022: true,
    jest: true,
  },
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
  },
  extends: ['eslint:recommended'],
  rules: {
    'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    'no-console': 'off',
    'prefer-const': 'error',
    'no-var': 'error',
    'eqeqeq': ['error', 'always'],
    'curly': ['warn', 'multi-line'],
    'no-throw-literal': 'error',
    'no-return-await': 'warn',
    'require-await': 'warn',
    'semi': ['error', 'always'],
    'quotes': ['error', 'single', { avoidEscape: true }],
    'comma-dangle': ['error', 'always-multiline'],
    'indent': ['warn', 2, { SwitchCase: 1 }],
    'max-len': ['warn', { code: 200, ignoreStrings: true, ignoreTemplateLiterals: true }],
  },
  ignorePatterns: ['node_modules/', 'coverage/', 'data/', 'logs/', 'dashboard/public/'],
};
