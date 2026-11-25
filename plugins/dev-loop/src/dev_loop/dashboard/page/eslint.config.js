// Core rules only, so this imports nothing (ESLint does not lint above its config's folder).
const browser = Object.fromEntries(
  "document window fetch alert confirm setInterval clearTimeout setTimeout AbortSignal".split(" ").map((n) => [n, "readonly"]),
);

export default [
  {
    files: ["**/*.js"],
    languageOptions: { ecmaVersion: "latest", sourceType: "module", globals: browser },
    rules: {
      "no-undef": "error",
      "no-unused-vars": "error",
      "max-lines": ["error", 300],
      "max-lines-per-function": ["error", 40],
      complexity: ["error", 10],
      "max-params": ["error", 4],
    },
  },
];
