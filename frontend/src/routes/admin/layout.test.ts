/**
 * Tests for /admin/+layout.svelte — a render-children wrapper.
 *
 * The layout is one line ({@render children()}), so the only behavior to
 * verify is that slot content reaches the DOM. createRawSnippet builds a
 * snippet prop directly without needing a wrapper .svelte helper.
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/svelte";
import { createRawSnippet } from "svelte";
import AdminLayout from "./+layout.svelte";

describe("/admin/+layout.svelte", () => {
  it("renders its children snippet", () => {
    const children = createRawSnippet(() => ({
      render: () => `<div data-testid="slot">admin section content</div>`,
    }));
    const { getByTestId } = render(AdminLayout, { props: { children } });
    expect(getByTestId("slot").textContent).toBe("admin section content");
  });
});
