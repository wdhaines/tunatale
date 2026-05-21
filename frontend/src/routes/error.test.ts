/**
 * Tests for the global +error.svelte error boundary route.
 *
 * Mocks $app/stores.page via a vi.hoisted mutable holder so each test can
 * set status/error before render() without re-mocking per test.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render } from "@testing-library/svelte";

const hoisted = vi.hoisted(() => ({
  pageData: { status: 500, error: null } as {
    status: number;
    error: { message?: string } | null;
  },
}));

vi.mock("$app/stores", () => ({
  page: {
    subscribe: (run: (v: typeof hoisted.pageData) => void) => {
      run(hoisted.pageData);
      return () => {};
    },
  },
}));

import ErrorPage from "./+error.svelte";

beforeEach(() => {
  hoisted.pageData = { status: 500, error: null };
});

describe("+error.svelte", () => {
  it("renders the HTTP status code from $page.status", () => {
    hoisted.pageData = { status: 404, error: { message: "Not found" } };
    const { getByRole } = render(ErrorPage);
    expect(getByRole("heading", { level: 1 }).textContent).toBe("404");
  });

  it("renders the error message when $page.error.message is present", () => {
    hoisted.pageData = { status: 500, error: { message: "Boom" } };
    const { getByText } = render(ErrorPage);
    expect(getByText("Boom")).toBeTruthy();
  });

  it("falls back to the default message when $page.error is null", () => {
    hoisted.pageData = { status: 500, error: null };
    const { getByText } = render(ErrorPage);
    expect(getByText("Something went wrong")).toBeTruthy();
  });

  it("falls back to the default message when error.message is missing", () => {
    hoisted.pageData = { status: 500, error: {} };
    const { getByText } = render(ErrorPage);
    expect(getByText("Something went wrong")).toBeTruthy();
  });

  it('renders a "back to TunaTale" link that points to the home route', () => {
    hoisted.pageData = { status: 404, error: null };
    const { getByRole } = render(ErrorPage);
    const link = getByRole("link", { name: /back to tunatale/i });
    expect(link.getAttribute("href")).toBe("/");
  });
});
