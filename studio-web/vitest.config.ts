import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

/**
 * Frontend unit tests.
 *
 * Added for `lib/opencode/transcript.ts` (#367), which is the first logic in
 * this app worth a test runner: it is a pure state machine driving what the
 * chat shows, its rules come from measured server behaviour rather than from
 * anything obvious, and three review rounds found ordering and reconciliation
 * bugs in it that `tsc` cannot see.
 *
 * Deliberately node-environment and no DOM: components are still checked only
 * by `tsc`, and pulling in jsdom and a testing library would be a much larger
 * commitment than this file implies.
 */
export default defineConfig({
  resolve: {
    alias: {
      // Mirrors the "@/*" path in tsconfig.json.
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts"],
  },
});
