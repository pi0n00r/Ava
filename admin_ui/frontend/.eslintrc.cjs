module.exports = {
    root: true,
    env: {
        browser: true,
        es2020: true,
    },
    extends: [
        'eslint:recommended',
        'plugin:@typescript-eslint/recommended',
        'plugin:react-hooks/recommended',
        'plugin:react/recommended',
        'plugin:react/jsx-runtime',
        'prettier',
    ],
    ignorePatterns: ['dist', '.eslintrc.cjs', 'node_modules'],
    parser: '@typescript-eslint/parser',
    parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
        ecmaFeatures: {
            jsx: true,
        },
    },
    plugins: ['react-refresh', '@typescript-eslint', 'react'],
    settings: {
        react: {
            version: 'detect',
        },
    },
    rules: {
        // Disable rules that are too strict for rapid development
        '@typescript-eslint/no-explicit-any': 'warn', // Allow any but warn
        '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
        'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
        'react/prop-types': 'off', // TypeScript handles this
        'react/no-unescaped-entities': 'warn',
        'no-console': ['warn', { allow: ['warn', 'error'] }],
    },
};
