import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import { createRawSnippet } from "svelte";
import Banner from "./Banner.svelte";

// One banner for every app-level notice (LLM failure, preset change, missing
// glosses), so they look and behave the same: a tone, a message, at most one
// action. The tone decides the ARIA role — a failure interrupts (alert), a
// degraded-but-working notice does not (status).

const text = (s: string) => createRawSnippet(() => ({ render: () => `<span>${s}</span>` }));

describe("Banner", () => {
  it("renders its message", () => {
    const { getByText } = render(Banner, { props: { children: text("Glosses missing") } });
    expect(getByText("Glosses missing")).toBeTruthy();
  });

  it("is an alert in danger tone", () => {
    const { container } = render(Banner, { props: { tone: "danger", children: text("x") } });
    const el = container.querySelector(".banner")!;
    expect(el.getAttribute("role")).toBe("alert");
    expect(el.classList.contains("banner-danger")).toBe(true);
  });

  it("is a status in warning tone", () => {
    const { container } = render(Banner, { props: { tone: "warning", children: text("x") } });
    const el = container.querySelector(".banner")!;
    expect(el.getAttribute("role")).toBe("status");
    expect(el.classList.contains("banner-warning")).toBe(true);
  });

  it("defaults to danger", () => {
    const { container } = render(Banner, { props: { children: text("x") } });
    expect(container.querySelector(".banner-danger")).not.toBeNull();
  });

  it("has no button without an action", () => {
    const { container } = render(Banner, { props: { children: text("x") } });
    expect(container.querySelector("button")).toBeNull();
  });

  it("runs its action on click", async () => {
    const onaction = vi.fn();
    const { getByRole } = render(Banner, {
      props: { children: text("x"), actionLabel: "Re-gloss", onaction },
    });
    await fireEvent.click(getByRole("button", { name: "Re-gloss" }));
    expect(onaction).toHaveBeenCalledOnce();
  });

  it("shows the busy label and disables the action while busy", () => {
    const { getByRole } = render(Banner, {
      props: {
        children: text("x"),
        actionLabel: "Re-gloss",
        busyLabel: "Re-glossing…",
        busy: true,
        onaction: vi.fn(),
      },
    });
    const btn = getByRole("button", { name: "Re-glossing…" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("keeps the action label while busy when no busy label is given", () => {
    const { getByRole } = render(Banner, {
      props: { children: text("x"), actionLabel: "Dismiss", busy: true, onaction: vi.fn() },
    });
    expect((getByRole("button", { name: "Dismiss" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
