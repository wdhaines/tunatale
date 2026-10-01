/**
 * Manual mode at CREATE time, on the Lessons index (bd tunatale-jmwb).
 *
 * Manual mode already existed for sessions but was id-scoped, so reaching it
 * meant generating a session first and discarding its dialogue. The user:
 * "I don't like having to do auto mode, then rewrite with Claude in the lesson
 * page." A story call is the most expensive thing TT does, and it was being spent
 * on output already decided against.
 *
 * ⚠️ THE WORD LIST IS THE THING UNDER TEST. Nothing server-side remembers what the
 * draft prompt asked for — deliberately, so there is no story-less session row to
 * render or orphan — so the page carries it from copy to paste. If it were dropped,
 * the import would 422 (empty list) or, worse, a re-selecting server would score
 * the dialogue against words the learner never saw. `passes the prompt's words back`
 * is the guard, and it is why these tests assert the SECOND argument rather than
 * just that import was called.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import Page from "./+page.svelte";

const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

vi.mock("$lib/api", () => ({
  LANGUAGE_STORAGE_KEY: "tt-language",
  api: {
    listCurricula: vi.fn(),
    startPlan: vi.fn(),
    getCurriculumProgress: vi.fn(),
    deleteCurriculum: vi.fn(),
    listReviewSessions: vi.fn(),
    createReviewSession: vi.fn(),
    getReviewSessionDraftPrompt: vi.fn(),
    createReviewSessionFromPaste: vi.fn(),
  },
}));

vi.mock("$lib/stores/listened.svelte", () => ({
  listenedStore: { has: vi.fn().mockReturnValue(false) },
}));

import { api } from "$lib/api";
import { unpinReviewWords } from "$lib/reviewDraft";
const mockListCurricula = vi.mocked(api.listCurricula);
const mockGetCurriculumProgress = vi.mocked(api.getCurriculumProgress);
const mockListSessions = vi.mocked(api.listReviewSessions);
const mockCreateSession = vi.mocked(api.createReviewSession);
const mockDraftPrompt = vi.mocked(api.getReviewSessionDraftPrompt);
const mockFromPaste = vi.mocked(api.createReviewSessionFromPaste);

const WORDS = ["oppføre", "dessuten"];
const STORY = '{"title":"By Hand","scenes":[]}';

beforeEach(() => {
  vi.clearAllMocks();
  // The pin outlives a page on purpose, so it would outlive a test too.
  unpinReviewWords();
  localStorage.clear();
  mockListCurricula.mockResolvedValue([]);
  mockGetCurriculumProgress.mockResolvedValue([]);
  mockListSessions.mockResolvedValue([]);
  mockDraftPrompt.mockResolvedValue({
    system_prompt: "SYS",
    user_prompt: "USER",
    review_words: WORDS,
  });
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

/** Copy the prompt, which is also what stashes the pinned word list. */
async function copyPrompt(getByTestId: (id: string) => HTMLElement) {
  await fireEvent.click(getByTestId("copy-btn"));
  await waitFor(() => expect(mockDraftPrompt).toHaveBeenCalled());
}

describe("manual mode on the index", () => {
  it("is available without generating anything first", async () => {
    const { findByText } = render(Page);
    expect(await findByText(/write one by hand instead/i)).toBeTruthy();
  });

  it("starts collapsed, so the one-click auto path stays the default", async () => {
    const { container, findByText } = render(Page);
    await findByText(/write one by hand instead/i);

    const fold = container.querySelector("details.manual-session");
    expect(fold).toBeTruthy();
    expect((fold as HTMLDetailsElement).open).toBe(false);
  });

  it("fetches the draft prompt without creating a session", async () => {
    const { findByText, getByTestId } = render(Page);
    await findByText(/write one by hand instead/i);

    await copyPrompt(getByTestId);

    expect(mockCreateSession).not.toHaveBeenCalled();
  });

  it("copies the system and user prompt together", async () => {
    const { findByText, getByTestId } = render(Page);
    await findByText(/write one by hand instead/i);

    await copyPrompt(getByTestId);

    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith("SYS\n\nUSER"));
  });

  it("passes the prompt's words back when importing the paste", async () => {
    mockFromPaste.mockResolvedValue({ id: "new-1", warnings: [] } as never);
    const { findByText, getByTestId, getByPlaceholderText } = render(Page);
    await findByText(/write one by hand instead/i);
    await copyPrompt(getByTestId);

    await fireEvent.input(getByPlaceholderText(/paste story json/i), {
      target: { value: STORY },
    });
    await fireEvent.click(getByTestId("import-btn"));

    await waitFor(() =>
      expect(mockFromPaste).toHaveBeenCalledWith(STORY, WORDS, expect.any(String)),
    );
  });

  it("still has the words after leaving the page and coming back", async () => {
    // The 2026-10-01 failure, step for step: copy the prompt, open another page
    // (the session auto mode had just made), come back, paste. The words were
    // component state, so the remount sent an empty list and the server refused
    // it.
    mockFromPaste.mockResolvedValue({ id: "new-1", warnings: [] } as never);
    const first = render(Page);
    await first.findByText(/write one by hand instead/i);
    await copyPrompt(first.getByTestId);
    first.unmount();

    const { findByText, getByTestId, getByPlaceholderText } = render(Page);
    await findByText(/write one by hand instead/i);
    await fireEvent.input(getByPlaceholderText(/paste story json/i), {
      target: { value: STORY },
    });
    await fireEvent.click(getByTestId("import-btn"));

    await waitFor(() =>
      expect(mockFromPaste).toHaveBeenCalledWith(STORY, WORDS, expect.any(String)),
    );
  });

  it("refuses a paste with no pinned words, and says how to recover", async () => {
    const { findByText, getByTestId, getByPlaceholderText } = render(Page);
    await findByText(/write one by hand instead/i);

    await fireEvent.input(getByPlaceholderText(/paste story json/i), {
      target: { value: STORY },
    });
    await fireEvent.click(getByTestId("import-btn"));

    expect(await findByText(/copy the prompt again/i)).toBeTruthy();
    // Not sent at all: the server would only answer with a validator message
    // about a field the learner never typed.
    expect(mockFromPaste).not.toHaveBeenCalled();
  });

  it("forgets the words once the session exists", async () => {
    // Otherwise the next paste, written from no prompt at all, would be scored
    // against this session's words.
    mockFromPaste.mockResolvedValue({ id: "new-1", warnings: [] } as never);
    const first = render(Page);
    await first.findByText(/write one by hand instead/i);
    await copyPrompt(first.getByTestId);
    await fireEvent.input(first.getByPlaceholderText(/paste story json/i), {
      target: { value: STORY },
    });
    await fireEvent.click(first.getByTestId("import-btn"));
    await waitFor(() => expect(mockGoto).toHaveBeenCalledWith("/review-sessions/new-1"));
    first.unmount();

    const { findByText, getByTestId, getByPlaceholderText } = render(Page);
    await findByText(/write one by hand instead/i);
    await fireEvent.input(getByPlaceholderText(/paste story json/i), {
      target: { value: STORY },
    });
    await fireEvent.click(getByTestId("import-btn"));

    expect(await findByText(/copy the prompt again/i)).toBeTruthy();
    expect(mockFromPaste).toHaveBeenCalledTimes(1);
  });

  it("opens the session it just created", async () => {
    mockFromPaste.mockResolvedValue({ id: "new-1", warnings: [] } as never);
    const { findByText, getByTestId, getByPlaceholderText } = render(Page);
    await findByText(/write one by hand instead/i);
    await copyPrompt(getByTestId);

    await fireEvent.input(getByPlaceholderText(/paste story json/i), {
      target: { value: STORY },
    });
    await fireEvent.click(getByTestId("import-btn"));

    // A clean import navigates straight through. Warnings are what hold the
    // learner on the page (behind a Continue button) so they can be read first —
    // hence the empty `warnings` here, which is the clean-import case.
    await waitFor(() => expect(mockGoto).toHaveBeenCalledWith("/review-sessions/new-1"));
  });
});
