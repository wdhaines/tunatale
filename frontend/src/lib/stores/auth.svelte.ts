// Session state for the SPA: whether this deployment requires a login at all,
// and who (if anyone) is signed in.
//
// ⚠️ The load-bearing subtlety is that `enabled` has THREE states. The backend
// answers `GET /api/auth/me` with 401 for an anonymous caller *whether or not*
// `auth_enabled` is set (pinned backend-side by
// `test_me_answers_identically_with_the_flag_off`), so a 401 alone does not
// mean "log in". `GET /api/auth/status` is the only thing that can tell them
// apart, and it can itself fail — so:
//
//   true  → the gate is on; a 401 means the session is gone → /login
//   false → the gate is off; a 401 is some other route's problem → do nothing
//   null  → we do not know yet, or the probe failed → redirect NOBODY
//
// Guessing on `null` is the failure this store exists to avoid: guess "on" and
// a dev whose backend is briefly down gets stranded on a login page whose
// submit cannot work either.

import { goto } from "$app/navigation";
import { api } from "$lib/api";

const LOGIN_PATH = "/login";

function createAuthStore() {
  let enabled = $state<boolean | null>(null);
  let email = $state<string | null>(null);
  let ready = $state(false);

  /** Ask the backend whether a login is required. Leaves `enabled` null on failure. */
  async function refreshStatus(): Promise<void> {
    try {
      enabled = (await api.getAuthStatus()).auth_enabled;
    } catch {
      enabled = null;
    }
  }

  /** `/login?next=<where we are>` — omitting `next` when it would just say "/". */
  function loginHref(): string {
    const here = `${window.location.pathname}${window.location.search}`;
    return here === "/" ? LOGIN_PATH : `${LOGIN_PATH}?next=${encodeURIComponent(here)}`;
  }

  async function init(): Promise<void> {
    await refreshStatus();
    // Cleared unconditionally: `init` re-runs (tests, and any future re-boot)
    // and a stale email surviving a gate that has since turned off would have
    // the UI claiming a session that cannot exist.
    email = null;
    if (enabled === true) {
      try {
        email = (await api.getMe()).email;
      } catch {
        // The ordinary "not logged in" answer. Not an error worth surfacing.
      }
    }
    ready = true;
  }

  async function login(emailInput: string, password: string): Promise<void> {
    // Rejections propagate: the login form owns the error message, and it is
    // the only place in the app where a failed request has a specific reader.
    email = (await api.login(emailInput, password)).email;
  }

  async function logout(): Promise<void> {
    try {
      await api.logout();
    } catch {
      // Most likely because the cookie was already dead — which is the state
      // we are trying to reach. Clearing regardless is the honest answer.
    }
    email = null;
    await goto(LOGIN_PATH);
  }

  /**
   * Send the visitor to the login page, remembering where they were.
   *
   * A no-op when they are already there: the login page's own requests can
   * 401, and redirecting would remount it mid-typing and nest one `next`
   * inside another.
   */
  async function redirectToLogin(): Promise<void> {
    if (window.location.pathname === LOGIN_PATH) return;
    await goto(loginHref());
  }

  /**
   * A data endpoint answered 401 mid-session. Registered as the API client's
   * unauthorized handler by the root layout.
   */
  async function handleUnauthorized(): Promise<void> {
    if (enabled === null) await refreshStatus();
    if (enabled !== true) return;
    email = null;
    await redirectToLogin();
  }

  return {
    get enabled(): boolean | null {
      return enabled;
    },
    get email(): string | null {
      return email;
    },
    /** True once `init` has settled — the layout holds the app back until then. */
    get ready(): boolean {
      return ready;
    },
    /** The gate is on and nobody is signed in. Never true while `enabled` is null. */
    get requiresLogin(): boolean {
      return enabled === true && email === null;
    },
    init,
    login,
    logout,
    redirectToLogin,
    handleUnauthorized,
  };
}

export const authStore = createAuthStore();
