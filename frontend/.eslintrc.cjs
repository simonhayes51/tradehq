// ESLint config.
//
// package.json has shipped `"lint": "eslint . --ext .js,.jsx"` with NO
// config file in the repo, so `npm run lint` has only ever printed
// "ESLint couldn't find a configuration file" and exited 1. A lint script
// that cannot run is worse than none: it looks like coverage that isn't
// there, and any CI step wired to it either fails permanently or was
// disabled and forgotten.
//
// Deliberately calibrated to the code that exists rather than to a
// green-field ideal. The rules turned on are the ones that catch real
// defects this codebase can actually hit - an undefined variable, a bad
// hook dependency array, a hook called conditionally - and the stylistic
// ones are off, because reformatting ~24,000 lines of working UI to
// satisfy a linter is a change with risk and no user-visible benefit.
module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  settings: { react: { version: "detect" } },
  extends: [
    "eslint:recommended",
    "plugin:react/recommended",
    "plugin:react/jsx-runtime",
    "plugin:react-hooks/recommended",
  ],
  plugins: ["react", "react-hooks"],
  ignorePatterns: ["dist", "node_modules", "public", "patches", "*.config.js", "index.js"],
  rules: {
    // The React 17+ JSX transform is in use (see plugin:react/jsx-runtime),
    // so React does not need to be in scope, and propTypes are not used
    // anywhere in this codebase.
    "react/prop-types": "off",
    "react/no-unescaped-entities": "off",
    // Genuine defect classes, kept as errors.
    "react-hooks/rules-of-hooks": "error",
    "react-hooks/exhaustive-deps": "warn",
    "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    // `catch {}` around localStorage is correct, not a mistake: it throws
    // in private-browsing modes and the app must carry on regardless.
    "no-empty": ["error", { allowEmptyCatch: true }],
    // Naming an inline render callback is cosmetic, not a defect.
    "react/display-name": "warn",
  },
  overrides: [
    {
      files: ["**/*.test.js", "**/*.test.jsx", "src/setupTests.js"],
      env: { node: true },
      globals: { vi: "readonly", describe: "readonly", it: "readonly", expect: "readonly",
                 beforeEach: "readonly", afterEach: "readonly", beforeAll: "readonly", afterAll: "readonly" },
    },
  ],
};
