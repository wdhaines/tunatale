/**
 * TunaTaleAPI client unit tests.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { TunaTaleAPI } from "./api";
import { makeSRSItemDetail } from "../test/factories";

const BASE = "http://test-backend";

function mockOk(json: unknown): Response {
  return { ok: true, json: async () => json } as Response;
}

function mockFail(statusText = "Internal Server Error"): Response {
  return { ok: false, statusText } as Response;
}

function mockFailBody(body: unknown, status = 500, statusText = ""): Response {
  return { ok: false, status, statusText, json: async () => body } as Response;
}

describe("BASE_URL SSR branch", () => {
  afterEach(async () => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("BASE_URL is https://localhost:8000 in SSR when SSL is enabled", async () => {
    vi.stubGlobal("window", undefined);
    vi.stubEnv("VITE_SSL_ENABLED", "true");
    vi.resetModules();
    const { BASE_URL } = await import("./api");
    expect(BASE_URL).toBe("https://localhost:8000");
  });

  it("BASE_URL is http://localhost:8000 in SSR when SSL is not enabled", async () => {
    vi.stubGlobal("window", undefined);
    vi.stubEnv("VITE_SSL_ENABLED", "");
    vi.resetModules();
    const { BASE_URL } = await import("./api");
    expect(BASE_URL).toBe("http://localhost:8000");
  });

  it("BASE_URL honors API_PORT in SSR (matches the Vite proxy target)", async () => {
    vi.stubGlobal("window", undefined);
    vi.stubEnv("VITE_SSL_ENABLED", "");
    vi.stubEnv("API_PORT", "8001");
    vi.resetModules();
    const { BASE_URL } = await import("./api");
    expect(BASE_URL).toBe("http://localhost:8001");
  });
});

describe("TunaTaleAPI", () => {
  let api: TunaTaleAPI;

  beforeEach(() => {
    api = new TunaTaleAPI(BASE);
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("curriculum", () => {
    it("listCurricula calls GET /api/curriculum", async () => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            mockOk([{ id: "1", topic: "coffee", created_at: "2026-04-10 12:00:00" }]),
          ),
      );

      const result = await api.listCurricula();

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum`);
      expect(result).toHaveLength(1);
      expect(result[0].created_at).toBe("2026-04-10 12:00:00");
    });

    it("getCurriculum calls GET /api/curriculum/:id", async () => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(mockOk({ id: "abc", topic: "coffee", language_code: "sl", days: 3 })),
      );

      const result = await api.getCurriculum("abc");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum/abc`);
      expect(result.id).toBe("abc");
    });

    it("getCurriculum throws on 404", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Not Found")));

      await expect(api.getCurriculum("missing")).rejects.toThrow(
        "GET /api/curriculum/missing: Not Found",
      );
    });

    it("startPlan calls POST /api/curriculum/plan", async () => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            mockOk({ id: "trip-1", topic: "trip", language_code: "sl", cefr_level: "B1", days: 0 }),
          ),
      );

      const result = await api.startPlan("trip", "B1");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/plan`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ topic: "trip", cefr_level: "B1" }),
        }),
      );
      expect(result.id).toBe("trip-1");
      expect(result.cefr_level).toBe("B1");
    });

    it("startPlan defaults cefr_level to A2", async () => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            mockOk({ id: "t-1", topic: "t", language_code: "sl", cefr_level: "A2", days: 0 }),
          ),
      );

      await api.startPlan("t");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/plan`,
        expect.objectContaining({ body: JSON.stringify({ topic: "t", cefr_level: "A2" }) }),
      );
    });

    it("planTurn calls POST /api/curriculum/:id/plan/turn", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(mockOk({ reply: "Here you go", proposed: null })),
      );

      const result = await api.planTurn("trip-1", "plan 3 days", 3);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/trip-1/plan/turn`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ message: "plan 3 days", batch_size: 3 }),
        }),
      );
      expect(result.reply).toBe("Here you go");
      expect(result.proposed).toBeNull();
    });

    it("planTurn defaults batch_size to 5 and surfaces 502 detail", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(mockFailBody({ detail: "Expected 5 days, got 1" }, 502)),
      );

      await expect(api.planTurn("trip-1", "plan")).rejects.toThrow(
        "POST /api/curriculum/trip-1/plan/turn: Expected 5 days, got 1",
      );
      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/trip-1/plan/turn`,
        expect.objectContaining({ body: JSON.stringify({ message: "plan", batch_size: 5 }) }),
      );
    });

    it("commitPlan calls POST /api/curriculum/:id/plan/commit", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ id: "trip-1", days: 3 })));

      const result = await api.commitPlan("trip-1");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/trip-1/plan/commit`,
        expect.objectContaining({ method: "POST" }),
      );
      expect(result.days).toBe(3);
    });

    it("resetPlanChat calls POST /api/curriculum/:id/plan/reset", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ reply_count_cleared: 3 })));

      const result = await api.resetPlanChat("trip-1");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/trip-1/plan/reset`,
        expect.objectContaining({ method: "POST" }),
      );
      expect(result.reply_count_cleared).toBe(3);
    });

    it("resetPlanChat returns 0 for empty chat", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ reply_count_cleared: 0 })));

      const result = await api.resetPlanChat("trip-1");

      expect(result.reply_count_cleared).toBe(0);
    });

    it("sendPlanFeedback calls POST /api/curriculum/:id/plan/feedback", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(mockOk({ feedback: [{ day: 2, note: "too fast" }] })),
      );

      const result = await api.sendPlanFeedback("trip-1", 2, "too fast");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/trip-1/plan/feedback`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ day: 2, note: "too fast" }),
        }),
      );
      expect(result.feedback).toHaveLength(1);
    });

    it("getPlanSource calls GET /api/curriculum/:id/source", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          mockOk({
            id: "trip-1",
            topic: "trip",
            language_code: "sl",
            cefr_level: "A2",
            days: [],
          }),
        ),
      );

      const result = await api.getPlanSource("trip-1");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum/trip-1/source`);
      expect(result.id).toBe("trip-1");
    });

    it("importPlan calls POST /api/curriculum/import", async () => {
      const file = { topic: "trip", language_code: "sl", cefr_level: "A2", days: [] };
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(mockOk({ id: "trip-9", topic: "trip", language_code: "sl", days: 0 })),
      );

      const result = await api.importPlan(file);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/import`,
        expect.objectContaining({ method: "POST", body: JSON.stringify(file) }),
      );
      expect(result.id).toBe("trip-9");
    });

    it("getLessonByDay calls GET /api/curriculum/:cid/days/:n/lesson", async () => {
      const mockDetail = {
        id: "l1",
        title: "Day 1",
        language_code: "sl",
        sections: [],
        key_phrases: [],
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(mockDetail)));

      const result = await api.getLessonByDay("cid-1", 1);

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum/cid-1/days/1/lesson`);
      expect(result.id).toBe("l1");
    });

    it("getLessonByDay throws on 404", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Not Found")));

      await expect(api.getLessonByDay("cid-1", 1)).rejects.toThrow(
        "GET /api/curriculum/cid-1/days/1/lesson: Not Found",
      );
    });

    it("setGenerationMode calls POST /api/curriculum/:id/generation-mode", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ mode: "manual" })));

      const result = await api.setGenerationMode("trip-1", "manual");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/trip-1/generation-mode`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ mode: "manual" }),
        }),
      );
      expect(result.mode).toBe("manual");
    });

    it("setGenerationMode throws on 404", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Not Found")));

      await expect(api.setGenerationMode("missing", "manual")).rejects.toThrow(
        "POST /api/curriculum/missing/generation-mode: Not Found",
      );
    });

    it("deleteCurriculumDay calls DELETE /api/curriculum/:id/days/:day", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ deleted_day: 2, days: 3 })));

      const result = await api.deleteCurriculumDay("trip-1", 2);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/trip-1/days/2`,
        expect.objectContaining({ method: "DELETE" }),
      );
      expect(result).toEqual({ deleted_day: 2, days: 3 });
    });

    it("deleteCurriculumDay throws on 404", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Not Found")));

      await expect(api.deleteCurriculumDay("missing", 2)).rejects.toThrow(
        "DELETE /api/curriculum/missing/days/2: Not Found",
      );
    });

    it("getPlanTurnPrompt calls POST /api/curriculum/:id/plan/turn/prompt", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(mockOk({ system_prompt: "sys", user_prompt: "plan 3 days" })),
      );

      const result = await api.getPlanTurnPrompt("trip-1", "plan 3 days", 3);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/trip-1/plan/turn/prompt`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ message: "plan 3 days", batch_size: 3 }),
        }),
      );
      expect(result.system_prompt).toBe("sys");
      expect(result.user_prompt).toBe("plan 3 days");
    });

    it("getPlanTurnPrompt defaults batch_size to 5", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(mockOk({ system_prompt: "sys", user_prompt: "msg" })),
      );

      await api.getPlanTurnPrompt("trip-1", "msg");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/trip-1/plan/turn/prompt`,
        expect.objectContaining({
          body: JSON.stringify({ message: "msg", batch_size: 5 }),
        }),
      );
    });

    it("planTurn passes pasted_response when provided", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ reply: "ok", proposed: null })));

      await api.planTurn("trip-1", "plan 1 day", 1, "Here are the days\n```json\n{...}\n```");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/trip-1/plan/turn`,
        expect.objectContaining({
          body: JSON.stringify({
            message: "plan 1 day",
            batch_size: 1,
            pasted_response: "Here are the days\n```json\n{...}\n```",
          }),
        }),
      );
    });

    it("planTurn omits pasted_response when not provided", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ reply: "ok", proposed: null })));

      await api.planTurn("trip-1", "plan", 5);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/curriculum/trip-1/plan/turn`,
        expect.objectContaining({
          body: JSON.stringify({ message: "plan", batch_size: 5 }),
        }),
      );
    });
  });

  describe("story", () => {
    it("getLesson calls GET /api/story/:id", async () => {
      const mockDetail = {
        id: "l1",
        title: "Day 1",
        language_code: "sl",
        sections: [
          {
            type: "key_phrases",
            phrases: [
              {
                text: "dober dan",
                role: "female-1",
                language_code: "sl",
                voice_id: "sl-SI-PetraNeural",
              },
            ],
          },
        ],
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(mockDetail)));

      const result = await api.getLesson("l1");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/story/l1`);
      expect(result.id).toBe("l1");
      expect(result.sections[0].phrases[0].text).toBe("dober dan");
    });

    it("getLesson throws on 404", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Not Found")));

      await expect(api.getLesson("missing")).rejects.toThrow("GET /api/story/missing: Not Found");
    });

    it("getStorySource calls GET /api/story/:id/source", async () => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            mockOk({ curriculum_id: "cid-1", day: 1, story: { title: "Kavarna" } }),
          ),
      );

      const result = await api.getStorySource("l1");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/story/l1/source`);
      expect(result.curriculum_id).toBe("cid-1");
      expect(result.story).toEqual({ title: "Kavarna" });
    });

    it("getStorySource throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Not Found")));

      await expect(api.getStorySource("l1")).rejects.toThrow("GET /api/story/l1/source: Not Found");
    });

    it("importStory calls POST /api/story/import", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          mockOk({
            id: "new-l1",
            title: "Day 1 v2",
            sections: [],
            warnings: ["speaker 'barman' is not in the sl voice map"],
          }),
        ),
      );

      const result = await api.importStory({
        curriculum_id: "cid-1",
        day: 1,
        story: { title: "Kavarna v2" },
      });

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/story/import`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ curriculum_id: "cid-1", day: 1, story: { title: "Kavarna v2" } }),
        }),
      );
      expect(result.id).toBe("new-l1");
      expect(result.warnings).toHaveLength(1);
    });

    it("importStory throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Bad Request")));

      await expect(api.importStory({ curriculum_id: "cid-1", day: 1, story: {} })).rejects.toThrow(
        "POST /api/story/import: Bad Request",
      );
    });

    it("importStory passes raw when provided", async () => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(mockOk({ id: "new-l1", title: "Day 1", sections: [], warnings: [] })),
      );

      const rawText = "Here is the story\n```json\n{...}\n```";
      await api.importStory({ curriculum_id: "cid-1", day: 1, raw: rawText });

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/story/import`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ curriculum_id: "cid-1", day: 1, raw: rawText }),
        }),
      );
    });

    it("getStoryPrompt calls GET /api/story/prompt with query params", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(mockOk({ system_prompt: "sys", user_prompt: "user" })),
      );

      const result = await api.getStoryPrompt("cid-1", 3, "DEEPER");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/story/prompt?curriculum_id=cid-1&day=3&strategy=DEEPER`,
      );
      expect(result.system_prompt).toBe("sys");
      expect(result.user_prompt).toBe("user");
    });

    it("getStoryPrompt defaults strategy to WIDER", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(mockOk({ system_prompt: "sys", user_prompt: "user" })),
      );

      await api.getStoryPrompt("cid-1", 1);

      expect(fetch).toHaveBeenCalledWith(expect.stringContaining("strategy=WIDER"));
    });
  });

  describe("audio", () => {
    it("getLessonAudio calls GET /api/audio/lesson/:id", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(mockOk({ audio_id: "a1", lesson_id: "l1", sections: [] })),
      );

      const result = await api.getLessonAudio("l1");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/audio/lesson/l1`);
      expect(result.audio_id).toBe("a1");
    });

    it("renderAudio calls POST /api/audio/render", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(mockOk({ audio_id: "audio-1", lesson_id: "l1" })),
      );

      const result = await api.renderAudio("l1");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/audio/render`,
        expect.objectContaining({ method: "POST" }),
      );
      expect(result.audio_id).toBe("audio-1");
    });

    it("audioUrl returns correct URL", () => {
      const url = api.audioUrl("audio-1");
      expect(url).toBe(`${BASE}/api/audio/audio-1`);
    });

    it("audioZipUrl returns correct ZIP URL", () => {
      const url = api.audioZipUrl("lesson-1");
      expect(url).toBe(`${BASE}/api/audio/lesson/lesson-1/zip`);
    });
  });

  describe("SRS", () => {
    it("getSRSDue calls GET /api/srs/due", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ due: [] })));

      const result = await api.getSRSDue();

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/due`);
      expect(result.due).toEqual([]);
    });

    it("getSRSStats calls GET /api/srs/stats", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ total: 10, due_today: 3 })));

      const result = await api.getSRSStats();

      expect(result.total).toBe(10);
      expect(result.due_today).toBe(3);
    });

    it("getSRSNew calls GET /api/srs/new", async () => {
      const mockResponse = { new: [{ text: "dober dan", translation: "good day" }] };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(mockResponse)));

      const result = await api.getSRSNew();

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/new`);
      expect(result.new).toEqual(mockResponse.new);
    });

    it("getSRSNew throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail()));

      await expect(api.getSRSNew()).rejects.toThrow("GET /api/srs/new: Internal Server Error");
    });

    it("fetchNew calls GET /api/srs/new?direction=recognition&limit=20 by default", async () => {
      const mockItems = [{ id: 1, text: "banka", translation: "bank" }];
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ new: mockItems })));

      const result = await api.fetchNew("recognition");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/new?direction=recognition&limit=20`);
      expect(result).toEqual(mockItems);
    });

    it("fetchNew accepts custom limit", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ new: [] })));

      await api.fetchNew("production", 5);

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/new?direction=production&limit=5`);
    });

    it("markAsListened calls POST /api/srs/listen with lesson_id and empty word_ratings by default", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          mockOk({
            status: "ok",
            registered: 3,
            created: 1,
            graded: 2,
            remaining_candidates: 5,
            listen_count: 4,
          }),
        ),
      );

      const result = await api.markAsListened("lesson-1");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/listen`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ lesson_id: "lesson-1", word_ratings: {} }),
        }),
      );
      expect(result.status).toBe("ok");
      expect(result.registered).toBe(3);
      expect(result.created).toBe(1);
      expect(result.graded).toBe(2);
      expect(result.remaining_candidates).toBe(5);
      expect(result.listen_count).toBe(4);
    });

    it("markAsListened sends word_ratings when provided", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          mockOk({
            status: "ok",
            registered: 5,
            created: 2,
            graded: 3,
            remaining_candidates: 0,
            listen_count: 6,
          }),
        ),
      );

      await api.markAsListened("lesson-1", { banka: "hard", zdravo: "easy" });

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/listen`,
        expect.objectContaining({
          body: JSON.stringify({
            lesson_id: "lesson-1",
            word_ratings: { banka: "hard", zdravo: "easy" },
          }),
        }),
      );
    });

    it("markAsListened throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail()));

      await expect(api.markAsListened("lesson-1")).rejects.toThrow(
        "POST /api/srs/listen: Internal Server Error",
      );
    });

    it("getLessonTranscript calls GET /api/srs/lesson/{id}/transcript", async () => {
      const mockTranscript = {
        lesson_id: "lesson-1",
        key_phrases: [{ phrase: "Zdravo", translation: "Hello" }],
        dialogue_lines: [
          {
            role: "female-1",
            words: [
              {
                surface: "Zdravo",
                lemma: "zdravo",
                srs_state: "unknown",
                card_type: null,
                active_state: "unknown",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
              },
            ],
          },
        ],
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(mockTranscript)));

      const result = await api.getLessonTranscript("lesson-1");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/lesson/lesson-1/transcript`);
      expect(result.lesson_id).toBe("lesson-1");
      expect(result.dialogue_lines).toHaveLength(1);
    });

    it("getLessonTranscript throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail()));

      await expect(api.getLessonTranscript("lesson-1")).rejects.toThrow(
        "GET /api/srs/lesson/lesson-1/transcript: Internal Server Error",
      );
    });

    it("fetchDue calls GET /api/srs/due?direction=recognition", async () => {
      const mockItem = {
        id: 1,
        text: "banka",
        translation: "bank",
        word_count: 1,
        state: "review",
        due_at: "2026-04-18",
        stability: 5.0,
        difficulty: 4.0,
        reps: 3,
        lapses: 0,
        last_review: "2026-04-10",
        language_code: "sl",
        image_url: null,
        directions: {
          recognition: {
            state: "review",
            due_at: "2026-04-18",
            stability: 5.0,
            difficulty: 4.0,
            reps: 3,
            lapses: 0,
            last_review: "2026-04-10",
            anki_card_id: null,
          },
          production: {
            state: "new",
            due_at: "2026-04-18",
            stability: 1.0,
            difficulty: 5.0,
            reps: 0,
            lapses: 0,
            last_review: null,
            anki_card_id: null,
          },
        },
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ due: [mockItem] })));

      const result = await api.fetchDue("recognition");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/due?direction=recognition`);
      expect(result).toHaveLength(1);
      expect(result[0].id).toBe(1);
      expect(result[0].directions?.recognition?.state).toBe("review");
    });

    it("fetchDue supports any direction", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ due: [] })));

      await api.fetchDue("any");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/due?direction=any`);
    });

    it("submitDrill calls POST /api/srs/items/:id/direction/:dir/feedback", async () => {
      const mockResp = {
        status: "ok",
        direction: "recognition",
        new_due_at: "2026-04-25",
        new_state: "review",
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(mockResp)));

      const result = await api.submitDrill(42, "recognition", "good");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/42/direction/recognition/feedback`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ rating: "good" }),
        }),
      );
      expect(result.new_due_at).toBe("2026-04-25");
      expect(result.new_state).toBe("review");
    });

    it("undoGrade calls POST /api/srs/items/:id/direction/:dir/undo", async () => {
      const mockResp = {
        status: "ok",
        direction: "recognition",
        restored_state: "learning",
        restored_due_at: "2026-06-11T04:00:00+00:00",
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(mockResp)));

      const result = await api.undoGrade(42, "recognition");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/42/direction/recognition/undo`,
        expect.objectContaining({ method: "POST" }),
      );
      expect(result.restored_state).toBe("learning");
    });

    it("submitDrill works for production direction", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          mockOk({
            status: "ok",
            direction: "production",
            new_due_at: "2026-04-30",
            new_state: "review",
          }),
        ),
      );

      const result = await api.submitDrill(7, "production", "easy");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/7/direction/production/feedback`,
        expect.objectContaining({ method: "POST", body: JSON.stringify({ rating: "easy" }) }),
      );
      expect(result.new_due_at).toBe("2026-04-30");
    });

    it("createInflectionCloze calls POST /api/srs/inflection-clozes", async () => {
      const mockResp = {
        id: 1,
        was_created: true,
        item: {
          id: 1,
          text: "sem",
          state: "new",
          due_at: "",
          stability: 1,
          difficulty: 5,
          reps: 0,
          lapses: 0,
          last_review: null,
          language_code: "sl",
        },
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(mockResp)));

      const body = {
        surface: "sem",
        lemma: "biti",
        feature: "1sg-past",
        sentence: "jaz sem bil",
        language_code: "sl",
      };
      const result = await api.createInflectionCloze(body);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/inflection-clozes`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify(body),
        }),
      );
      expect(result.id).toBe(1);
      expect(result.was_created).toBe(true);
      expect(result.item.text).toBe("sem");
    });

    it("createInflectionCloze throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail()));

      await expect(
        api.createInflectionCloze({
          surface: "sem",
          lemma: "biti",
          feature: "1sg-past",
          sentence: "jaz sem bil",
          language_code: "sl",
        }),
      ).rejects.toThrow("POST /api/srs/inflection-clozes: Internal Server Error");
    });

    it("createBaseCard calls POST /api/srs/items/base", async () => {
      const mockResp = {
        id: 1,
        was_created: true,
        item: {
          id: 1,
          text: "zdravo",
          state: "new",
          due_at: "",
          stability: 1,
          difficulty: 5,
          reps: 0,
          lapses: 0,
          last_review: null,
          language_code: "sl",
        },
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(mockResp)));

      const body = {
        surface: "zdravo",
        lemma: "zdravo",
        sentence: "Zdravo, kako si?",
        language_code: "sl",
        translation: "hello",
      };
      const result = await api.createBaseCard(body);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/base`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify(body),
        }),
      );
      expect(result.id).toBe(1);
      expect(result.was_created).toBe(true);
      expect(result.item.text).toBe("zdravo");
    });

    it("createBaseCard throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail()));

      await expect(
        api.createBaseCard({
          surface: "zdravo",
          lemma: "zdravo",
          sentence: "Zdravo, kako si?",
          language_code: "sl",
        }),
      ).rejects.toThrow("POST /api/srs/items/base: Internal Server Error");
    });

    it("ignoreLemma calls POST /api/srs/ignored-lemmas", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ status: "ok" })));

      const result = await api.ignoreLemma("banka", "sl");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/ignored-lemmas`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lemma: "banka", language_code: "sl" }),
      });
      expect(result.status).toBe("ok");
    });

    it("ignoreLemma throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail()));
      await expect(api.ignoreLemma("banka", "sl")).rejects.toThrow(
        "POST /api/srs/ignored-lemmas: Internal Server Error",
      );
    });

    it("unignoreLemma calls DELETE /api/srs/ignored-lemmas", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ status: "ok" })));

      const result = await api.unignoreLemma("banka", "sl");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/ignored-lemmas?lemma=banka&language_code=sl`,
        { method: "DELETE" },
      );
      expect(result.status).toBe("ok");
    });

    it("unignoreLemma throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail()));
      await expect(api.unignoreLemma("banka", "sl")).rejects.toThrow(
        "DELETE /api/srs/ignored-lemmas?lemma=banka&language_code=sl: Internal Server Error",
      );
    });
  });

  describe("curriculum progress", () => {
    it("getCurriculumProgress calls GET /api/curriculum/:id/progress", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          mockOk([
            { day: 1, lesson_id: "l1" },
            { day: 3, lesson_id: "l3" },
          ]),
        ),
      );

      const result = await api.getCurriculumProgress("abc");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum/abc/progress`);
      expect(result).toHaveLength(2);
      expect(result[0].day).toBe(1);
      expect(result[0].lesson_id).toBe("l1");
    });

    it("getCurriculumProgress throws on 404", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Not Found")));

      await expect(api.getCurriculumProgress("missing")).rejects.toThrow(
        "GET /api/curriculum/missing/progress: Not Found",
      );
    });
  });

  describe("SRS admin", () => {
    it("createSRSItem calls POST /api/srs/items", async () => {
      const created = {
        id: 1,
        text: "banka",
        translation: "bank",
        state: "new",
        due_at: "2026-04-14",
        stability: 1.0,
        difficulty: 5.0,
        reps: 0,
        lapses: 0,
        last_review: null,
        language_code: "sl",
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(created)));

      const result = await api.createSRSItem({
        text: "banka",
        language_code: "sl",
        word_count: 1,
        translation: "bank",
      });

      const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
      expect(call[0]).toBe(`${BASE}/api/srs/items`);
      expect(JSON.parse(call[1].body)).toEqual({
        text: "banka",
        language_code: "sl",
        word_count: 1,
        translation: "bank",
      });
      expect(result.text).toBe("banka");
    });

    it("listSRSItems calls GET /api/srs/items with no params", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ items: [], total: 0 })));

      const result = await api.listSRSItems();

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/items`);
      expect(result.total).toBe(0);
    });

    it("listSRSItems passes query params when provided", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ items: [], total: 0 })));

      await api.listSRSItems({ search: "dan", limit: 10, offset: 20 });

      const url = vi.mocked(fetch).mock.calls[0][0] as string;
      expect(url).toContain("search=dan");
      expect(url).toContain("limit=10");
      expect(url).toContain("offset=20");
    });

    it("listSRSItems skips undefined params (242 branch)", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ items: [], total: 0 })));

      // Pass params where some values are undefined
      await api.listSRSItems({ search: undefined, limit: 10 });

      const url = vi.mocked(fetch).mock.calls[0][0] as string;
      expect(url).not.toContain("search=");
      expect(url).toContain("limit=10");
    });

    it("updateSRSItem calls PATCH /api/srs/items/:id", async () => {
      const item = {
        id: 1,
        text: "dober",
        translation: "good",
        state: "new" as const,
        due_at: "2026-04-01",
        stability: 1,
        difficulty: 5,
        reps: 0,
        lapses: 0,
        last_review: null,
        language_code: "sl",
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(item)));

      const result = await api.updateSRSItem(1, { text: "dober", translation: "good" });

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/1`,
        expect.objectContaining({ method: "PATCH" }),
      );
      expect(result.id).toBe(1);
    });

    it("deleteSRSItem calls DELETE /api/srs/items/:id", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ status: "deleted" })));

      await api.deleteSRSItem(42);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/42`,
        expect.objectContaining({ method: "DELETE" }),
      );
    });

    it("bulkDeleteSRSItems calls POST /api/srs/items/bulk-delete", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ deleted: 3 })));

      const result = await api.bulkDeleteSRSItems([1, 2, 3]);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/bulk-delete`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ ids: [1, 2, 3] }),
        }),
      );
      expect(result.deleted).toBe(3);
    });

    it("resetSRSItem calls POST /api/srs/items/:id/reset", async () => {
      const item = {
        id: 5,
        text: "test",
        translation: "",
        state: "new" as const,
        due_at: "2026-04-01",
        stability: 1,
        difficulty: 5,
        reps: 0,
        lapses: 0,
        last_review: null,
        language_code: "sl",
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(item)));

      await api.resetSRSItem(5);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/5/reset`,
        expect.objectContaining({ method: "POST" }),
      );
    });

    it("suspendSRSItem calls POST /api/srs/items/:id/suspend with suspended flag", async () => {
      const item = {
        id: 7,
        text: "test",
        translation: "",
        state: "suspended" as const,
        due_at: "2026-04-01",
        stability: 1,
        difficulty: 5,
        reps: 0,
        lapses: 0,
        last_review: null,
        language_code: "sl",
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(item)));

      await api.suspendSRSItem(7, true);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/7/suspend`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ suspended: true }),
        }),
      );
    });

    it("suspendSRSItem includes direction in body when provided", async () => {
      const item = {
        id: 7,
        text: "test",
        translation: "",
        state: "suspended" as const,
        due_at: "2026-04-01",
        stability: 1,
        difficulty: 5,
        reps: 0,
        lapses: 0,
        last_review: null,
        language_code: "sl",
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(item)));

      await api.suspendSRSItem(7, true, "production");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/7/suspend`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ suspended: true, direction: "production" }),
        }),
      );
    });

    it("restoreKnown calls POST /api/srs/items/:id/restore-known", async () => {
      const item = {
        id: 3,
        text: "zdravo",
        translation: "",
        state: "learning" as const,
        due_at: "2026-04-14",
        stability: 1.0,
        difficulty: 5.0,
        reps: 0,
        lapses: 0,
        last_review: null,
        language_code: "sl",
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(item)));

      const result = await api.restoreKnown(3);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/3/restore-known`,
        expect.objectContaining({ method: "POST" }),
      );
      expect(result.state).toBe("learning");
    });

    it("restoreKnown throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Not Found")));

      await expect(api.restoreKnown(999)).rejects.toThrow(
        "POST /api/srs/items/999/restore-known: Not Found",
      );
    });

    it("setSRSItemState calls POST /api/srs/items/:id/state with state", async () => {
      const item = {
        id: 3,
        text: "zdravo",
        translation: "",
        state: "known" as const,
        due_at: "2026-04-14",
        stability: 1.0,
        difficulty: 5.0,
        reps: 0,
        lapses: 0,
        last_review: null,
        language_code: "sl",
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(item)));

      const result = await api.setSRSItemState(3, "known");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/3/state`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ state: "known" }),
        }),
      );
      expect(result.state).toBe("known");
    });

    it("untrackSRSItem calls POST /api/srs/items/:id/untrack and returns deleted action", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ action: "deleted" })));
      const result = await api.untrackSRSItem(3);
      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/items/3/untrack`,
        expect.objectContaining({ method: "POST" }),
      );
      expect(result).toEqual({ action: "deleted" });
    });

    it("untrackSRSItem returns suspended action with item detail", async () => {
      const suspendedItem = {
        action: "suspended" as const,
        item: {
          id: 3,
          text: "zdravo",
          state: "suspended",
          due_at: "2026-04-14",
          stability: 1.0,
          difficulty: 5.0,
          reps: 0,
          lapses: 0,
          last_review: null,
          language_code: "sl",
        },
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(suspendedItem)));
      const result = await api.untrackSRSItem(3);
      expect(result.action).toBe("suspended");
      expect((result as { action: "suspended"; item: { state: string } }).item.state).toBe(
        "suspended",
      );
    });

    it("peerSync calls POST /api/anki/peer-sync?dry_run=false by default", async () => {
      const payload = {
        auth_success: true,
        pull_required: 0,
        push_required: 1,
        tt_push_pull_exit: 0,
        dry_run: false,
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(payload)));

      const result = await api.peerSync();

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/anki/peer-sync?dry_run=false`,
        expect.objectContaining({ method: "POST" }),
      );
      expect(result.auth_success).toBe(true);
      expect(result.push_required).toBe(1);
      expect(result.dry_run).toBe(false);
    });

    it("peerSync forwards dryRun=true", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ dry_run: true })));

      await api.peerSync(true);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/anki/peer-sync?dry_run=true`,
        expect.objectContaining({ method: "POST" }),
      );
    });

    it("surfaces the server's error detail (body.detail) on a failed request", async () => {
      const detail =
        "AnkiWeb requires a one-way FULL_SYNC (required=2) on the pull leg. " +
        "Fix: cd backend && uv run python -m app.anki.sync_orchestrator --bootstrap";
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFailBody({ detail }, 409, "")));

      await expect(api.peerSync()).rejects.toThrow(detail);
    });

    it("falls back to statusText when the error body has no string detail", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(mockFailBody({ not_detail: "x" }, 503, "Service Unavailable")),
      );

      await expect(api.peerSync()).rejects.toThrow(
        "POST /api/anki/peer-sync?dry_run=false: Service Unavailable",
      );
    });

    it("surfaces FastAPI validation errors (list-shaped detail) with field and message", async () => {
      // FastAPI 422s put detail as a LIST of {loc, msg, type} objects, not a string.
      const detail = [
        {
          loc: ["body", "batch_size"],
          msg: "Input should be less than or equal to 14",
          type: "less_than_equal",
        },
      ];
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFailBody({ detail }, 422, "")));

      await expect(api.peerSync()).rejects.toThrow(
        "batch_size: Input should be less than or equal to 14",
      );
    });

    it("handles degenerate validation-error entries (missing loc or msg)", async () => {
      const detail = [
        { msg: "field required" }, // no loc
        { loc: ["body", "day"] }, // no msg
        { loc: "not-an-array", msg: "weird loc" },
      ];
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFailBody({ detail }, 422, "")));

      await expect(api.peerSync()).rejects.toThrow("field required; weird loc");
    });

    it("falls back to HTTP <status> when there is no detail and no statusText", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFailBody({}, 409, "")));

      await expect(api.peerSync()).rejects.toThrow(
        "POST /api/anki/peer-sync?dry_run=false: HTTP 409",
      );
    });

    it("translateTerm calls POST /api/srs/translate with text and language_code", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(mockOk({ translation: "in the city centre" })),
      );

      const result = await api.translateTerm("centru mesta", "sl");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/translate`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ text: "centru mesta", language_code: "sl" }),
        }),
      );
      expect(result.translation).toBe("in the city centre");
    });
  });

  describe("fetchQueueStats", () => {
    it("calls GET /api/srs/queue-stats and returns parsed shape", async () => {
      const payload = { new: 5, learning: 3, review: 9, daily_new_cap: 30, cap_source: "cache" };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(payload)));

      const result = await api.fetchQueueStats();

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/queue-stats`);
      expect(result.new).toBe(5);
      expect(result.learning).toBe(3);
      expect(result.review).toBe(9);
      expect(result.daily_new_cap).toBe(30);
      expect(result.cap_source).toBe("cache");
    });

    it("throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Service Unavailable")));
      await expect(api.fetchQueueStats()).rejects.toThrow("Service Unavailable");
    });
  });

  describe("getRateLimit", () => {
    it("calls GET /api/llm/rate-limit and returns parsed shape", async () => {
      const payload = {
        provider: "groq",
        model: "openai/gpt-oss-120b",
        llm_mode: "live",
        snapshot: {
          age_s: 12.3,
          requests_limit: 1000,
          requests_remaining: 999,
          requests_reset_in_s: 86.4,
          tokens_limit: 8000,
          tokens_remaining: 7927,
          tokens_reset_in_s: 0.5,
        },
        last_429: null,
        tokens_used_24h: 73,
        tokens_per_day_limit: 100000,
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(payload)));

      const result = await api.getRateLimit();

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/llm/rate-limit`);
      expect(result.provider).toBe("groq");
      expect(result.snapshot?.tokens_remaining).toBe(7927);
      expect(result.last_429).toBeNull();
    });

    it("throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Not Found")));
      await expect(api.getRateLimit()).rejects.toThrow("Not Found");
    });
  });

  describe("probeRateLimit", () => {
    it("calls POST /api/llm/rate-limit/probe and returns parsed shape", async () => {
      const payload = {
        provider: "groq",
        model: "openai/gpt-oss-120b",
        llm_mode: "live",
        snapshot: null,
        last_429: null,
        tokens_used_24h: null,
        tokens_per_day_limit: 100000,
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(payload)));

      const result = await api.probeRateLimit();

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/llm/rate-limit/probe`,
        expect.objectContaining({ method: "POST" }),
      );
      expect(result.snapshot).toBeNull();
    });

    it("throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Service Unavailable")));
      await expect(api.probeRateLimit()).rejects.toThrow("Service Unavailable");
    });
  });

  describe("getLlmHealth", () => {
    it("calls GET /api/llm/health and returns parsed shape", async () => {
      const payload = {
        healthy: true,
        consecutive_failures: 0,
        last_error: null,
        fallback_allowed: false,
        llm_mode: "live",
      };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(payload)));

      const result = await api.getLlmHealth();

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/llm/health`);
      expect(result.healthy).toBe(true);
      expect(result.consecutive_failures).toBe(0);
      expect(result.last_error).toBeNull();
      expect(result.fallback_allowed).toBe(false);
    });

    it("throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Service Unavailable")));
      await expect(api.getLlmHealth()).rejects.toThrow("Service Unavailable");
    });
  });

  describe("fetchReviewQueue", () => {
    it("GETs /api/srs/review-queue and returns the payload", async () => {
      const queue = [{ id: 1, text: "foo" }];
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ queue })));

      const result = await api.fetchReviewQueue();

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/review-queue`);
      expect(result).toEqual({ queue });
    });

    it("appends session_start=1 when sessionStart is true", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ queue: [] })));
      await api.fetchReviewQueue({ sessionStart: true });
      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/review-queue?session_start=1`);
    });

    it("throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Service Unavailable")));
      await expect(api.fetchReviewQueue()).rejects.toThrow("Service Unavailable");
    });
  });

  describe("getListens", () => {
    it("GETs /api/srs/listens and returns the lessons payload", async () => {
      const lessons = [
        { lesson_id: "l1", listen_count: 3, last_listened_at: "2026-01-01T00:00:00Z" },
        { lesson_id: "l2", listen_count: 1, last_listened_at: "2026-01-02T00:00:00Z" },
      ];
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ lessons })));

      const result = await api.getListens();

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/listens`);
      expect(result.lessons).toEqual(lessons);
    });

    it("returns empty array when no listens exist", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ lessons: [] })));

      const result = await api.getListens();

      expect(result.lessons).toEqual([]);
    });

    it("throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail()));
      await expect(api.getListens()).rejects.toThrow("GET /api/srs/listens: Internal Server Error");
    });
  });

  describe("importListens", () => {
    it("POSTs to /api/srs/listens/import with lesson_ids", async () => {
      const response = { imported: ["l1", "l2"], already_present: [], unknown: [] };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(response)));

      const result = await api.importListens(["l1", "l2"]);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/listens/import`,
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ lesson_ids: ["l1", "l2"] }),
        }),
      );
      expect(result.imported).toEqual(["l1", "l2"]);
    });

    it("returns already_present and unknown arrays from server", async () => {
      const response = { imported: [], already_present: ["l1"], unknown: ["l3"] };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(response)));

      const result = await api.importListens(["l1", "l3"]);

      expect(result.already_present).toEqual(["l1"]);
      expect(result.unknown).toEqual(["l3"]);
    });

    it("sets X-TT-Language header to languageCode, overriding the active language", async () => {
      localStorage.setItem("tt-language", "sl");
      const response = { imported: ["l1"], already_present: [], unknown: [] };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(response)));

      await api.importListens(["l1"], "no");

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/listens/import`,
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({ "X-TT-Language": "no" }),
        }),
      );
      localStorage.removeItem("tt-language");
    });

    it("omits X-TT-Language override when languageCode is omitted", async () => {
      localStorage.setItem("tt-language", "sl");
      const response = { imported: [], already_present: [], unknown: [] };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(response)));

      await api.importListens(["l1"]);

      expect(fetch).toHaveBeenCalledWith(
        `${BASE}/api/srs/listens/import`,
        expect.objectContaining({
          headers: expect.objectContaining({ "X-TT-Language": "sl" }),
        }),
      );
      localStorage.removeItem("tt-language");
    });

    it("throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail()));
      await expect(api.importListens(["l1"])).rejects.toThrow(
        "POST /api/srs/listens/import: Internal Server Error",
      );
    });
  });

  describe("fetchLessonReviewQueue", () => {
    it("GETs /api/srs/lesson/{id}/review-queue and returns queue", async () => {
      const queue = [{ id: 1, text: "foo", direction: "recognition" }];
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ queue })));

      const result = await api.fetchLessonReviewQueue("lesson-1");

      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/lesson/lesson-1/review-queue`);
      expect(result).toEqual({ queue });
    });

    it("throws on non-ok response", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Not Found")));
      await expect(api.fetchLessonReviewQueue("missing")).rejects.toThrow(
        "GET /api/srs/lesson/missing/review-queue: Not Found",
      );
    });
  });
});

describe("TunaTaleAPI language header", () => {
  let api: TunaTaleAPI;

  beforeEach(() => {
    api = new TunaTaleAPI(BASE);
    vi.restoreAllMocks();
    localStorage.removeItem("tt-language");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.removeItem("tt-language");
  });

  it("sends X-TT-Language on a GET when a language is selected", async () => {
    localStorage.setItem("tt-language", "no");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ items: [], total: 0 })));
    await api.listSRSItems();
    expect(fetch).toHaveBeenCalledWith(
      `${BASE}/api/srs/items`,
      expect.objectContaining({ headers: expect.objectContaining({ "X-TT-Language": "no" }) }),
    );
  });

  it("merges X-TT-Language into a POST's existing headers", async () => {
    localStorage.setItem("tt-language", "no");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ id: "x" })));
    await api.startPlan("coffee");
    const init = (fetch as unknown as { mock: { calls: unknown[][] } }).mock
      .calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect((init.headers as Record<string, string>)["X-TT-Language"]).toBe("no");
  });

  it("omits the header when no language is selected (single-arg GET preserved)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk([])));
    await api.listCurricula();
    expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum`);
  });

  it("omits the header during SSR (no localStorage)", async () => {
    vi.stubGlobal("localStorage", undefined);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk([])));
    await api.listCurricula();
    expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum`);
  });

  it("getLanguages calls GET /api/languages", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        mockOk({
          languages: [{ code: "sl", name: "Slovene" }],
          active: "sl",
          sync_available: true,
        }),
      ),
    );
    const result = await api.getLanguages();
    expect(fetch).toHaveBeenCalledWith(`${BASE}/api/languages`);
    expect(result.active).toBe("sl");
    expect(result.sync_available).toBe(true);
  });
});

describe("pipeline API", () => {
  let api: TunaTaleAPI;

  beforeEach(() => {
    api = new TunaTaleAPI(BASE);
    vi.restoreAllMocks();
  });

  it("getPipeline calls GET /api/curriculum/:id/pipeline", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        mockOk({
          active: true,
          days: [
            {
              day: 1,
              state: "generating",
              lesson_id: null,
              has_audio: false,
              error: null,
              retryable: true,
              detail: "attempt 1/4",
            },
          ],
        }),
      ),
    );
    const result = await api.getPipeline("cid-1");
    expect(fetch).toHaveBeenCalledWith(`${BASE}/api/curriculum/cid-1/pipeline`);
    expect(result.active).toBe(true);
    expect(result.days[0].state).toBe("generating");
  });

  it("getPipeline returns inactive when no pipeline exists", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ active: false, days: [] })));
    const result = await api.getPipeline("cid-1");
    expect(result.active).toBe(false);
    expect(result.days).toEqual([]);
  });

  it("retryPipelineDay calls POST /api/curriculum/:id/pipeline/retry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ status: "queued" })));
    const result = await api.retryPipelineDay("cid-1", 2);
    expect(fetch).toHaveBeenCalledWith(
      `${BASE}/api/curriculum/cid-1/pipeline/retry`,
      expect.objectContaining({ method: "POST", body: JSON.stringify({ day: 2 }) }),
    );
    expect(result.status).toBe("queued");
  });

  it("retryPipelineDay returns ready when audio already exists", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ status: "ready" })));
    const result = await api.retryPipelineDay("cid-1", 3);
    expect(result.status).toBe("ready");
  });

  it("regenerateDay calls POST /api/curriculum/:id/pipeline/regenerate", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ status: "queued" })));
    const result = await api.regenerateDay("cid-1", 2, "DEEPER");
    expect(fetch).toHaveBeenCalledWith(
      `${BASE}/api/curriculum/cid-1/pipeline/regenerate`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ day: 2, strategy: "DEEPER" }),
      }),
    );
    expect(result.status).toBe("queued");
  });

  it("regenerateDay defaults to WIDER strategy", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ status: "queued" })));
    await api.regenerateDay("cid-1", 1, "WIDER");
    expect(fetch).toHaveBeenCalledWith(
      `${BASE}/api/curriculum/cid-1/pipeline/regenerate`,
      expect.objectContaining({ body: JSON.stringify({ day: 1, strategy: "WIDER" }) }),
    );
  });

  describe("getLlmActivity", () => {
    it("calls GET /api/llm/activity with no params", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ latest: 0, events: [] })));
      const result = await api.getLlmActivity();
      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/llm/activity`);
      expect(result.latest).toBe(0);
    });

    it("appends ?since=N when provided", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ latest: 5, events: [] })));
      await api.getLlmActivity(3);
      expect(fetch).toHaveBeenCalledWith(`${BASE}/api/llm/activity?since=3`);
    });

    it("returns events when available", async () => {
      const events = [
        {
          seq: 1,
          timestamp: 1000,
          kind: "pipeline",
          curriculum_id: "cid-1",
          day: 1,
          state: "queued",
          message: "enqueued",
        },
        {
          seq: 2,
          timestamp: 1001,
          kind: "llm_call",
          provider: "groq",
          model: "llama",
          latency_ms: 500,
          status: "success",
          is_fallback: false,
          prompt_preview: "",
          response_preview: "",
          rate_limits: null,
          reasoning_effort: null,
        },
      ];
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk({ latest: 2, events })));
      const result = await api.getLlmActivity();
      expect(result.events).toHaveLength(2);
      expect(result.events[0].kind).toBe("pipeline");
      expect(result.events[1].kind).toBe("llm_call");
    });
  });

  it("throws on non-ok pipeline response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockFail("Not Found")));
    await expect(api.getPipeline("missing")).rejects.toThrow(
      "GET /api/curriculum/missing/pipeline: Not Found",
    );
  });

  it("throws on non-ok retry with 409", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(mockFailBody({ detail: "Day 2 is already active" }, 409)),
    );
    await expect(api.retryPipelineDay("cid-1", 2)).rejects.toThrow(
      "POST /api/curriculum/cid-1/pipeline/retry: Day 2 is already active",
    );
  });
});

describe("image methods", () => {
  let api: TunaTaleAPI;

  beforeEach(() => {
    api = new TunaTaleAPI(BASE);
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetchImageCandidates returns candidates", async () => {
    const response = {
      query: "water",
      status: "ok",
      candidates: [
        {
          preview_url: "http://x/a.jpg",
          webformat_url: "http://x/b.jpg",
          tags: "water",
          width: 100,
          height: 100,
          likes: 5,
        },
      ],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(response)));

    const result = await api.fetchImageCandidates(1);

    expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/items/1/image/candidates`);
    expect(result.candidates).toHaveLength(1);
    expect(result.candidates[0].preview_url).toBe("http://x/a.jpg");
  });

  it("fetchImageCandidates includes query param when provided", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(mockOk({ query: "water", status: "ok", candidates: [] })),
    );

    await api.fetchImageCandidates(5, "water");

    expect(fetch).toHaveBeenCalledWith(`${BASE}/api/srs/items/5/image/candidates?q=water`);
  });

  it("setItemImageFromUrl calls PUT with JSON body", async () => {
    const updated = makeSRSItemDetail({ id: 1, image_url: "http://x/new.jpg" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(updated)));

    const result = await api.setItemImageFromUrl(1, "http://x/new.jpg");

    expect(fetch).toHaveBeenCalledWith(
      `${BASE}/api/srs/items/1/image`,
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ url: "http://x/new.jpg" }),
      }),
    );
    expect(result.image_url).toBe("http://x/new.jpg");
  });

  it("uploadItemImage sends FormData without Content-Type header", async () => {
    const updated = makeSRSItemDetail({ id: 1, image_url: "http://x/uploaded.jpg" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(updated)));

    const file = new File(["dummy"], "photo.jpg", { type: "image/jpeg" });
    const result = await api.uploadItemImage(1, file);

    const init = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][1] as RequestInit;
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.headers).toBeUndefined();
    expect(result.image_url).toBe("http://x/uploaded.jpg");
  });

  it("removeItemImage calls DELETE", async () => {
    const updated = makeSRSItemDetail({ id: 1, image_url: null });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockOk(updated)));

    const result = await api.removeItemImage(1);

    expect(fetch).toHaveBeenCalledWith(
      `${BASE}/api/srs/items/1/image`,
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(result.image_url).toBeNull();
  });
});
