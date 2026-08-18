/**
 * Auth store — what the SPA knows about the session, and what it does when
 * that knowledge changes.
 *
 * The central asymmetry these tests pin: `enabled` has THREE states, not two.
 * `true` and `false` are answers from the backend; `null` means the question
 * has not been answered (boot in flight, or the status probe itself failed).
 * A `null` must never redirect anyone — see
 * "an unknown gate state never redirects".
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("$lib/api", () => ({
  api: { getAuthStatus: vi.fn(), getMe: vi.fn(), login: vi.fn(), logout: vi.fn() },
  setUnauthorizedHandler: vi.fn(),
}));

const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

import { api } from "$lib/api";

const mockStatus = vi.mocked(api.getAuthStatus);
const mockMe = vi.mocked(api.getMe);
const mockLogin = vi.mocked(api.login);
const mockLogout = vi.mocked(api.logout);

interface AuthStore {
  enabled: boolean | null;
  email: string | null;
  ready: boolean;
  requiresLogin: boolean;
  init: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  handleUnauthorized: () => Promise<void>;
}

let store: AuthStore;

/** Point `window.location` at *path* — the store reads it to build `next`. */
function atPath(path: string): void {
  const url = new URL(`http://localhost${path}`);
  Object.defineProperty(window, "location", {
    value: { pathname: url.pathname, search: url.search, href: url.href },
    writable: true,
    configurable: true,
  });
}

beforeEach(async () => {
  vi.clearAllMocks();
  vi.resetModules();
  atPath("/");
  const mod = await import("./auth.svelte");
  store = mod.authStore as unknown as AuthStore;
});

describe("init", () => {
  it("records the gate as off and never asks who you are", async () => {
    mockStatus.mockResolvedValue({ auth_enabled: false });

    await store.init();

    expect(store.enabled).toBe(false);
    expect(store.email).toBeNull();
    expect(store.ready).toBe(true);
    expect(store.requiresLogin).toBe(false);
    // `me` answers 401 with the gate off exactly as it does with the gate on,
    // so asking is pure noise — one guaranteed console error on every dev boot.
    expect(mockMe).not.toHaveBeenCalled();
  });

  it("records the signed-in user when the gate is on and the cookie is live", async () => {
    mockStatus.mockResolvedValue({ auth_enabled: true });
    mockMe.mockResolvedValue({ email: "a@b.c" });

    await store.init();

    expect(store.enabled).toBe(true);
    expect(store.email).toBe("a@b.c");
    expect(store.requiresLogin).toBe(false);
  });

  it("requires a login when the gate is on and `me` says nobody", async () => {
    mockStatus.mockResolvedValue({ auth_enabled: true });
    mockMe.mockRejectedValue(new Error("GET /api/auth/me: "));

    await store.init();

    expect(store.enabled).toBe(true);
    expect(store.email).toBeNull();
    expect(store.requiresLogin).toBe(true);
  });

  it("leaves the gate UNKNOWN when the probe itself fails, and locks nobody out", async () => {
    // Backend unreachable at boot. Guessing "on" would strand the user on a
    // login page whose submit cannot work either; guessing "off" would be a
    // claim we have no evidence for. Neither — stay null, redirect nobody,
    // and let the first real 401 re-ask.
    mockStatus.mockRejectedValue(new Error("fetch failed"));

    await store.init();

    expect(store.enabled).toBeNull();
    expect(store.ready).toBe(true);
    expect(store.requiresLogin).toBe(false);
    expect(mockGoto).not.toHaveBeenCalled();
  });
});

describe("login", () => {
  it("stores the email the backend echoes back", async () => {
    mockLogin.mockResolvedValue({ email: "a@b.c" });

    await store.login("A@B.C ", "hunter2");

    expect(mockLogin).toHaveBeenCalledWith("A@B.C ", "hunter2");
    expect(store.email).toBe("a@b.c");
  });

  it("propagates a failure and leaves the session empty", async () => {
    mockLogin.mockRejectedValue(new Error("POST /api/auth/login: Invalid credentials"));

    await expect(store.login("a@b.c", "wrong")).rejects.toThrow("Invalid credentials");

    expect(store.email).toBeNull();
  });
});

describe("logout", () => {
  it("clears the session and lands on the login page", async () => {
    mockStatus.mockResolvedValue({ auth_enabled: true });
    mockMe.mockResolvedValue({ email: "a@b.c" });
    await store.init();
    mockLogout.mockResolvedValue({ status: "logged out" });

    await store.logout();

    expect(store.email).toBeNull();
    expect(mockGoto).toHaveBeenCalledWith("/login");
  });

  it("clears the session even when the server call fails", async () => {
    // The cookie may already be dead — that is the most likely reason the call
    // failed. Leaving the UI claiming a live session would be the one wrong
    // answer available here.
    mockStatus.mockResolvedValue({ auth_enabled: true });
    mockMe.mockResolvedValue({ email: "a@b.c" });
    await store.init();
    mockLogout.mockRejectedValue(new Error("network"));

    await store.logout();

    expect(store.email).toBeNull();
    expect(mockGoto).toHaveBeenCalledWith("/login");
  });
});

describe("handleUnauthorized", () => {
  it("redirects to the login page, preserving where the user was", async () => {
    mockStatus.mockResolvedValue({ auth_enabled: true });
    mockMe.mockResolvedValue({ email: "a@b.c" });
    await store.init();
    atPath("/c/abc/l/xyz?mode=listen");

    await store.handleUnauthorized();

    expect(store.email).toBeNull();
    expect(mockGoto).toHaveBeenCalledWith("/login?next=%2Fc%2Fabc%2Fl%2Fxyz%3Fmode%3Dlisten");
  });

  it("does not redirect when the gate is off", async () => {
    // With auth disabled a 401 is somebody else's bug — a route that 401s for
    // its own reasons — and a login page cannot fix it.
    mockStatus.mockResolvedValue({ auth_enabled: false });
    await store.init();

    await store.handleUnauthorized();

    expect(mockGoto).not.toHaveBeenCalled();
  });

  it("re-asks the gate when it is unknown, then redirects if it is on", async () => {
    mockStatus.mockRejectedValueOnce(new Error("fetch failed"));
    await store.init();
    expect(store.enabled).toBeNull();

    mockStatus.mockResolvedValue({ auth_enabled: true });
    atPath("/review");
    await store.handleUnauthorized();

    expect(mockGoto).toHaveBeenCalledWith("/login?next=%2Freview");
  });

  it("does not redirect off the login page onto itself", async () => {
    // The login page's own probes can 401; a redirect here would remount the
    // page mid-typing and bury `next` inside another `next`.
    mockStatus.mockResolvedValue({ auth_enabled: true });
    mockMe.mockRejectedValue(new Error("401"));
    await store.init();
    atPath("/login?next=%2Freview");

    await store.handleUnauthorized();

    expect(mockGoto).not.toHaveBeenCalled();
  });

  it("redirects without a `next` when the user was at the root", async () => {
    mockStatus.mockResolvedValue({ auth_enabled: true });
    mockMe.mockResolvedValue({ email: "a@b.c" });
    await store.init();
    atPath("/");

    await store.handleUnauthorized();

    expect(mockGoto).toHaveBeenCalledWith("/login");
  });
});
