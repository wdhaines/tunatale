/**
 * Tests for Transcript.svelte component.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import Transcript from "./Transcript.svelte";
import { api } from "$lib/api";
import type { LessonDetail, TranscriptData } from "$lib/api";

vi.mock("$lib/api", () => ({
  api: { translateTerm: vi.fn() },
}));

const baseTranscript: TranscriptData = {
  lesson_id: "l1",
  key_phrases: [],
  dialogue_lines: [],
};

const transcriptWithPhrases: TranscriptData = {
  lesson_id: "l1",
  key_phrases: [
    { phrase: "dober dan", translation: "good day" },
    { phrase: "hvala", translation: "thank you" },
  ],
  dialogue_lines: [],
};

const transcriptWithDialogue: TranscriptData = {
  lesson_id: "l1",
  key_phrases: [],
  dialogue_lines: [
    {
      role: "Petra",
      sentence: "",
      words: [
        {
          surface: "zdravo",
          lemma: "zdravo",
          srs_state: "new",
          srs_item_id: null,
          translation: null,
          collocation_span_id: null,
          collocation_start: false,
          collocation_srs_state: null,
          collocation_lemma: null,
          collocation_translation: null,
          card_type: null,
          active_state: "new",
          active_direction: null,
          is_due: false,
          progress: null,
          inflectable: false,
          inflection_feature: null,
          known_marked: false,
        },
      ],
    },
  ],
};

const transcriptWithCollocation: TranscriptData = {
  lesson_id: "l1",
  key_phrases: [],
  dialogue_lines: [
    {
      role: "Petra",
      sentence: "",
      words: [
        {
          surface: "dober",
          lemma: "dober",
          srs_state: "new",
          srs_item_id: null,
          translation: "good",
          collocation_span_id: 99,
          collocation_start: true,
          collocation_srs_state: "learning",
          collocation_lemma: "dober dan",
          collocation_translation: "good day",
          card_type: null,
          active_state: "new",
          active_direction: null,
          is_due: false,
          progress: null,
          inflectable: false,
          inflection_feature: null,
          known_marked: false,
        },
        {
          surface: "dan",
          lemma: "dan",
          srs_state: "new",
          srs_item_id: null,
          translation: "day",
          collocation_span_id: 99,
          collocation_start: false,
          collocation_srs_state: "learning",
          collocation_lemma: "dober dan",
          collocation_translation: "good day",
          card_type: null,
          active_state: "new",
          active_direction: null,
          is_due: false,
          progress: null,
          inflectable: false,
          inflection_feature: null,
          known_marked: false,
        },
        {
          surface: "hvala",
          lemma: "hvala",
          srs_state: "unknown",
          srs_item_id: null,
          translation: null,
          collocation_span_id: null,
          collocation_start: false,
          collocation_srs_state: null,
          collocation_lemma: null,
          collocation_translation: null,
          card_type: null,
          active_state: "new",
          active_direction: null,
          is_due: false,
          progress: null,
          inflectable: false,
          inflection_feature: null,
          known_marked: false,
        },
      ],
    },
  ],
};

function defaultProps(overrides = {}) {
  return {
    transcript: baseTranscript,
    onWordClick: vi.fn(),
    ...overrides,
  };
}

describe("Transcript", () => {
  it("renders key phrases when present", () => {
    const { getByText } = render(Transcript, {
      props: defaultProps({ transcript: transcriptWithPhrases }),
    });
    expect(getByText("Key Phrases")).toBeTruthy();
    expect(getByText("dober dan")).toBeTruthy();
    expect(getByText("good day")).toBeTruthy();
  });

  it("does not render Key Phrases section when empty", () => {
    const { queryByText } = render(Transcript, { props: defaultProps() });
    expect(queryByText("Key Phrases")).toBeFalsy();
  });

  it("renders dialogue lines when present", () => {
    const { getByText } = render(Transcript, {
      props: defaultProps({ transcript: transcriptWithDialogue }),
    });
    expect(getByText("Dialogue")).toBeTruthy();
    expect(getByText("Petra")).toBeTruthy();
  });

  it("does not render Dialogue section when empty", () => {
    const { queryByText } = render(Transcript, { props: defaultProps() });
    expect(queryByText("Dialogue")).toBeFalsy();
  });

  it("wraps collocation tokens in a collocation-span container", () => {
    const { container } = render(Transcript, {
      props: defaultProps({ transcript: transcriptWithCollocation }),
    });
    const spans = container.querySelectorAll(".collocation-span");
    expect(spans.length).toBe(1);
  });

  it("collocation-span contains both tokens", () => {
    const { container } = render(Transcript, {
      props: defaultProps({ transcript: transcriptWithCollocation }),
    });
    const span = container.querySelector(".collocation-span");
    expect(span).not.toBeNull();
    expect(span!.textContent).toContain("dober");
    expect(span!.textContent).toContain("dan");
  });

  it("word outside collocation is not inside a collocation-span", () => {
    const { container } = render(Transcript, {
      props: defaultProps({ transcript: transcriptWithCollocation }),
    });
    // 'hvala' should not be inside .collocation-span
    const spans = container.querySelectorAll(".collocation-span");
    for (const span of spans) {
      expect(span.textContent).not.toContain("hvala");
    }
  });

  describe("collocation click behavior", () => {
    it("plain click on collocation wrapper fires onCollocationStateChange", async () => {
      const onCollocationStateChange = vi.fn();
      const { container } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptWithCollocation,
          onCollocationStateChange,
        }),
      });
      const span = container.querySelector(".collocation-span") as HTMLElement;
      await fireEvent.click(span);
      expect(onCollocationStateChange).toHaveBeenCalledWith(99);
    });

    it("Enter key on collocation wrapper fires onCollocationStateChange", async () => {
      const onCollocationStateChange = vi.fn();
      const { container } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptWithCollocation,
          onCollocationStateChange,
        }),
      });
      const span = container.querySelector(".collocation-span") as HTMLElement;
      await fireEvent.keyDown(span, { key: "Enter" });
      expect(onCollocationStateChange).toHaveBeenCalledWith(99);
    });

    it("plain click inside collocation does not fire word-level onWordClick", async () => {
      const onWordClick = vi.fn();
      const onCollocationStateChange = vi.fn();
      const { getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptWithCollocation,
          onWordClick,
          onCollocationStateChange,
        }),
      });
      await fireEvent.click(getByText("dober"));
      expect(onWordClick).not.toHaveBeenCalled();
      expect(onCollocationStateChange).toHaveBeenCalled();
    });

    it("Alt+click inside collocation fires word-level onWordClick", async () => {
      const onWordClick = vi.fn();
      const onCollocationStateChange = vi.fn();
      const { getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptWithCollocation,
          onWordClick,
          onCollocationStateChange,
        }),
      });
      await fireEvent.click(getByText("dober"), { altKey: true });
      expect(onWordClick).toHaveBeenCalledWith(expect.objectContaining({ lemma: "dober" }), 0);
    });

    it("plain click on word outside collocation fires word-level onWordClick", async () => {
      const onWordClick = vi.fn();
      const onCollocationStateChange = vi.fn();
      const { getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptWithCollocation,
          onWordClick,
          onCollocationStateChange,
        }),
      });
      await fireEvent.click(getByText("hvala"));
      expect(onWordClick).toHaveBeenCalledWith(expect.objectContaining({ lemma: "hvala" }), 0);
      expect(onCollocationStateChange).not.toHaveBeenCalled();
    });

    it("collocation wrapper tints its background by mastery (on-ramp state)", () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptWithCollocation }),
      });
      const span = container.querySelector(".collocation-span") as HTMLElement;
      // 'learning' is on the ramp; with no collocation_progress it defaults to 0 → faint red.
      expect(span.getAttribute("style")).toContain("rgba(195, 34, 34, 0.15)");
      expect(span.className).not.toContain("coll-bg-ignored");
    });

    it("collocation wrapper has role=button and is keyboard-reachable", () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptWithCollocation }),
      });
      const span = container.querySelector(".collocation-span") as HTMLElement;
      expect(span.getAttribute("role")).toBe("button");
      expect(span.getAttribute("tabindex")).toBe("0");
    });

    it("suppresses the group tooltip and shows per-word tooltips while Alt is held", async () => {
      const { container, queryByText } = render(Transcript, {
        props: defaultProps({ transcript: transcriptWithCollocation }),
      });
      const collSpan = container.querySelector(".collocation-span") as HTMLElement;
      const outerWrap = collSpan.parentElement as HTMLElement;
      // The group tooltip is the role=tooltip element that is a direct sibling of
      // the collocation span (per-word tooltips are nested deeper, inside the span).
      const groupTooltip = () =>
        Array.from(outerWrap.children).find((el) => el.getAttribute("role") === "tooltip") ?? null;

      // Default: the group tooltip element exists; per-word tooltips do not.
      expect(groupTooltip()).not.toBeNull();
      expect(queryByText("good")).toBeNull();

      await fireEvent.keyDown(window, { key: "Alt" });

      // Alt held: the whole group tooltip is gone (not just its translation), and the
      // individual word tooltip ("good") appears — so only one popover shows.
      expect(groupTooltip()).toBeNull();
      expect(queryByText("good")).not.toBeNull();

      await fireEvent.keyUp(window, { key: "Alt" });
      expect(groupTooltip()).not.toBeNull();
    });

    it("forwards sentence + tooltipActions to collocation inner words, reactively", async () => {
      const makeColl = (sentence: string): TranscriptData => ({
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "Petra",
            sentence,
            words: [
              {
                ...transcriptWithCollocation.dialogue_lines[0].words[0],
                surface: "centru",
                lemma: "center",
                translation: "center",
                srs_item_id: 867,
                active_state: "review",
                inflectable: true,
                inflection_feature: "noun:loc:sg",
                collocation_lemma: "centru mesta",
                collocation_translation: "city center",
              },
              {
                ...transcriptWithCollocation.dialogue_lines[0].words[1],
                surface: "mesta",
                lemma: "mesto",
                translation: "city",
                srs_item_id: 260,
                active_state: "review",
                collocation_lemma: "centru mesta",
                collocation_translation: "city center",
              },
            ],
          },
        ],
      });
      const onCreateInflection = vi.fn();
      const { queryByText, getByText, rerender } = render(Transcript, {
        props: defaultProps({
          transcript: makeColl("v centru mesta"),
          tooltipActions: { onCreateInflection },
        }),
      });
      // Suppressed by default — the action button is not rendered yet.
      expect(queryByText("Create inflection card")).toBeNull();

      // The per-word sentence binding must track the line reactively.
      await rerender(
        defaultProps({
          transcript: makeColl("blizu centra mesta"),
          tooltipActions: { onCreateInflection },
        }),
      );

      await fireEvent.keyDown(window, { key: "Alt" });

      // With Alt held, the inner word's populated popover exposes its action,
      // wired with the current line sentence.
      await fireEvent.click(getByText("Create inflection card"));
      expect(onCreateInflection).toHaveBeenCalledWith(
        expect.objectContaining({ lemma: "center" }),
        "blizu centra mesta",
      );
    });

    it("collocation wrapper has no title attribute", () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptWithCollocation }),
      });
      const span = container.querySelector(".collocation-span") as HTMLElement;
      expect(span.getAttribute("title")).toBeNull();
    });

    it("collocation tooltip shows collocation_translation in DOM", () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptWithCollocation }),
      });
      const tooltip = container
        .querySelector(".collocation-span")!
        .closest(".tt-wrap")!
        .querySelector('[role="tooltip"]');
      expect(tooltip).not.toBeNull();
      expect(tooltip!.textContent).toContain("good day");
    });

    it("collocation tooltip shows state label when collocation_translation is null", () => {
      const noTranslationColl: TranscriptData = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "Petra",
            words: [
              {
                surface: "dober",
                lemma: "dober",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: 1,
                collocation_start: true,
                collocation_srs_state: "learning",
                collocation_lemma: "dober dan",
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
              {
                surface: "dan",
                lemma: "dan",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: 1,
                collocation_start: false,
                collocation_srs_state: "learning",
                collocation_lemma: "dober dan",
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: noTranslationColl }),
      });
      const tooltip = container
        .querySelector(".collocation-span")!
        .closest(".tt-wrap")!
        .querySelector('[role="tooltip"]');
      expect(tooltip).not.toBeNull();
      expect(tooltip!.textContent).toContain("Not Due");
    });

    it("Space key on collocation wrapper fires onCollocationStateChange", async () => {
      const onCollocationStateChange = vi.fn();
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptWithCollocation, onCollocationStateChange }),
      });
      await fireEvent.keyDown(container.querySelector(".collocation-span") as HTMLElement, {
        key: " ",
      });
      expect(onCollocationStateChange).toHaveBeenCalled();
    });

    it("other keys on collocation wrapper do not fire", async () => {
      const onCollocationStateChange = vi.fn();
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptWithCollocation, onCollocationStateChange }),
      });
      await fireEvent.keyDown(container.querySelector(".collocation-span") as HTMLElement, {
        key: "Tab",
      });
      expect(onCollocationStateChange).not.toHaveBeenCalled();
    });
  });

  describe("collocation alt-key behavior (svelte:window listeners)", () => {
    // When altHeld=false: collocation Tooltip shows "good day"; word-level Tooltips are hidden.
    // When altHeld=true: collocation Tooltip is gone; word-level Tooltips appear inside the wrapper.
    // So we check for the collocation tooltip by its specific content ("good day").
    function hasCollocationTooltip(container: HTMLElement) {
      return Array.from(container.querySelectorAll('[role="tooltip"]')).some((el) =>
        el.textContent?.includes("good day"),
      );
    }

    it("alt keydown hides collocation tooltip", async () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptWithCollocation }),
      });

      expect(hasCollocationTooltip(container)).toBe(true);

      await fireEvent.keyDown(window, { key: "Alt", altKey: true });

      expect(hasCollocationTooltip(container)).toBe(false);
    });

    it("non-alt keydown does not hide collocation tooltip", async () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptWithCollocation }),
      });

      await fireEvent.keyDown(window, { key: "Control" });

      expect(hasCollocationTooltip(container)).toBe(true);
    });

    it("alt keyup restores collocation tooltip", async () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptWithCollocation }),
      });

      await fireEvent.keyDown(window, { key: "Alt", altKey: true });
      expect(hasCollocationTooltip(container)).toBe(false);

      await fireEvent.keyUp(window, { key: "Alt" });
      expect(hasCollocationTooltip(container)).toBe(true);
    });
  });

  describe("collocation background colors", () => {
    function makeCollTranscript(state: string, progress: number | null = null): TranscriptData {
      return {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "Petra",
            words: [
              {
                surface: "dober",
                lemma: "dober",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: 1,
                collocation_start: true,
                collocation_srs_state: state,
                collocation_lemma: "dober dan",
                collocation_translation: null,
                collocation_progress: progress,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
              {
                surface: "dan",
                lemma: "dan",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: 1,
                collocation_start: false,
                collocation_srs_state: state,
                collocation_lemma: "dober dan",
                collocation_translation: null,
                collocation_progress: progress,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
    }

    it("on-ramp state (review) tints background by mastery progress", () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: makeCollTranscript("review", 1) }),
      });
      const span = container.querySelector(".collocation-span") as HTMLElement;
      expect(span.getAttribute("style")).toContain("rgba(34, 195, 34, 0.15)");
      expect(span.className).not.toContain("coll-bg-ignored");
    });

    it("on-ramp state (known) tints background by mastery progress", () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: makeCollTranscript("known", 0.5) }),
      });
      const span = container.querySelector(".collocation-span") as HTMLElement;
      expect(span.getAttribute("style")).toContain("rgba(195, 195, 34, 0.15)");
    });

    it("on-ramp state (relearning) tints background by mastery progress", () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: makeCollTranscript("relearning", 0) }),
      });
      const span = container.querySelector(".collocation-span") as HTMLElement;
      expect(span.getAttribute("style")).toContain("rgba(195, 34, 34, 0.15)");
    });

    it("suspended state stays off the ramp → coll-bg-ignored, no inline tint", () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: makeCollTranscript("suspended", 0.9) }),
      });
      const span = container.querySelector(".collocation-span") as HTMLElement;
      expect(span.className).toContain("coll-bg-ignored");
      expect(span.getAttribute("style") ?? "").not.toContain("hsla");
    });

    it("ignored state stays off the ramp → coll-bg-ignored", () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: makeCollTranscript("ignored") }),
      });
      expect(container.querySelector(".collocation-span")!.className).toContain("coll-bg-ignored");
    });

    it("unrecognized non-suspended state is treated as on-ramp (tinted, not gray)", () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: makeCollTranscript("exotic", 0) }),
      });
      const span = container.querySelector(".collocation-span") as HTMLElement;
      expect(span.className).not.toContain("coll-bg-ignored");
      expect(span.getAttribute("style")).toContain("background-color");
    });
  });

  describe('phrase creation — "+ New phrase" toggle and drag', () => {
    const transcriptForDrag: TranscriptData = {
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [
        {
          role: "Petra",
          words: [
            {
              surface: "centru",
              lemma: "centru",
              srs_state: "new",
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "new",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
            {
              surface: "mesta",
              lemma: "mesto",
              srs_state: "new",
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "new",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
            {
              surface: "hvala",
              lemma: "hvala",
              srs_state: "new",
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "new",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
          ],
        },
      ],
    };

    const transcriptTwoLines: TranscriptData = {
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [
        {
          role: "Petra",
          words: [
            {
              surface: "centru",
              lemma: "centru",
              srs_state: "new",
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "new",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
            {
              surface: "mesta",
              lemma: "mesto",
              srs_state: "new",
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "new",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
          ],
        },
        {
          role: "Ana",
          sentence: "",
          words: [
            {
              surface: "hvala",
              lemma: "hvala",
              srs_state: "new",
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "new",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
          ],
        },
      ],
    };

    it('renders a "+ New phrase" button', () => {
      const { getByText } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag }),
      });
      expect(getByText("+ New phrase")).toBeTruthy();
    });

    it('clicking "+ New phrase" button enables selection mode', async () => {
      const { getByText } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag }),
      });
      const btn = getByText("+ New phrase");
      await fireEvent.click(btn);
      // Once in selection mode the button should show a cancel label or the button is active
      expect(getByText("Cancel")).toBeTruthy();
    });

    it("pointerup without prior pointerdown does not show confirm bar", async () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag }),
      });
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
      // pointerUp fires without isDragging being set
      await fireEvent.pointerUp(mestaSpan);
      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();
    });

    it("pointermove without prior pointerdown does not show confirm bar", async () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag }),
      });
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
      await fireEvent.pointerMove(mestaSpan);
      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();
    });

    it("pointerdown on container (not on a word) does not start drag", async () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag }),
      });
      const wordsContainer = container.querySelector(".dialogue-words") as HTMLElement;
      // Fire directly on the container — resolveWordTarget returns null
      await fireEvent.pointerDown(wordsContainer);
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);
      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();
    });

    it("drag: pointerdown + pointermove + pointerup over 2 words shows confirm bar", async () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag }),
      });
      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

      // Fire events directly on word spans so e.target resolves correctly
      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      expect(container.querySelector(".phrase-confirm-bar")).toBeTruthy();
      expect(container.querySelector(".phrase-confirm-bar")!.textContent).toContain("centru mesta");
    });

    it("drag with anchor == endpoint (single word) does not show confirm bar", async () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag }),
      });
      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;

      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerUp(centruSpan);

      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();
    });

    it("cross-line drag resets and shows no confirm bar", async () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptTwoLines }),
      });
      const centruSpan = container.querySelector(
        '[data-line-index="0"][data-word-index="0"]',
      ) as HTMLElement;
      const hvalaSpan = container.querySelector(
        '[data-line-index="1"][data-word-index="0"]',
      ) as HTMLElement;

      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerUp(hvalaSpan);

      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();
    });

    it("drag over a word with collocation_span_id aborts — no confirm bar", async () => {
      const transcriptWithExistingColl: TranscriptData = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "Petra",
            words: [
              {
                surface: "centru",
                lemma: "centru",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: 5,
                collocation_start: true,
                collocation_srs_state: "new",
                collocation_lemma: "centru mesta",
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
              {
                surface: "mesta",
                lemma: "mesto",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: 5,
                collocation_start: false,
                collocation_srs_state: "new",
                collocation_lemma: "centru mesta",
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
              {
                surface: "hvala",
                lemma: "hvala",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptWithExistingColl }),
      });
      // words 0 and 1 are inside a collocation-span wrapper, words rendered inside collocation
      // Try to drag from hvala (index 2) — but it can't overlap with collocation 5 unless we
      // start from inside the collocation. Start at word 0 (collocation), end at word 2.
      // Use the word-index data attributes on the inner WordSpan elements
      const word0Span = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const hvalaSpan = container.querySelector('[data-word-index="2"]') as HTMLElement;

      await fireEvent.pointerDown(word0Span);
      await fireEvent.pointerMove(hvalaSpan);
      await fireEvent.pointerUp(hvalaSpan);

      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();
    });

    it("clicking Create fires onCreatePhrase with correct args", async () => {
      const onCreatePhrase = vi.fn();
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag, onCreatePhrase }),
      });
      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      const createBtn = container.querySelector(
        ".phrase-confirm-bar button.confirm-create",
      ) as HTMLElement;
      await fireEvent.click(createBtn);

      expect(onCreatePhrase).toHaveBeenCalledWith(
        expect.objectContaining({
          text: "centru mesta",
          word_count: 2,
          translation: "",
          lineIndex: 0,
          startIdx: 0,
          endIdx: 1,
          source_sentence: "centru mesta hvala",
          source_lesson_id: undefined,
          source_line_index: 0,
        }),
      );
    });

    it("clicking Cancel clears selection and fires no callback", async () => {
      const onCreatePhrase = vi.fn();
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag, onCreatePhrase }),
      });
      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      const cancelBtn = container.querySelector(
        ".phrase-confirm-bar button.confirm-cancel",
      ) as HTMLElement;
      await fireEvent.click(cancelBtn);

      expect(onCreatePhrase).not.toHaveBeenCalled();
      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();
    });

    it("selected words carry a word-selected highlight class during drag", async () => {
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag }),
      });
      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);

      // Both centru (0) and mesta (1) should have word-selected
      expect(container.querySelector('[data-word-index="0"]')!.className).toContain(
        "word-selected",
      );
      expect(container.querySelector('[data-word-index="1"]')!.className).toContain(
        "word-selected",
      );
      // hvala (2) should not
      expect(container.querySelector('[data-word-index="2"]')!.className).not.toContain(
        "word-selected",
      );
    });

    it("selectionMode: first tap sets anchor, second tap shows confirm bar", async () => {
      const { container, getByText } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag }),
      });

      await fireEvent.click(getByText("+ New phrase"));

      // First tap: click word 0
      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      await fireEvent.click(centruSpan);
      // No confirm bar yet after first tap
      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();

      // Second tap: click word 1
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
      await fireEvent.click(mestaSpan);
      // Now confirm bar should appear
      expect(container.querySelector(".phrase-confirm-bar")).toBeTruthy();
    });

    it("selectionMode: cross-line second tap resets anchor to new line, no confirm bar", async () => {
      const { container, getByText } = render(Transcript, {
        props: defaultProps({ transcript: transcriptTwoLines }),
      });

      await fireEvent.click(getByText("+ New phrase"));

      // First tap: line 0, word 0
      const centruSpan = container.querySelector(
        '[data-line-index="0"][data-word-index="0"]',
      ) as HTMLElement;
      await fireEvent.click(centruSpan);
      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();

      // Second tap: line 1, word 0 (different line) — anchor resets to this word
      const hvalaSpan = container.querySelector(
        '[data-line-index="1"][data-word-index="0"]',
      ) as HTMLElement;
      await fireEvent.click(hvalaSpan);
      // No confirm bar (anchor was reset, this is now the new first tap)
      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();
    });

    it("selectionMode: tapping same word twice (start===end) shows no confirm bar", async () => {
      const { container, getByText } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag }),
      });

      await fireEvent.click(getByText("+ New phrase"));

      // First tap: word 0
      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      await fireEvent.click(centruSpan);
      // Second tap: same word 0
      await fireEvent.click(centruSpan);
      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();
    });

    it("scene grouping: renders scene header from lesson natural_speed narrator+en phrases", () => {
      const lesson: LessonDetail = {
        id: "l1",
        day: 1,
        title: "test",
        language_code: "sl",
        key_phrases: [],
        sections: [
          {
            type: "natural_speed",
            phrases: [
              { text: "Natural Speed", role: "narrator", language_code: "en", voice_id: "v" },
              {
                text: "At the City Information Office",
                role: "narrator",
                language_code: "en",
                voice_id: "v",
              },
              { text: "zdravo", role: "female-1", language_code: "sl", voice_id: "v" },
            ],
          },
          { type: "slow_speed", phrases: [] },
          { type: "translated", phrases: [] },
        ],
      };
      const transcriptData: TranscriptData = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "female-1",
            sentence: "",
            words: [
              {
                surface: "zdravo",
                lemma: "zdravo",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      const { container, getByText } = render(Transcript, {
        props: defaultProps({ transcript: transcriptData, lesson }),
      });
      const sceneHeader = container.querySelector(".scene-header");
      expect(sceneHeader).not.toBeNull();
      expect(sceneHeader!.textContent).toContain("At the City Information Office");
      // Scene header text should NOT be rendered as a dialogue line
      expect(container.querySelectorAll(".dialogue-line").length).toBe(1);
      expect(getByText("zdravo")).toBeTruthy();
    });

    it("scene grouping: multiple scenes each produce a scene header", () => {
      const lesson: LessonDetail = {
        id: "l1",
        day: 1,
        title: "test",
        language_code: "sl",
        key_phrases: [],
        sections: [
          {
            type: "natural_speed",
            phrases: [
              { text: "Natural Speed", role: "narrator", language_code: "en", voice_id: "v" },
              { text: "At the Airport", role: "narrator", language_code: "en", voice_id: "v" },
              { text: "zdravo", role: "female-1", language_code: "sl", voice_id: "v" },
              { text: "At the Hotel", role: "narrator", language_code: "en", voice_id: "v" },
              { text: "hvala", role: "female-1", language_code: "sl", voice_id: "v" },
            ],
          },
          { type: "slow_speed", phrases: [] },
          { type: "translated", phrases: [] },
        ],
      };
      const transcriptData: TranscriptData = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "female-1",
            sentence: "",
            words: [
              {
                surface: "zdravo",
                lemma: "zdravo",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
          {
            role: "female-1",
            sentence: "",
            words: [
              {
                surface: "hvala",
                lemma: "hvala",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptData, lesson }),
      });
      const sceneHeaders = container.querySelectorAll(".scene-header");
      expect(sceneHeaders.length).toBe(2);
      expect(sceneHeaders[0].textContent).toContain("At the Airport");
      expect(sceneHeaders[1].textContent).toContain("At the Hotel");
    });

    it("scene grouping: does not show the section title (Natural Speed) as a scene header", () => {
      const lesson: LessonDetail = {
        id: "l1",
        day: 1,
        title: "test",
        language_code: "sl",
        key_phrases: [],
        sections: [
          {
            type: "natural_speed",
            phrases: [
              { text: "Natural Speed", role: "narrator", language_code: "en", voice_id: "v" },
              { text: "zdravo", role: "female-1", language_code: "sl", voice_id: "v" },
            ],
          },
          { type: "slow_speed", phrases: [] },
          { type: "translated", phrases: [] },
        ],
      };
      const transcriptData: TranscriptData = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "female-1",
            sentence: "",
            words: [
              {
                surface: "zdravo",
                lemma: "zdravo",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptData, lesson }),
      });
      const sceneHeaders = container.querySelectorAll(".scene-header");
      expect(sceneHeaders.length).toBe(0);
    });

    it("progressive disclosure: slow text hidden by default, shown when Slow toggle is enabled", async () => {
      const lesson: LessonDetail = {
        id: "l1",
        day: 1,
        title: "test",
        language_code: "sl",
        key_phrases: [],
        sections: [
          {
            type: "natural_speed",
            phrases: [{ text: "zdravo", role: "female-1", language_code: "sl", voice_id: "v" }],
          },
          {
            type: "slow_speed",
            phrases: [{ text: "zdra...vo", role: "female-1", language_code: "sl", voice_id: "v" }],
          },
          { type: "translated", phrases: [] },
        ],
      };
      const transcriptData: TranscriptData = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "female-1",
            sentence: "",
            words: [
              {
                surface: "zdravo",
                lemma: "zdravo",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      const { container, getByText, queryByText } = render(Transcript, {
        props: defaultProps({ transcript: transcriptData, lesson }),
      });
      // Slow text not shown by default
      expect(queryByText("zdra...vo")).toBeFalsy();
      // Toggle Slow
      await fireEvent.click(getByText("Slow"));
      expect(container.querySelector(".line-slow")).not.toBeNull();
      expect(container.querySelector(".line-slow")!.textContent).toContain("zdra...vo");
    });

    it("progressive disclosure: per-word gloss hidden by default, shown when Gloss toggle is enabled", async () => {
      const transcriptData: TranscriptData = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "female-1",
            sentence: "",
            words: [
              {
                surface: "zdravo",
                lemma: "zdravo",
                srs_state: "new",
                srs_item_id: null,
                translation: "hello",
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      const { container, getByText } = render(Transcript, {
        props: defaultProps({ transcript: transcriptData }),
      });
      expect(container.querySelector(".word-gloss")).toBeNull();
      await fireEvent.click(getByText("Gloss"));
      const gloss = container.querySelector(".word-gloss");
      expect(gloss).not.toBeNull();
      expect(gloss!.textContent).toContain("hello");
    });

    it("progressive disclosure: interlinear L1 hidden by default, shown when Interlinear toggle is enabled", async () => {
      const lesson: LessonDetail = {
        id: "l1",
        day: 1,
        title: "test",
        language_code: "sl",
        key_phrases: [],
        sections: [
          {
            type: "natural_speed",
            phrases: [{ text: "zdravo", role: "female-1", language_code: "sl", voice_id: "v" }],
          },
          { type: "slow_speed", phrases: [] },
          {
            type: "translated",
            phrases: [
              { text: "zdravo", role: "female-1", language_code: "sl", voice_id: "v" },
              { text: "hello there", role: "narrator", language_code: "en", voice_id: "v" },
            ],
          },
        ],
      };
      const transcriptData: TranscriptData = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "female-1",
            sentence: "",
            words: [
              {
                surface: "zdravo",
                lemma: "zdravo",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      const { container, getByText, queryByText } = render(Transcript, {
        props: defaultProps({ transcript: transcriptData, lesson }),
      });
      // Interlinear L1 not shown by default
      expect(queryByText("hello there")).toBeFalsy();
      expect(container.querySelector(".line-interlinear")).toBeNull();
      // Toggle Interlinear
      await fireEvent.click(getByText("Interlinear"));
      const interlinear = container.querySelector(".line-interlinear");
      expect(interlinear).not.toBeNull();
      expect(interlinear!.textContent).toContain("hello there");
    });

    it("translation input can be updated and is included in onCreatePhrase call", async () => {
      const onCreatePhrase = vi.fn();
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForDrag, onCreatePhrase }),
      });
      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;

      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      const translationInput = container.querySelector(
        ".phrase-translation-input",
      ) as HTMLInputElement;
      await fireEvent.input(translationInput, { target: { value: "city centre" } });

      await fireEvent.click(
        container.querySelector(".phrase-confirm-bar button.confirm-create") as HTMLElement,
      );

      expect(onCreatePhrase).toHaveBeenCalledWith(
        expect.objectContaining({ translation: "city centre" }),
      );
    });

    it("✨ button calls api.translateTerm and fills the translation input", async () => {
      const _mockTranslate = vi
        .mocked(api.translateTerm)
        .mockResolvedValue({ translation: "in the city centre" });
      const { container } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptForDrag,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });

      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      const translateBtn = container.querySelector(".phrase-translate-btn") as HTMLElement;
      expect(translateBtn).toBeTruthy();
      await fireEvent.click(translateBtn);

      await waitFor(() => {
        const input = container.querySelector(".phrase-translation-input") as HTMLInputElement;
        expect(input.value).toBe("in the city centre");
      });
    });

    it("✨ button is disabled while loading", async () => {
      let resolvePromise: (v: { translation: string }) => void;
      const pending = new Promise<{ translation: string }>((resolve) => {
        resolvePromise = resolve;
      });
      vi.mocked(api.translateTerm).mockReturnValue(pending);

      const { container } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptForDrag,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });

      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      const translateBtn = container.querySelector(".phrase-translate-btn") as HTMLElement;
      await fireEvent.click(translateBtn);

      expect(translateBtn.hasAttribute("disabled")).toBe(true);
      expect(translateBtn.textContent).toBe("…");

      resolvePromise!({ translation: "in the city centre" });
    });

    it("✨ button error shows error message and does not change translation input", async () => {
      vi.mocked(api.translateTerm).mockRejectedValue(new Error("LLM error"));

      const { container } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptForDrag,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });

      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      const translateBtn = container.querySelector(".phrase-translate-btn") as HTMLElement;
      await fireEvent.click(translateBtn);

      await waitFor(() => {
        const input = container.querySelector(".phrase-translation-input") as HTMLInputElement;
        expect(input.value).toBe("");
        const errorEl = container.querySelector(".phrase-error");
        expect(errorEl).toBeTruthy();
        expect(errorEl!.textContent).toContain("Translation failed");
      });
    });

    it("✨ after successful translate, edit then Create includes edited value", async () => {
      vi.mocked(api.translateTerm).mockResolvedValue({ translation: "in the city centre" });
      const onCreatePhrase = vi.fn();
      const { container } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptForDrag,
          onCreatePhrase,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });

      const centruSpan = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const mestaSpan = container.querySelector('[data-word-index="1"]') as HTMLElement;
      await fireEvent.pointerDown(centruSpan);
      await fireEvent.pointerMove(mestaSpan);
      await fireEvent.pointerUp(mestaSpan);

      await fireEvent.click(container.querySelector(".phrase-translate-btn") as HTMLElement);
      await waitFor(() => {
        const input = container.querySelector(".phrase-translation-input") as HTMLInputElement;
        expect(input.value).toBe("in the city centre");
      });

      const translationInput = container.querySelector(
        ".phrase-translation-input",
      ) as HTMLInputElement;
      await fireEvent.input(translationInput, { target: { value: "custom edit" } });

      await fireEvent.click(
        container.querySelector(".phrase-confirm-bar button.confirm-create") as HTMLElement,
      );

      expect(onCreatePhrase).toHaveBeenCalledWith(
        expect.objectContaining({ translation: "custom edit" }),
      );
    });
  });

  describe("add-phrase collapsed section", () => {
    const transcriptEmpty: TranscriptData = {
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [],
    };

    it("does not show add-phrase form by default", () => {
      const { container } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptEmpty,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });
      expect(container.querySelector(".add-phrase-form")).toBeFalsy();
    });

    it("shows add-phrase form after toggling", async () => {
      const { container, getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptEmpty,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });
      const toggle = getByText(/Add phrase/);
      await fireEvent.click(toggle);
      expect(container.querySelector(".add-phrase-form")).toBeTruthy();
      expect(container.querySelector(".add-phrase-form input")).toBeTruthy();
    });

    it("toggle button expands and collapses", async () => {
      const { container, getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptEmpty,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });
      const toggle = getByText(/Add phrase/);
      await fireEvent.click(toggle);
      expect(container.querySelector(".add-phrase-form")).toBeTruthy();
      await fireEvent.click(toggle);
      expect(container.querySelector(".add-phrase-form")).toBeFalsy();
    });

    it("typing text and clicking Create calls onCreatePhrase with correct args", async () => {
      const onCreatePhrase = vi.fn();
      const { container, getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptEmpty,
          onCreatePhrase,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });
      await fireEvent.click(getByText(/Add phrase/));

      const textInput = container.querySelector(".add-phrase-text") as HTMLInputElement;
      await fireEvent.input(textInput, { target: { value: "good morning sunshine" } });

      const translationInput = container.querySelector(
        ".add-phrase-translation",
      ) as HTMLInputElement;
      await fireEvent.input(translationInput, { target: { value: "dobro jutro sonce" } });

      await fireEvent.click(container.querySelector(".add-phrase-create") as HTMLElement);

      expect(onCreatePhrase).toHaveBeenCalledWith(
        expect.objectContaining({
          text: "good morning sunshine",
          word_count: 3,
          translation: "dobro jutro sonce",
          lineIndex: -1,
          startIdx: -1,
          endIdx: -1,
        }),
      );
    });

    it("✨ translate button works in add-phrase form", async () => {
      vi.mocked(api.translateTerm).mockResolvedValue({ translation: "dobro jutro sonce" });
      const { container, getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptEmpty,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });
      await fireEvent.click(getByText(/Add phrase/));

      const textInput = container.querySelector(".add-phrase-text") as HTMLInputElement;
      await fireEvent.input(textInput, { target: { value: "good morning sunshine" } });

      const translateBtn = container.querySelector(".add-phrase-translate-btn") as HTMLElement;
      await fireEvent.click(translateBtn);

      await waitFor(() => {
        const translationInput = container.querySelector(
          ".add-phrase-translation",
        ) as HTMLInputElement;
        expect(translationInput.value).toBe("dobro jutro sonce");
      });
    });

    it("✨ translate error in add-phrase form shows error message", async () => {
      vi.mocked(api.translateTerm).mockRejectedValue(new Error("LLM error"));
      const { container, getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptEmpty,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });
      await fireEvent.click(getByText(/Add phrase/));

      const textInput = container.querySelector(".add-phrase-text") as HTMLInputElement;
      await fireEvent.input(textInput, { target: { value: "good morning" } });

      const translateBtn = container.querySelector(".add-phrase-translate-btn") as HTMLElement;
      await fireEvent.click(translateBtn);

      await waitFor(() => {
        const errorEl = container.querySelector(".add-phrase-form .phrase-error");
        expect(errorEl).toBeTruthy();
        expect(errorEl!.textContent).toContain("Translation failed");
      });
    });

    it("Create with empty text does not call onCreatePhrase", async () => {
      const onCreatePhrase = vi.fn();
      const { container, getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptEmpty,
          onCreatePhrase,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });
      await fireEvent.click(getByText(/Add phrase/));

      const createBtn = container.querySelector(".add-phrase-create") as HTMLElement;
      await fireEvent.click(createBtn);
      expect(onCreatePhrase).not.toHaveBeenCalled();
    });

    it("Create with whitespace-only text does not call onCreatePhrase", async () => {
      const onCreatePhrase = vi.fn();
      const { container, getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptEmpty,
          onCreatePhrase,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });
      await fireEvent.click(getByText(/Add phrase/));

      const textInput = container.querySelector(".add-phrase-text") as HTMLInputElement;
      await fireEvent.input(textInput, { target: { value: "   " } });

      const createBtn = container.querySelector(".add-phrase-create") as HTMLElement;
      await fireEvent.click(createBtn);
      expect(onCreatePhrase).not.toHaveBeenCalled();
    });

    it("Create button is disabled when text is empty", async () => {
      const { container, getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptEmpty,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });
      await fireEvent.click(getByText(/Add phrase/));

      const createBtn = container.querySelector(".add-phrase-create") as HTMLButtonElement;
      expect(createBtn.disabled).toBe(true);
    });

    it("Create resets form and collapses section on success", async () => {
      const onCreatePhrase = vi.fn();
      const { container, getByText } = render(Transcript, {
        props: defaultProps({
          transcript: transcriptEmpty,
          onCreatePhrase,
          lesson: { id: "l1", title: "t", language_code: "sl", key_phrases: [], sections: [] },
        }),
      });
      await fireEvent.click(getByText(/Add phrase/));

      const textInput = container.querySelector(".add-phrase-text") as HTMLInputElement;
      await fireEvent.input(textInput, { target: { value: "good morning" } });
      const translationInput = container.querySelector(
        ".add-phrase-translation",
      ) as HTMLInputElement;
      await fireEvent.input(translationInput, { target: { value: "dobro jutro" } });

      await fireEvent.click(container.querySelector(".add-phrase-create") as HTMLElement);

      expect(container.querySelector(".add-phrase-form")).toBeFalsy();
    });
  });

  describe("defensive guards (closing coverage gaps)", () => {
    const oneWord: TranscriptData = {
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [
        {
          role: "A",
          sentence: "",
          words: [
            {
              surface: "x",
              lemma: "x",
              srs_state: "new",
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "new",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
          ],
        },
      ],
    };

    const twoLines: TranscriptData = {
      lesson_id: "l1",
      key_phrases: [],
      dialogue_lines: [
        {
          role: "A",
          sentence: "",
          words: [
            {
              surface: "first",
              lemma: "first",
              srs_state: "new",
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "new",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
          ],
        },
        {
          role: "B",
          sentence: "",
          words: [
            {
              surface: "second",
              lemma: "second",
              srs_state: "new",
              srs_item_id: null,
              translation: null,
              collocation_span_id: null,
              collocation_start: false,
              collocation_srs_state: null,
              collocation_lemma: null,
              collocation_translation: null,
              card_type: null,
              active_state: "new",
              active_direction: null,
              is_due: false,
              progress: null,
              inflectable: false,
              inflection_feature: null,
              known_marked: false,
            },
          ],
        },
      ],
    };

    it("resolveWordTarget returns null when data-word-index is non-numeric", async () => {
      // Hits the `isNaN(wordIdx) || isNaN(lineIdx)` early return.
      // Render normally, then inject a malformed word-index span as a child of
      // the dialogue-words container so pointerdown bubbles to the line's handler.
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: oneWord }),
      });
      const wordsContainer = container.querySelector(".dialogue-words") as HTMLElement;
      const malformed = document.createElement("span");
      malformed.setAttribute("data-word-index", "not-a-number");
      malformed.setAttribute("data-line-index", "also-bad");
      wordsContainer.appendChild(malformed);

      await fireEvent.pointerDown(malformed);
      await fireEvent.pointerUp(malformed);

      // No drag started → no phrase-confirm-bar
      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();
    });

    it("handlePointerMove drops moves to a different line than the drag anchor", async () => {
      // Hits the `resolved.lineIndex !== dragAnchor.lineIndex` early return.
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: twoLines }),
      });
      const word0Line0 = container.querySelector(
        '[data-line-index="0"][data-word-index="0"]',
      ) as HTMLElement;
      const word0Line1 = container.querySelector(
        '[data-line-index="1"][data-word-index="0"]',
      ) as HTMLElement;

      await fireEvent.pointerDown(word0Line0);
      await fireEvent.pointerMove(word0Line1); // ← exercises guard
      await fireEvent.pointerUp(word0Line1);

      // Cross-line drag does not produce a selection
      expect(container.querySelector(".phrase-confirm-bar")).toBeFalsy();
    });

    it("fetchTranslation early-returns when lesson is null (no api.translateTerm call)", async () => {
      // Hits the `if (!selection || !lesson) return` early return in fetchTranslation.
      vi.mocked(api.translateTerm).mockClear();
      const transcriptForSelect: TranscriptData = {
        lesson_id: "l1",
        key_phrases: [],
        dialogue_lines: [
          {
            role: "A",
            words: [
              {
                surface: "one",
                lemma: "one",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
              {
                surface: "two",
                lemma: "two",
                srs_state: "new",
                srs_item_id: null,
                translation: null,
                collocation_span_id: null,
                collocation_start: false,
                collocation_srs_state: null,
                collocation_lemma: null,
                collocation_translation: null,
                card_type: null,
                active_state: "new",
                active_direction: null,
                is_due: false,
                progress: null,
                inflectable: false,
                inflection_feature: null,
                known_marked: false,
              },
            ],
          },
        ],
      };
      const { container } = render(Transcript, {
        props: defaultProps({ transcript: transcriptForSelect, lesson: null }),
      });

      const w0 = container.querySelector('[data-word-index="0"]') as HTMLElement;
      const w1 = container.querySelector('[data-word-index="1"]') as HTMLElement;
      await fireEvent.pointerDown(w0);
      await fireEvent.pointerMove(w1);
      await fireEvent.pointerUp(w1);

      const translateBtn = container.querySelector(".phrase-translate-btn") as HTMLElement;
      expect(translateBtn).toBeTruthy();
      await fireEvent.click(translateBtn);

      expect(vi.mocked(api.translateTerm)).not.toHaveBeenCalled();
    });

    it("fetchAddPhraseTranslation early-returns when lesson is null", async () => {
      // Hits the `if (!addPhraseText.trim() || !lesson) return` early return.
      vi.mocked(api.translateTerm).mockClear();
      const { container, getByText } = render(Transcript, {
        props: defaultProps({ transcript: oneWord, lesson: null }),
      });
      await fireEvent.click(getByText(/Add phrase/));

      const textInput = container.querySelector(".add-phrase-text") as HTMLInputElement;
      await fireEvent.input(textInput, { target: { value: "test phrase" } });

      const translateBtn = container.querySelector(".add-phrase-translate-btn") as HTMLElement;
      await fireEvent.click(translateBtn);

      expect(vi.mocked(api.translateTerm)).not.toHaveBeenCalled();
    });
  });
});
