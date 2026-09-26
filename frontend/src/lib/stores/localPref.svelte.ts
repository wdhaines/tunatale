// One localStorage-backed preference, for every store in this folder that is a
// plain "read a value, write a value" preference with no side effect of its own.
//
// theme and language are deliberately NOT built on this: they act on the value
// (a DOM attribute, an API fetch), so they need to know WHEN it is applied, not
// just what it is.
//
// Three things each of those stores needed, and each of them had spelled them
// slightly differently:
//
//   - a self-init on the first read. Consumers deeper in the tree (LessonPlayer)
//     mount BEFORE the layout's onMount calls init(), so the first read has to
//     apply a stored override itself or a direct lesson-page load ignores it.
//   - localStorage is not guaranteed. Private mode and blocked site data throw
//     on BOTH get and set, and a preference that breaks the player it is
//     attached to is worse than one that quietly does not persist.
//   - localStorage does not exist at all without a window (SSR), where the
//     default stands until something calls set().
//
// The KEY and the stored string format belong to the caller, not to this file:
// users already have these values on disk, so parse/serialize are the only
// thing that decides what a stored preference means, and both must be
// byte-compatible with what shipped.

import { untrack } from "svelte";

export interface LocalPrefOptions<T> {
  /**
   * Raw stored string, null when the key is absent, to the value. Must be
   * total: garbage in, the default out. `parse(null)` is the default, and is
   * also what the value starts as.
   */
  parse: (raw: string | null) => T;
  /** The inverse, for `set`. */
  serialize: (value: T) => string;
}

export interface LocalPref<T> {
  readonly value: T;
  /** Seed from storage. Idempotent; the first read does it anyway. */
  init(): void;
  /** Apply and persist. */
  set(next: T): void;
}

export function createLocalPref<T>(key: string, opts: LocalPrefOptions<T>): LocalPref<T> {
  // Undefined means "no value seeded", NOT a value: the default is parse(null),
  // and it is computed on the read that needs it rather than at module load.
  // Eagerly would be wrong twice over — the module loads before the environment
  // is ready (jsdom has no matchMedia, and a real load can race the same way),
  // and parse is the caller's, so its preconditions are its own business.
  let value = $state<T | undefined>(undefined);
  // Deliberately NOT $state: whether we have seeded is not a value a consumer
  // can depend on, and making it one would have every reader re-run on the seed.
  let initialized = false;

  function init(): void {
    initialized = true;
    if (typeof localStorage === "undefined") return; // SSR: keep the default
    let raw: string | null = null;
    try {
      raw = localStorage.getItem(key);
    } catch {
      // Private mode / blocked site data: degrade to the default rather than
      // breaking whatever the preference is attached to.
      raw = null;
    }
    value = opts.parse(raw);
  }

  function set(next: T): void {
    initialized = true;
    value = next;
    if (typeof localStorage === "undefined") return;
    try {
      localStorage.setItem(key, opts.serialize(next));
    } catch {
      // An unwritable store must not break the toggle. The in-memory value
      // above still tracks the choice for the rest of the session.
    }
  }

  return {
    get value(): T {
      // untrack: the first read can happen inside a $derived (lessonModePref's
      // mode is), and writing a $state from inside one is a Svelte error. The
      // seed is a one-off, not a dependency of whatever read us.
      if (!initialized) untrack(init);
      // Still unseeded only under SSR, where there was no storage to read.
      return value === undefined ? opts.parse(null) : value;
    },
    init,
    set,
  };
}
