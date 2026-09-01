import { builtinEnvironments } from "vitest/environments";

import type { Environment } from "vitest/environments";

const jsdom = builtinEnvironments.jsdom;

/**
 * jsdom, minus Node's built-in Storage globals.
 *
 * See vite.config.ts::test.environment for the full reasoning.
 */
export default {
  name: "jsdom-storage",
  transformMode: "web",
  async setup(global, options) {
    delete global.localStorage;
    delete global.sessionStorage;
    return jsdom.setup(global, options);
  },
} satisfies Environment;
