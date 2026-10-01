import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Vitest config.
 *
 * Kept separate from vite.config.ts so that the unit-test run does not
 * depend on the application's build-time plugins. The `@` alias and the
 * `@/` -> `src/` resolution must match vite.config.ts exactly, or tests
 * would resolve modules differently from the real build.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    // jsdom gives us a DOM; no test needs a real browser or a backend.
    environment: "jsdom",
    globals: true,
    // Vitest handles CSS imports itself (it stubs them by default);
    // this flag makes the intent explicit rather than implicit.
    css: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.{test,spec}.{ts,tsx}"],
    restoreMocks: true,
  },
});
