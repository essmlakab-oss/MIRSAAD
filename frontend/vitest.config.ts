import path from "node:path";
import { defineConfig } from "vitest/config";

// The unit-test runner: jsdom + the app's `@` alias. No TanStack Start
// plugins — the specs mount the FDI components directly, so vite's own TS
// handling is enough.
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
