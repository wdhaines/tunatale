/**
 * Tests for /invite — the redeem page an invited person lands on.
 *
 * The token arrives in the URL FRAGMENT (never the query string), is prefilled
 * into an editable field, and is only ever sent in the redeem request body.
 * Redeeming creates the account but no session, so success goes to /login.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";

const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

vi.mock("$lib/api", () => ({
  api: { redeemInvite: vi.fn() },
}));

import { api } from "$lib/api";
import Invite from "./+page.svelte";

const mockRedeem = vi.mocked(api.redeemInvite);

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/invite");
  mockRedeem.mockResolvedValue({ email: "a@b.c" });
});

describe("/invite", () => {
  it("prefills the token field from the URL fragment", async () => {
    window.history.replaceState(null, "", "/invite#abc123");
    const { findByLabelText } = render(Invite);

    const field = (await findByLabelText(/token/i)) as HTMLInputElement;

    await waitFor(() => expect(field.value).toBe("abc123"));
  });

  it("leaves the token field empty with no fragment, and lets one be typed", async () => {
    const { getByLabelText } = render(Invite);
    const field = getByLabelText(/token/i) as HTMLInputElement;

    expect(field.value).toBe("");
    await fireEvent.input(field, { target: { value: "pasted-token" } });
    expect(field.value).toBe("pasted-token");
  });

  it("redeems with the trimmed token and email, then sends the person to /login", async () => {
    window.history.replaceState(null, "", "/invite#abc123");
    const { getByLabelText, getByRole, findByLabelText } = render(Invite);
    await findByLabelText(/token/i);

    await fireEvent.input(getByLabelText(/token/i), { target: { value: "  abc123  " } });
    await fireEvent.input(getByLabelText(/email/i), { target: { value: "  a@b.c  " } });
    await fireEvent.input(getByLabelText(/password/i), { target: { value: "hunter2" } });
    await fireEvent.click(getByRole("button", { name: /create account/i }));

    await waitFor(() => expect(mockRedeem).toHaveBeenCalledWith("abc123", "a@b.c", "hunter2"));
    await waitFor(() => expect(mockGoto).toHaveBeenCalledWith("/login"));
  });

  it("shows the server's reason and stays put when the invite is dead", async () => {
    mockRedeem.mockRejectedValue(
      new Error("POST /api/auth/invite/redeem: Invalid or expired invite"),
    );
    const { getByLabelText, getByRole, findByRole } = render(Invite);

    await fireEvent.input(getByLabelText(/token/i), { target: { value: "dead" } });
    await fireEvent.input(getByLabelText(/email/i), { target: { value: "a@b.c" } });
    await fireEvent.input(getByLabelText(/password/i), { target: { value: "hunter2" } });
    await fireEvent.click(getByRole("button", { name: /create account/i }));

    const alert = await findByRole("alert");
    expect(alert.textContent).toContain("Invalid or expired invite");
    expect(mockGoto).not.toHaveBeenCalled();
  });

  it("disables the button while the request is in flight", async () => {
    let release: (value: { email: string }) => void = () => {};
    mockRedeem.mockReturnValue(new Promise((resolve) => (release = resolve)));
    const { getByLabelText, getByRole } = render(Invite);

    await fireEvent.input(getByLabelText(/token/i), { target: { value: "abc123" } });
    await fireEvent.input(getByLabelText(/email/i), { target: { value: "a@b.c" } });
    await fireEvent.input(getByLabelText(/password/i), { target: { value: "hunter2" } });
    await fireEvent.click(getByRole("button", { name: /create account/i }));

    const button = getByRole("button", { name: /creating account/i });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    release({ email: "a@b.c" });
    await waitFor(() => expect(mockGoto).toHaveBeenCalled());
  });
});
