/**
 * Tests for /login — the only route a logged-out visitor may render.
 *
 * The `next` parameter is the interesting part: it is attacker-controllable
 * (it arrives in a URL anyone can send you) and it feeds a navigation, which
 * is the classic open-redirect shape. The sanitiser tests below are the point
 * of this file, not decoration.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

const nav = vi.hoisted(() => ({ search: "" }));
vi.mock("$app/stores", () => ({
  page: {
    subscribe: vi.fn((cb) => {
      cb({ url: new URL(`http://localhost/login${nav.search}`) });
      return () => {};
    }),
  },
}));

const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

vi.mock("$lib/api", () => ({
  api: { login: vi.fn(), getAuthStatus: vi.fn(), getMe: vi.fn(), logout: vi.fn() },
  setUnauthorizedHandler: vi.fn(),
}));

import { api } from "$lib/api";
import { authStore } from "$lib/stores/auth.svelte";
import Login from "./+page.svelte";

const mockLogin = vi.mocked(api.login);

beforeEach(() => {
  vi.clearAllMocks();
  nav.search = "";
  mockLogin.mockResolvedValue({ email: "a@b.c" });
});

async function submit(
  getByLabelText: (t: RegExp) => HTMLElement,
  getByRole: (r: string, o?: object) => HTMLElement,
) {
  await fireEvent.input(getByLabelText(/email/i), { target: { value: "a@b.c" } });
  await fireEvent.input(getByLabelText(/password/i), { target: { value: "hunter2" } });
  await fireEvent.click(getByRole("button", { name: /sign in/i }));
}

describe("/login", () => {
  it("signs in and lands on the root when no destination was requested", async () => {
    const { getByLabelText, getByRole } = render(Login);

    await submit(getByLabelText, getByRole);

    await waitFor(() => expect(mockLogin).toHaveBeenCalledWith("a@b.c", "hunter2"));
    expect(authStore.email).toBe("a@b.c");
    expect(mockGoto).toHaveBeenCalledWith("/");
  });

  it("returns the user to the route they originally asked for", async () => {
    nav.search = "?next=%2Fc%2Fabc%2Fl%2Fxyz%3Fmode%3Dlisten";
    const { getByLabelText, getByRole } = render(Login);

    await submit(getByLabelText, getByRole);

    await waitFor(() => expect(mockGoto).toHaveBeenCalledWith("/c/abc/l/xyz?mode=listen"));
  });

  it("shows the server's reason and stays put when the credentials are wrong", async () => {
    mockLogin.mockRejectedValue(new Error("POST /api/auth/login: Invalid credentials"));
    const { getByLabelText, getByRole, findByRole } = render(Login);

    await submit(getByLabelText, getByRole);

    const alert = await findByRole("alert");
    expect(alert.textContent).toContain("Invalid credentials");
    expect(mockGoto).not.toHaveBeenCalled();
  });

  it("clears a previous error when the next attempt succeeds", async () => {
    mockLogin.mockRejectedValueOnce(new Error("POST /api/auth/login: Invalid credentials"));
    const { getByLabelText, getByRole, findByRole, queryByRole } = render(Login);

    await submit(getByLabelText, getByRole);
    await findByRole("alert");
    await submit(getByLabelText, getByRole);

    await waitFor(() => expect(queryByRole("alert")).toBeNull());
  });

  it("disables the button while the request is in flight", async () => {
    let release: (value: { email: string }) => void = () => {};
    mockLogin.mockReturnValue(new Promise((resolve) => (release = resolve)));
    const { getByLabelText, getByRole } = render(Login);

    await submit(getByLabelText, getByRole);

    const button = getByRole("button", { name: /signing in/i });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    release({ email: "a@b.c" });
    await waitFor(() => expect(mockGoto).toHaveBeenCalled());
  });

  describe("the `next` parameter is attacker-controllable", () => {
    // Each of these is a real open-redirect payload: a login link mailed to a
    // user that hands them back to a lookalike host once they authenticate.
    // The rule is "same-origin paths only", enforced by shape, and the
    // fallback is always the root.
    it.each([
      ["an absolute URL", "https://evil.example/steal"],
      ["a protocol-relative URL", "//evil.example/steal"],
      ["a backslash-disguised host", "/\\evil.example"],
      ["a non-path value", "evil.example"],
      ["an empty value", ""],
    ])("refuses %s and goes to the root instead", async (_label, value) => {
      nav.search = `?next=${encodeURIComponent(value)}`;
      const { getByLabelText, getByRole } = render(Login);

      await submit(getByLabelText, getByRole);

      await waitFor(() => expect(mockGoto).toHaveBeenCalledWith("/"));
    });
  });
});
