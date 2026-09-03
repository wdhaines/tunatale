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
const mockInvalidateAll = vi.fn();
vi.mock("$app/navigation", () => ({
  goto: (...args: unknown[]) => mockGoto(...args),
  invalidateAll: () => mockInvalidateAll(),
}));

// The confirm is a real dialog component elsewhere; here it is the boundary, so
// stub it to "yes" and let the tests assert what the click DOES.
const mockConfirm = vi.fn().mockResolvedValue(true);
vi.mock("$lib/components/ConfirmDialog.svelte", () => ({
  default: () => null,
  confirmDialog: (...args: unknown[]) => mockConfirm(...args),
}));

vi.mock("$lib/stores/listened.svelte", () => ({
  listenedStore: { has: vi.fn().mockReturnValue(false), refresh: vi.fn() },
}));

vi.mock("$lib/api", () => ({
  api: {
    getReviewSession: vi.fn(),
    getLessonAudio: vi.fn(),
    renderReviewSession: vi.fn(),
    getTranscript: vi.fn(),
    submitDrill: vi.fn(),
    fetchLessonReviewQueue: vi.fn(),
    getListenPreview: vi.fn(),
    undoGrade: vi.fn(),
    createSRSItem: vi.fn(),
    createBaseCard: vi.fn(),
    audioUrl: vi.fn((id: string) => `/audio/${id}`),
    audioZipUrl: vi.fn((id: string) => `/audio/lesson/${id}/zip`),
    regenerateReviewSession: vi.fn(),
    createReviewSession: vi.fn(),
  },
}));

import { api } from "$lib/api";
import { lessonModePref } from "$lib/stores/lessonModePref.svelte";
import { listenedStore } from "$lib/stores/listened.svelte";
import Page from "./+page.svelte";
import { load } from "./+page";

const mockGetSession = vi.mocked(api.getReviewSession);
const mockGetAudio = vi.mocked(api.getLessonAudio);
const mockRender = vi.mocked(api.renderReviewSession);
const mockSessionTranscript = vi.mocked(api.getTranscript);

const TRANSCRIPT = {
  lesson_id: "sess-1",
  key_phrases: [{ phrase: "å oppføre seg", translation: "to behave" }],
  dialogue_lines: [
    {
      role: "female-1",
      sentence: "Toget er dessverre forsinket.",
      words: [
        { surface: "Toget", prefix_punct: "", suffix_punct: "", lemma: "tog" },
        { surface: "forsinket", prefix_punct: "", suffix_punct: ".", lemma: "forsinke" },
      ],
    },
  ],
} as never;

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

function transcriptWithWord(overrides: Record<string, unknown> = {}) {
  return {
    lesson_id: "sess-1",
    key_phrases: [],
    dialogue_lines: [
      {
        role: "female-1",
        sentence: "Toget er forsinket",
        words: [
          {
            surface: "Toget",
            lemma: "tog",
            srs_state: "learning",
            srs_item_id: 42,
            translation: null,
            collocation_span_id: null,
            collocation_start: false,
            collocation_srs_state: null,
            collocation_lemma: null,
            collocation_translation: null,
            card_type: null,
            active_state: "learning",
            active_direction: "recognition",
            is_due: true,
            progress: null,
            inflectable: false,
            inflection_feature: null,
            known_marked: false,
            ...overrides,
          },
        ],
      },
    ],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom has no matchMedia, and the shared Read/Listen toggle reads it on mount
  // to pick the viewport default. Desktop, so Read mode is the default and the
  // transcript renders — the same stub the lesson page's tests use.
  (window as unknown as { matchMedia: unknown }).matchMedia = vi.fn(() => ({
    matches: false,
    media: "(max-width: 640px)",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  // lessonModePref is a module singleton shared with the lesson page, so a
  // Listen click in one test leaks into the next. Reset it explicitly rather
  // than depending on test order.
  lessonModePref.set("read");
  vi.mocked(listenedStore.has).mockReturnValue(false);
  mockSessionTranscript.mockResolvedValue(TRANSCRIPT);
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

  it("reads through the SAME transcript component a lesson uses", async () => {
    // The point of this whole page. An earlier version rendered its own list of
    // dialogue lines because the transcript endpoint missed on a session id;
    // that fork was worse within a day. If this stops rendering Transcript, the
    // fork is back.
    const { findByText } = render(Page, { props: { data: data() } });

    expect(await findByText(/Toget/)).toBeTruthy();
  });

  it("grading a word goes through the same actions the lesson page uses", async () => {
    // The user's ask: "as much like the lesson page as possible, just with a
    // different source". The word popover's grade button is the SAME control the
    // lesson page's own tests drive — it exists here because both pages call one
    // createReadingActions, not because it was re-implemented.
    //
    // The refetch assertion is the guard against a future fork: it must re-read
    // the SESSION transcript and never the lesson one.
    mockSessionTranscript.mockResolvedValue(transcriptWithWord() as never);
    vi.mocked(api.submitDrill).mockResolvedValue({} as never);

    const { findByRole } = render(Page, { props: { data: data() } });

    await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));

    await vi.waitFor(() => {
      expect(api.submitDrill).toHaveBeenCalledWith(42, "recognition", "good");
      expect(mockSessionTranscript).toHaveBeenCalledTimes(2);
    });
  });

  it("introduces an unknown word against the SESSION's language", async () => {
    // Covers the other branch of the shared actions, and pins that the language
    // comes from the session rather than from any curriculum.
    mockSessionTranscript.mockResolvedValue(
      transcriptWithWord({ active_state: "unknown" }) as never,
    );
    vi.mocked(api.createBaseCard).mockResolvedValue({ id: 7 } as never);
    vi.mocked(api.submitDrill).mockResolvedValue({} as never);

    const { findByRole } = render(Page, { props: { data: data() } });
    await fireEvent.click(await findByRole("button", { name: "Start learning" }));

    await vi.waitFor(() =>
      expect(api.createBaseCard).toHaveBeenCalledWith(
        expect.objectContaining({ language_code: "no", surface: "Toget" }),
      ),
    );
  });

  it("surfaces a failed grade instead of swallowing it", async () => {
    mockSessionTranscript.mockResolvedValue(transcriptWithWord() as never);
    vi.mocked(api.submitDrill).mockRejectedValue(new Error("already synced to Anki"));

    const { findByRole, findByText } = render(Page, { props: { data: data() } });
    await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));

    expect(await findByText(/already synced to Anki/)).toBeTruthy();
  });

  it("a graded word can be undone, exactly as on a lesson", async () => {
    // Completes the Got it ✓ -> Undo ↩ cycle here too. The undo state lives in
    // the shared actions, so this is the same code path the lesson page's own
    // undo test drives — which is the point of the extraction.
    mockSessionTranscript.mockResolvedValue(transcriptWithWord() as never);
    vi.mocked(api.submitDrill).mockResolvedValue({} as never);
    vi.mocked(api.undoGrade).mockResolvedValue({} as never);

    const { findByRole } = render(Page, { props: { data: data() } });
    await fireEvent.click(await findByRole("button", { name: "Got it ✓" }));
    await fireEvent.click(await findByRole("button", { name: "Undo ↩" }));

    await vi.waitFor(() => expect(api.undoGrade).toHaveBeenCalledWith(42, "recognition"));
  });

  it("offers the same listen flow a lesson does", async () => {
    // Nothing about listening was ever day-scoped; the only thing that had made
    // it look lesson-only was store.get_lesson(id) in five handlers.
    mockSessionTranscript.mockResolvedValue(transcriptWithWord() as never);
    const { findByRole } = render(Page, { props: { data: data() } });

    expect(await findByRole("button", { name: "Mark as Listened" })).toBeTruthy();
  });

  it("shows how much of the session is known", async () => {
    // MasteryLine is pure arithmetic over the transcript — no curriculum, no
    // day, no API call. It works here for exactly that reason.
    mockSessionTranscript.mockResolvedValue(transcriptWithWord() as never);
    const { findByText } = render(Page, { props: { data: data() } });

    expect(await findByText(/\d+%/)).toBeTruthy();
  });

  it("sends Check your work back to the SESSION, not to a curriculum", async () => {
    // /review used to take ?c= purely to rebuild a "back to lesson" url, which
    // assumed the content lived under a curriculum. It takes a return PATH now.
    mockSessionTranscript.mockResolvedValue(transcriptWithWord() as never);
    vi.mocked(listenedStore.has).mockReturnValue(true);
    vi.mocked(api.fetchLessonReviewQueue).mockResolvedValue({
      queue: [{ id: 1 }],
      has_unreviewed_listen: true,
    } as never);

    const { findByRole } = render(Page, { props: { data: data() } });

    const link = await findByRole("link", { name: /Check your work/ });
    expect(link.getAttribute("href")).toBe("/review?lesson=sess-1&back=/review-sessions/sess-1");
  });

  it("opens the same listen preview a lesson does, and commits back into it", async () => {
    // Exercises the whole listen path on a session: the modal opens against the
    // SESSION id, and finishing it re-reads the transcript through the shared
    // content route. Both were lesson-only until get_readable_content landed.
    mockSessionTranscript.mockResolvedValue(transcriptWithWord() as never);
    vi.mocked(api.getListenPreview).mockResolvedValue({
      candidates: [],
      key_phrases: [],
      daily_new_cap: 20,
      introduced_today: 0,
    } as never);

    const { findByRole } = render(Page, { props: { data: data() } });
    await fireEvent.click(await findByRole("button", { name: "Mark as Listened" }));

    expect(await findByRole("dialog", { name: "Listen preview" })).toBeTruthy();
    await vi.waitFor(() => expect(api.getListenPreview).toHaveBeenCalledWith("sess-1"));
  });

  it("has the Read/Listen toggle, and Listen hides the transcript", async () => {
    // Same control, same store, same behaviour as a lesson: the mode is one
    // persisted preference, so switching here and opening a lesson keeps it.
    mockSessionTranscript.mockResolvedValue(transcriptWithWord() as never);
    const { findByRole, queryByText, getByRole } = render(Page, { props: { data: data() } });

    expect(await findByRole("button", { name: "Read" })).toBeTruthy();
    await findByRole("button", { name: "Got it ✓" });

    await fireEvent.click(getByRole("button", { name: "Listen" }));

    expect(queryByText("Toget")).toBeNull();
  });

  it("reads its transcript through the shared content route", async () => {
    // There is no session-specific transcript endpoint any more, and no lesson
    // one either: /api/srs/content/{id}/transcript resolves both. What is worth
    // pinning is that this page passes its OWN id to it.
    render(Page, { props: { data: data() } });

    await vi.waitFor(() => expect(mockSessionTranscript).toHaveBeenCalledWith("sess-1"));
  });

  it("shows the shared placeholder while the words are still coming", async () => {
    // The transcript runs the lemmatizer and is slow; the lesson page shows the
    // same placeholder for the same reason.
    mockSessionTranscript.mockReturnValue(new Promise(() => {}) as never);
    const { container } = render(Page, { props: { data: data() } });

    expect(container.querySelector(".transcript")?.textContent?.trim()).not.toBe("");
  });

  it("says so plainly when the transcript cannot be had", async () => {
    mockSessionTranscript.mockRejectedValue(new Error("backend down"));
    const { findByText } = render(Page, { props: { data: data() } });

    expect(await findByText(/no transcript available/i)).toBeTruthy();
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
    const { getByRole, container } = render(Page, { props: { data: data() } });

    await fireEvent.click(getByRole("button", { name: /prepare audio/i }));

    // The shared player itself, not a wrapper this page owns — LessonReader
    // renders it, so asserting on a local test id would have been asserting on
    // markup that no longer exists.
    await vi.waitFor(() => expect(container.querySelector("section.player")).toBeTruthy());
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
    const { container, queryByRole } = render(Page, { props: { data: data({ audio }) } });

    expect(container.querySelector("section.player")).toBeTruthy();
    expect(queryByRole("button", { name: /prepare audio/i })).toBeNull();
  });

  it("offers a way back to the list", () => {
    const { getByRole } = render(Page, { props: { data: data() } });

    const back = getByRole("link", { name: /lessons|back/i });
    expect(back.getAttribute("href")).toBe("/");
  });

  /**
   * Session tools. Reported by the user as "the lesson tools are also missing —
   * I'd love to have Opus improve the dialog, but I can't": the page rendered
   * the dialogue with no way to download it and no way to ask for a better one.
   */
  describe("session tools", () => {
    const withAudio = () => ({
      audio_id: "a1",
      lesson_id: "sess-1",
      sections: [{ audio_id: "s1", title: "Natural Speed" }],
    });

    it("offers the downloads once audio exists", () => {
      const { getByRole } = render(Page, { props: { data: data({ audio: withAudio() }) } });

      expect(getByRole("link", { name: /download all sections/i }).getAttribute("href")).toBe(
        "/audio/lesson/sess-1/zip",
      );
      expect(getByRole("link", { name: "Natural Speed" }).getAttribute("href")).toBe("/audio/s1");
    });

    it("rewrites THIS session rather than minting a new one", async () => {
      vi.mocked(api.regenerateReviewSession).mockResolvedValue({
        id: "sess-1",
        session_date: "2026-09-02",
        title: "A Better Dialogue",
        review_requested: [],
        review_used: [],
        warnings: [],
      });
      const { getByRole } = render(Page, { props: { data: data() } });

      await fireEvent.click(getByRole("button", { name: /rewrite dialogue/i }));

      expect(api.regenerateReviewSession).toHaveBeenCalledWith("sess-1");
      // The distinction the whole route exists for: creating one would leave
      // this session behind under a new id at a new URL.
      expect(api.createReviewSession).not.toHaveBeenCalled();
    });

    it("stops offering audio that belongs to the dialogue it replaced", async () => {
      vi.mocked(api.regenerateReviewSession).mockResolvedValue({
        id: "sess-1",
        session_date: "2026-09-02",
        title: "A Better Dialogue",
        review_requested: [],
        review_used: [],
        warnings: [],
      });
      const { getByRole, findByRole } = render(Page, {
        props: { data: data({ audio: withAudio() }) },
      });

      await fireEvent.click(getByRole("button", { name: /rewrite dialogue/i }));

      // The server dropped those rows; a page still linking them would serve a
      // recording of a script that is no longer on screen.
      expect(await findByRole("button", { name: /prepare audio/i })).toBeTruthy();
    });

    it("asks first, and does nothing when the answer is no", async () => {
      mockConfirm.mockResolvedValueOnce(false);
      const { getByRole } = render(Page, { props: { data: data() } });

      await fireEvent.click(getByRole("button", { name: /rewrite dialogue/i }));

      expect(mockConfirm).toHaveBeenCalled();
      expect(api.regenerateReviewSession).not.toHaveBeenCalled();
    });

    it("shows the failure instead of leaving the button spinning", async () => {
      vi.mocked(api.regenerateReviewSession).mockRejectedValue(
        new Error("daily token budget exhausted"),
      );
      const { getByRole, findByText } = render(Page, { props: { data: data() } });

      await fireEvent.click(getByRole("button", { name: /rewrite dialogue/i }));

      expect(await findByText(/daily token budget exhausted/i)).toBeTruthy();
      expect(getByRole("button", { name: /rewrite dialogue/i })).not.toHaveProperty(
        "disabled",
        true,
      );
    });

    it("survives a transcript that fails to come back after the rewrite", async () => {
      // The rewrite already succeeded server-side at this point, so a failed
      // re-fetch must not surface as a failed rewrite — the new dialogue IS
      // stored, and telling the user otherwise would invite a second LLM call.
      vi.mocked(api.regenerateReviewSession).mockResolvedValue({
        id: "sess-1",
        session_date: "2026-09-02",
        title: "A Better Dialogue",
        review_requested: [],
        review_used: [],
        warnings: [],
      });
      // Every call, not `…Once`: onMount fetches the transcript too, so a single
      // one-shot rejection is consumed by the mount and never reaches the path
      // under test — which is why this first read as "the button vanished".
      mockSessionTranscript.mockRejectedValue(new Error("lemmatizer cold"));
      const { getByRole, findByRole, queryByText } = render(Page, { props: { data: data() } });

      await fireEvent.click(getByRole("button", { name: /rewrite dialogue/i }));

      // findBy, not getBy: the label is "Rewriting…" until the promise chain
      // settles, so a synchronous read here is a race with its own success case.
      expect(await findByRole("button", { name: /rewrite dialogue/i })).toBeTruthy();
      expect(queryByText(/lemmatizer cold/i)).toBeNull();
    });

    it("explains what rewriting trades, on demand", async () => {
      const { getByRole, findByText } = render(Page, { props: { data: data() } });

      await fireEvent.click(getByRole("button", { name: /what does rewriting do/i }));

      // Not /decayed/ — the page's own standing blurb already says that, and the
      // assertion matched it in both panels. Pick a phrase only the help has.
      expect(await findByText(/keeping its date/i)).toBeTruthy();
    });
  });
});
