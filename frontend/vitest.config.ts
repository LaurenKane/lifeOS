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
    // `tests/live-stack.test.tsx` drives the real FastAPI server and the real
    // Postgres, so it cannot be part of a default run: it would be red for
    // everyone who is not already running the stack. That is the "green gate
    // over nothing" failure wearing a disguise -- here the gate is red for
    // everyone and green only for whoever happened to start the server first.
    //
    // So it is EXCLUDED by default and opted into explicitly, mirroring the
    // backend's `-m 'not db'` / `make test-db` split. The exclusion is
    // conditional rather than hard-coded so that opting in needs no second
    // config file that could drift from this one:
    //
    //     LIFEOS_LIVE_STACK=1 npx vitest run tests/live-stack.test.tsx
    //
    // Vitest REPLACES the default exclude list when `exclude` is set, so the
    // defaults are re-listed here verbatim rather than silently dropped.
    exclude: process.env.LIFEOS_LIVE_STACK
      ? []
      : [
          "**/node_modules/**",
          "**/dist/**",
          "**/cypress/**",
          "**/.{idea,git,cache,output,temp}/**",
          "**/{karma,rollup,webpack,vite,vitest,jest,ava,babel,nyc,cypress,tsup,build,eslint,prettier}.config.*",
          "tests/live-stack.test.tsx",
        ],
    restoreMocks: true,
  },
});
