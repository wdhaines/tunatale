/**
 * Tests for PlannerChat.svelte.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import PlannerChat from "./PlannerChat.svelte";
import type { ChatMessage } from "$lib/planner";

const messages: ChatMessage[] = [
  { role: "user", content: "Plan a market trip" },
  { role: "planner", content: "Here's a first arc" },
  { role: "event", content: "Committed days 1-3." },
];

function setup(overrides: Record<string, unknown> = {}) {
  const onSend = vi.fn().mockResolvedValue(undefined);
  const utils = render(PlannerChat, {
    props: { messages, pending: false, batchSize: 5, onSend, ...overrides },
  });
  return { onSend, ...utils };
}

describe("PlannerChat", () => {
  it("renders messages with role-specific styling", () => {
    const { container } = setup();
    expect(container.querySelector(".msg-user")?.textContent).toContain("Plan a market trip");
    expect(container.querySelector(".msg-planner")?.textContent).toContain("Here's a first arc");
    expect(container.querySelector(".msg-event")?.textContent).toContain("Committed days 1-3.");
  });

  it("shows an empty-state hint when there are no messages", () => {
    const { getByText } = setup({ messages: [] });
    expect(getByText(/describe what you want to learn/i)).toBeTruthy();
  });

  it("sends the typed message and clears the textarea", async () => {
    const { onSend, getByRole, getByPlaceholderText } = setup();
    const textarea = getByPlaceholderText(/message the planner/i) as HTMLTextAreaElement;
    await fireEvent.input(textarea, { target: { value: "add food day" } });
    await fireEvent.click(getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledWith("add food day");
    expect(textarea.value).toBe("");
  });

  it("does not send an empty or whitespace-only message", async () => {
    const { onSend, getByRole, getByPlaceholderText } = setup();
    const sendButton = getByRole("button", { name: "Send" }) as HTMLButtonElement;
    expect(sendButton.disabled).toBe(true);
    await fireEvent.input(getByPlaceholderText(/message the planner/i), {
      target: { value: "   " },
    });
    expect(sendButton.disabled).toBe(true);
    await fireEvent.click(sendButton);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("Enter sends; Shift+Enter does not", async () => {
    const { onSend, getByPlaceholderText } = setup();
    const textarea = getByPlaceholderText(/message the planner/i);
    await fireEvent.input(textarea, { target: { value: "hello" } });
    await fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    await fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hello");
  });

  it("disables Send and quick action while pending", () => {
    const { getByRole } = setup({ pending: true });
    expect((getByRole("button", { name: /thinking/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((getByRole("button", { name: /plan the next/i }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("quick action sends 'Plan the next N days.' using the batch size", async () => {
    const { onSend, getByRole } = setup({ batchSize: 3 });
    await fireEvent.click(getByRole("button", { name: "Plan the next 3 days" }));
    expect(onSend).toHaveBeenCalledWith("Plan the next 3 days.");
  });

  it("clamps the batch-size input on change", async () => {
    const { getByLabelText } = setup();
    const input = getByLabelText(/days per batch/i) as HTMLInputElement;
    await fireEvent.input(input, { target: { value: "99" } });
    await fireEvent.change(input, { target: { value: "99" } });
    expect(input.value).toBe("14");
  });

  it("exposes focusInput for the Revise affordance", async () => {
    const { component, getByPlaceholderText } = setup();
    (component as unknown as { focusInput: () => void }).focusInput();
    expect(document.activeElement).toBe(getByPlaceholderText(/message the planner/i));
  });
});
