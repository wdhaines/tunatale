/**
 * The review-session reader (bd tunatale-dswn, under tunatale-9p9d).
 *
 * ⚠️ THE ROUTE IS NOT UNDER /c/[curriculumId]/. A session belongs to no
 * curriculum, and a URL implying one would be the same placement error the epic
 * exists to correct — the one that put a "Review story" button beside
 * Regenerate — expressed in a path instead of a button.
 *
 * WHY IT IS THIS SMALL: LessonPlayer takes `audio: LessonAudio` and keys on
 * `audio.lesson_id` alone. It knows nothing about curricula or days, so it is
 * reused unchanged; `audio_files` rows already land under the session id and
 * GET /api/audio/lesson/{id} serves them. The 1223 lines of the lesson page are
 * day/curriculum glue around a player that never needed either.
 *
 * `test_it_reads_as_a_scene` is the one that matters to a human: step 8 of the
 * manual shakedown ("read the story it wrote") had nowhere to happen until this
 * route existed. With the theme gone, coherence is the only constraint left.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";

const mockGoto = vi.fn();
vi.mock("$app/navigation", () => ({ goto: (...args: unknown[]) => mockGoto(...args) }));

vi.mock("$lib/api", () => ({
  api: {
    getReviewSession: vi.fn(),
    getLessonAudio: vi.fn(),
    renderReviewSession: vi.fn(),
    audioUrl: vi.fn((id: string) => `/audio/${id}`),
  },
}));

import { api } from "$lib/api";
import Page from "./+page.svelte";
import { load } from "./+page";

const mockGetSession = vi.mocked(api.getReviewSession);
const mockGetAudio = vi.mocked(api.getLessonAudio);
const mockRender = vi.mocked(api.renderReviewSession);

type Loaded = { session: { title: string }; audio: { audio_id: string } | null };
const loaded = (r: unknown) => r as Loaded;

function sessionBody(overrides: Record<string, unknown> = {}) {
  return {
    id: "sess-1",
    session_date: "2026-09-02",
    title: "A Missed Train",
    language_code: "no",
    key_phrases: [{ phrase: "å oppføre seg", translation: "to behave" }],
    review_requested: ["oppføre", "dessuten"],
    review_used: ["oppføre"],
    // ⚠️ SHAPED FROM THE LIVE API, not invented. natural_speed really does open
    // with two NARRATOR lines in English — the section's own name, then the
    // scene label — before any dialogue. The earlier fixture here omitted them,
    // which made a naive `phrases` filter look correct while it would have
    // rendered "Natural Speed" as the first line of the scene.
    sections: [
      {
        type: "natural_speed",
        phrases: [
          { text: "Natural Speed", role: "narrator", language_code: "en", voice_id: "v" },
          { text: "On the Platform", role: "narrator", language_code: "en", voice_id: "v" },
          {
            text: "Toget er dessverre forsinket.",
            role: "female-1",
            language_code: "no",
            voice_id: "v",
          },
          { text: "Da rekker vi ikke møtet.", role: "male-1", language_code: "no", voice_id: "v" },
        ],
      },
      {
        // The bilingual section buildScenes pulls each line's gloss from: an L2
        // line, then its narrator translation, in that order.
        type: "translated",
        phrases: [
          {
            text: "Toget er dessverre forsinket.",
            role: "female-1",
            language_code: "no",
            voice_id: "v",
          },
          {
            text: "The train is unfortunately delayed.",
            role: "narrator",
            language_code: "en",
            voice_id: "v",
          },
          { text: "Da rekker vi ikke møtet.", role: "male-1", language_code: "no", voice_id: "v" },
          {
            text: "Then we will not make the meeting.",
            role: "narrator",
            language_code: "en",
            voice_id: "v",
          },
        ],
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("load", () => {
  it("returns the session and its audio", async () => {
    mockGetSession.mockResolvedValue(sessionBody());
    mockGetAudio.mockResolvedValue({ audio_id: "a1", lesson_id: "sess-1", sections: [] });

    const data = loaded(await load({ params: { sessionId: "sess-1" } } as never));

    expect(data.session.title).toBe("A Missed Train");
    expect(data.audio?.audio_id).toBe("a1");
  });

  it("still loads when there is no audio yet", async () => {
    // A session is readable the moment it is generated; rendering is a separate,
    // slower step. Blocking the page on audio would make a new session look broken.
    mockGetSession.mockResolvedValue(sessionBody());
    mockGetAudio.mockRejectedValue(new Error("404"));

    const data = loaded(await load({ params: { sessionId: "sess-1" } } as never));

    expect(data.session.title).toBe("A Missed Train");
    expect(data.audio).toBeNull();
  });

  it("404s when the session does not exist", async () => {
    mockGetSession.mockRejectedValue(new Error("nope"));

    await expect(load({ params: { sessionId: "ghost" } } as never)).rejects.toMatchObject({
      status: 404,
    });
  });
});

describe("the reader", () => {
  const data = (overrides: Record<string, unknown> = {}) => ({
    session: sessionBody(),
    audio: null,
    ...overrides,
  });

  it("names the session by date, never by a day", () => {
    const { getByText, queryByText } = render(Page, { props: { data: data() } });

    expect(getByText("A Missed Train")).toBeTruthy();
    expect(getByText(/2 September/)).toBeTruthy();
    expect(queryByText(/Day \d/)).toBeNull();
  });

  it("reads as a scene — the dialogue is on the page", () => {
    // Step 8 of the manual shakedown. No test can judge whether it reads WELL;
    // this only guarantees there is something to judge.
    const { getByText } = render(Page, { props: { data: data() } });

    expect(getByText("Toget er dessverre forsinket.")).toBeTruthy();
    expect(getByText("Da rekker vi ikke møtet.")).toBeTruthy();
  });

  it("does not open the scene with the section's own name", () => {
    // The bug this caught — found by running the real app, not by a test.
    // natural_speed's first phrase is the narrator saying "Natural Speed"; a
    // raw phrase list renders it as line one. buildScenes drops it.
    const { queryByText } = render(Page, { props: { data: data() } });

    expect(queryByText("Natural Speed")).toBeNull();
  });

  it("shows the English under each line", () => {
    // The gloss is what makes it readable to someone still learning — and it
    // comes free from buildScenes, which a hand-rolled phrase filter would not
    // have given.
    const { getByText } = render(Page, { props: { data: data() } });

    expect(getByText("The train is unfortunately delayed.")).toBeTruthy();
  });

  it("promotes the scene label to a heading rather than a line", () => {
    const { getByRole } = render(Page, { props: { data: data() } });

    expect(getByRole("heading", { name: "On the Platform" })).toBeTruthy();
  });

  it("says which forgotten words it managed to use", () => {
    const { getByText } = render(Page, { props: { data: data() } });

    expect(getByText(/reused 1 of 2/i)).toBeTruthy();
  });

  it("shows no readout when the session was never measured", () => {
    const session = sessionBody({ review_requested: [], review_used: [] });
    const { queryByText } = render(Page, { props: { data: data({ session }) } });

    expect(queryByText(/reused/i)).toBeNull();
  });

  it("offers to prepare the audio when there is none", async () => {
    mockRender.mockResolvedValue({ audio_id: "a1", lesson_id: "sess-1", sections: [] });
    mockGetAudio.mockResolvedValue({ audio_id: "a1", lesson_id: "sess-1", sections: [] });
    const { getByRole, findByTestId } = render(Page, { props: { data: data() } });

    await fireEvent.click(getByRole("button", { name: /prepare audio/i }));

    expect(await findByTestId("session-player")).toBeTruthy();
    expect(mockRender).toHaveBeenCalledWith("sess-1");
  });

  it("reports a failed render instead of silently doing nothing", async () => {
    mockRender.mockRejectedValue(new Error("ffmpeg not found"));
    const { getByRole, findByText } = render(Page, { props: { data: data() } });

    await fireEvent.click(getByRole("button", { name: /prepare audio/i }));

    expect(await findByText(/ffmpeg not found/i)).toBeTruthy();
  });

  it("plays straight away when the audio already exists", async () => {
    const audio = { audio_id: "a1", lesson_id: "sess-1", sections: [] };
    const { findByTestId, queryByRole } = render(Page, { props: { data: data({ audio }) } });

    expect(await findByTestId("session-player")).toBeTruthy();
    expect(queryByRole("button", { name: /prepare audio/i })).toBeNull();
  });

  it("offers a way back to the list", () => {
    const { getByRole } = render(Page, { props: { data: data() } });

    const back = getByRole("link", { name: /lessons|back/i });
    expect(back.getAttribute("href")).toBe("/");
  });
});
