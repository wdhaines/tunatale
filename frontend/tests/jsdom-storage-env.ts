import { builtinEnvironments } from "vitest/runtime";

import type { Environment } from "vitest/runtime";

const jsdom = builtinEnvironments.jsdom;

/**
 * jsdom, minus Node's built-in Storage globals.
 *
 * See vite.config.ts::test.environment for the full reasoning.
 */
export default {
  name: "jsdom-storage",
  // "client", not the Vitest-3 spelling `transformMode: "web"` — that field is
  // deprecated in Vitest 4 and warns once per test FILE, which is 73 lines of
  // noise in a gate run. Same for importing from "vitest/environments"
  // (deprecated since 4.1) instead of "vitest/runtime".
  viteEnvironment: "client",
  async setup(global, options) {
    delete global.localStorage;
    delete global.sessionStorage;
    return jsdom.setup(global, options);
  },
} satisfies Environment;
