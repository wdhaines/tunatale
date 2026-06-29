/**
 * Tests for DrillCard shared flashcard component.
 */
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import DrillCard from "./DrillCard.svelte";
import { makeSRSItemDetail } from "../../test/factories";

describe("DrillCard", () => {
  describe("recognition direction", () => {
    const item = makeSRSItemDetail({
      text: "dober dan",
      translation: "good day",
      audio_url: "/api/media/sl_dober_dan.mp3",
      image_url: "/api/media/dober_dan.jpg",
      grammar: "phrase, masc",
      note: "common greeting",
    });

    it("front renders audio element with autoplay and Slovene text", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { container } = render(DrillCard, { item, direction: "recognition", onRate });
      const audio = container.querySelector("audio");
      expect(audio).toBeTruthy();
      expect(audio?.getAttribute("autoplay")).toBe("");
      expect(audio?.getAttribute("src")).toBe("/api/media/sl_dober_dan.mp3");
      expect(container.textContent).toContain("dober dan");
    });

    it("front shows play button for manual replay", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { getByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      expect(getByRole("button", { name: "Play audio" })).toBeTruthy();
    });

    it("front does NOT show image, English, grammar, note before reveal", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { container } = render(DrillCard, { item, direction: "recognition", onRate });
      expect(container.textContent).not.toContain("good day");
      expect(container.textContent).not.toContain("common greeting");
      expect(container.querySelector("img")).toBeNull();
    });

    it("back stacks: Slovene still in DOM, <hr>, image, English, grammar, note", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));

      // Slovene still visible
      expect(container.textContent).toContain("dober dan");
      // HR divider exists
      expect(container.querySelector("hr")).toBeTruthy();
      // Image shown
      expect(container.querySelector("img")).toBeTruthy();
      // English translation
      expect(container.textContent).toContain("good day");
      // Grammar shown
      expect(container.textContent).toContain("phrase, masc");
      // Note shown
      expect(container.textContent).toContain("common greeting");
    });

    it("back hides empty grammar/note divs", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const noGramNote = makeSRSItemDetail({
        text: "hvala",
        translation: "thank you",
        grammar: "",
        note: "",
      });
      const { findByRole, container } = render(DrillCard, {
        item: noGramNote,
        direction: "recognition",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(container.querySelector(".gram")).toBeNull();
      expect(container.querySelector(".note")).toBeNull();
    });

    it("shows all four rating buttons after reveal", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { findByRole, getByText } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(getByText("Again")).toBeTruthy();
      expect(getByText("Hard")).toBeTruthy();
      expect(getByText("Good")).toBeTruthy();
      expect(getByText("Easy")).toBeTruthy();
    });
  });

  describe("production direction", () => {
    const item = makeSRSItemDetail({
      text: "dober dan",
      translation: "good day",
      audio_url: "/api/media/sl_dober_dan.mp3",
      image_url: "/api/media/dober-dan.jpg",
      grammar: "phrase",
      note: "greeting",
    });

    it("front: image only (no text)", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { queryByText, container } = render(DrillCard, {
        item,
        direction: "production",
        onRate,
      });
      expect(container.querySelector("img")).toBeTruthy();
      expect(queryByText("dober dan")).toBeFalsy();
      expect(queryByText("good day")).toBeFalsy();
    });

    it("back: image stays on top after reveal, <hr>, then audio + Slovene + English + grammar + note", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { findByRole, container, findByText } = render(DrillCard, {
        item,
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));

      // Wait for back content to render
      await findByText("dober dan"); // Slovene text indicates back is shown

      // Image still visible AFTER reveal
      const img = container.querySelector("img");
      expect(img).toBeTruthy();
      expect(img?.getAttribute("src")).toBe("/api/media/dober-dan.jpg");

      // HR divider between front and back
      expect(container.querySelector("hr")).toBeTruthy();

      // Audio element on back
      const audios = container.querySelectorAll("audio");
      expect(audios.length).toBe(1);

      // Slovene and English visible
      expect(container.textContent).toContain("dober dan");
      expect(container.textContent).toContain("good day");

      // Grammar and note
      expect(container.textContent).toContain("phrase");
      expect(container.textContent).toContain("greeting");
    });

    it("front renders image only (no audio), falls back to translation when no image", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { container } = render(DrillCard, { item, direction: "production", onRate });
      expect(container.querySelector("audio")).toBeNull();
      expect(container.querySelector("img")).toBeTruthy();
    });

    it("front falls back to translation text when image_url is null", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const noImg = makeSRSItemDetail({ text: "hvala", translation: "thank you", image_url: null });
      const { findByText } = render(DrillCard, { item: noImg, direction: "production", onRate });
      expect(await findByText("thank you")).toBeTruthy();
    });
  });

  describe("cloze card", () => {
    const clozeItem = makeSRSItemDetail({
      text: "vsak",
      translation: "every",
      card_type: "cloze",
      source_sentence: "Odprto je vsak dan",
      source_sentence_translation: "It is open every day",
      audio_url: "/api/media/sl_vsak.mp3",
      grammar: "adj",
      note: "common word",
    });

    it("front shows sentence with blank, no leak of answer word", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { container } = render(DrillCard, { item: clozeItem, direction: "production", onRate });
      expect(container.textContent).toContain("[...]");
      expect(container.textContent).toContain("Odprto je");
      expect(container.textContent).toContain("dan");
      expect(container.textContent).not.toContain("vsak");
    });

    it("reveal shows filled sentence with answer highlighted", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { findByRole, container } = render(DrillCard, {
        item: clozeItem,
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(container.innerHTML).toContain('<mark class="cloze-answer">');
      expect(container.textContent).toContain("vsak");
      expect(container.textContent).toContain("every");
    });

    it("reveal shows audio, sentence translation, word translation, grammar, note", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { findByRole, container } = render(DrillCard, {
        item: clozeItem,
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(container.querySelector("audio")).toBeTruthy();
      expect(container.textContent).toContain("It is open every day");
      expect(container.textContent).toContain("every");
      expect(container.textContent).toContain("adj");
      expect(container.textContent).toContain("common word");
    });

    it("front falls back to translation when cloze but no source_sentence or image", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const noSentence = makeSRSItemDetail({
        text: "vsak",
        translation: "every",
        card_type: "cloze",
        source_sentence: "",
        image_url: null,
      });
      const { findByText } = render(DrillCard, {
        item: noSentence,
        direction: "production",
        onRate,
      });
      expect(await findByText("every")).toBeTruthy();
    });

    it("reveal shows word audio button when word_audio_url is present", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "vsak",
        translation: "every",
        card_type: "cloze",
        source_sentence: "Odprto je vsak dan",
        source_sentence_translation: "It is open every day",
        audio_url: "/api/media/sentence.mp3",
        word_audio_url: "/api/media/tts_vsak.mp3",
      });
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      const audios = container.querySelectorAll("audio");
      expect(audios.length).toBe(2);
      const wordBtn = container.querySelector('button[aria-label="Play word audio"]');
      expect(wordBtn).toBeTruthy();
      expect(container.textContent).toContain("vsak");
    });

    it("reveal renders gracefully without word_audio_url", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "še",
        translation: "still",
        card_type: "cloze",
        source_sentence: "Ja, še nisem videl.",
        source_sentence_translation: "Yes, I haven't seen yet.",
        audio_url: "/api/media/sentence.mp3",
      });
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      const audios = container.querySelectorAll("audio");
      expect(audios.length).toBe(1);
      const wordBtn = container.querySelector('button[aria-label="Play word audio"]');
      expect(wordBtn).toBeNull();
    });

    it("masks non-ASCII Slovene word (še) with Unicode-aware word boundary", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "še",
        translation: "still",
        card_type: "cloze",
        source_sentence: "Ja, še nisem videl.",
        source_sentence_translation: "Yes, I haven't seen yet.",
      });
      const { container } = render(DrillCard, { item, direction: "production", onRate });
      expect(container.textContent).toContain("[...]");
      expect(container.textContent).toContain("Ja,");
      expect(container.textContent).toContain("nisem videl.");
      expect(container.textContent).not.toContain("še");
    });

    it("renders {{c1::surface::hint}} as [...] in blank", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "Ljubljano",
        translation: "Ljubljana",
        card_type: "cloze",
        source_sentence: "Grem v {{c1::Ljubljano::ljubljana, acc sg}} s prijateljem.",
        source_sentence_translation: "I'm going to Ljubljana with a friend.",
      });
      const { container } = render(DrillCard, { item, direction: "production", onRate });
      expect(container.textContent).toContain("[...]");
      expect(container.textContent).toContain("Grem v");
      expect(container.textContent).toContain("s prijateljem.");
      expect(container.textContent).not.toContain("Ljubljano");
      expect(container.textContent).not.toContain("ljubljana, acc sg");
    });

    it("renders surface from {{c1::surface::hint}} in answer", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "Ljubljano",
        translation: "Ljubljana",
        card_type: "cloze",
        source_sentence: "Grem v {{c1::Ljubljano::ljubljana, acc sg}} s prijateljem.",
        source_sentence_translation: "I'm going to Ljubljana with a friend.",
      });
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(container.innerHTML).toContain('<mark class="cloze-answer">Ljubljano</mark>');
      expect(container.textContent).toContain("Ljubljano");
    });

    it("renders plain {{c1::surface}} as [...] in blank", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "ki",
        translation: "that/which",
        card_type: "cloze",
        source_sentence: "Knjiga, {{c1::ki}} je tam.",
        source_sentence_translation: "The book that is there.",
      });
      const { container } = render(DrillCard, { item, direction: "production", onRate });
      expect(container.textContent).toContain("[...]");
      expect(container.textContent).toContain("Knjiga,");
      expect(container.textContent).toContain("je tam.");
      expect(container.textContent).not.toContain("ki");
    });

    it("renders plain {{c1::surface}} answer with highlighted word", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "ki",
        translation: "that/which",
        card_type: "cloze",
        source_sentence: "Knjiga, {{c1::ki}} je tam.",
        source_sentence_translation: "The book that is there.",
      });
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(container.innerHTML).toContain('<mark class="cloze-answer">ki</mark>');
      expect(container.textContent).toContain("ki");
    });

    it("blanks every occurrence of a repeated hinted cloze in the prompt", async () => {
      // make_morphology_cloze_text wraps *every* occurrence of the surface, so a
      // surface that repeats in one sentence yields two {{c1::...}} spans. The
      // prompt must blank both — not leave the second as raw markup.
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "sem",
        translation: "to be (1sg)",
        card_type: "cloze",
        source_sentence: "Jaz {{c1::sem::biti, 1sg}} tu in {{c1::sem::biti, 1sg}} tam.",
        source_sentence_translation: "I am here and am there.",
      });
      const { container } = render(DrillCard, { item, direction: "production", onRate });
      expect(container.textContent).not.toContain("{{c1::");
      expect(container.textContent).not.toContain("sem");
      expect(container.textContent).not.toContain("biti, 1sg");
      const blanks = (container.textContent?.match(/\[\.\.\.\]/g) || []).length;
      expect(blanks).toBe(2);
    });

    it("highlights every occurrence of a repeated hinted cloze in the answer", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "sem",
        translation: "to be (1sg)",
        card_type: "cloze",
        source_sentence: "Jaz {{c1::sem::biti, 1sg}} tu in {{c1::sem::biti, 1sg}} tam.",
        source_sentence_translation: "I am here and am there.",
      });
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(container.innerHTML).not.toContain("{{c1::");
      const marks = (container.innerHTML.match(/<mark class="cloze-answer">sem<\/mark>/g) || [])
        .length;
      expect(marks).toBe(2);
    });

    it("shows grammar hint on the answer side", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "sem",
        translation: "to be (1sg)",
        card_type: "cloze",
        source_sentence: "Jaz {{c1::sem::biti, 1sg}} tu in {{c1::sem::biti, 1sg}} tam.",
        source_sentence_translation: "I am here and am there.",
        grammar: "biti, 1st person singular",
      });
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "production",
        onRate,
      });
      expect(container.textContent).not.toContain("biti, 1st person singular");
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(container.textContent).toContain("biti, 1st person singular");
    });
  });

  describe("rating callbacks", () => {
    it('calls onRate("good") when Good clicked', async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.click(await findByRole("button", { name: "Good" }));
      expect(onRate).toHaveBeenCalledWith("good", expect.any(Number));
    });

    it('calls onRate("again") when Again clicked', async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.click(await findByRole("button", { name: "Again" }));
      expect(onRate).toHaveBeenCalledWith("again", expect.any(Number));
    });

    it('calls onRate("hard") when Hard clicked', async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.click(await findByRole("button", { name: "Hard" }));
      expect(onRate).toHaveBeenCalledWith("hard", expect.any(Number));
    });

    it('calls onRate("easy") when Easy clicked', async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.click(await findByRole("button", { name: "Easy" }));
      expect(onRate).toHaveBeenCalledWith("easy", expect.any(Number));
    });
  });

  describe("audio play button", () => {
    it("calls audioEl.play() when play button clicked", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        audio_url: "/api/media/test.mp3",
      });
      const { getByRole, container } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      const audio = container.querySelector("audio");
      expect(audio).toBeTruthy();
      const playMock = vi.fn().mockResolvedValue(undefined);
      if (audio) {
        audio.play = playMock;
      }
      await fireEvent.click(getByRole("button", { name: "Play audio" }));
      expect(playMock).toHaveBeenCalled();
    });

    it("swallows rejected play() (autoplay-policy block) without surfacing", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({ audio_url: "/api/media/test.mp3" });
      const { getByRole, container } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      const audio = container.querySelector("audio");
      const playMock = vi.fn().mockRejectedValue(new Error("blocked"));
      if (audio) audio.play = playMock;

      await fireEvent.click(getByRole("button", { name: "Play audio" }));
      // Catch handler must absorb the rejection. Yield to let microtasks drain.
      await new Promise<void>((resolve) => queueMicrotask(() => resolve()));
      expect(playMock).toHaveBeenCalled();
    });
  });

  describe("word audio play button", () => {
    const clozeItem = makeSRSItemDetail({
      text: "vsak",
      translation: "every",
      card_type: "cloze",
      source_sentence: "Odprto je vsak dan",
      source_sentence_translation: "It is open every day",
      audio_url: "/api/media/sentence.mp3",
      word_audio_url: "/api/media/tts_vsak.mp3",
    });

    it("calls wordAudioEl.play() when word-audio button clicked", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { findByRole, container, getByLabelText } = render(DrillCard, {
        item: clozeItem,
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));

      const wordAudio = container.querySelector(
        'audio[src="/api/media/tts_vsak.mp3"]',
      ) as HTMLAudioElement | null;
      expect(wordAudio).toBeTruthy();
      const playMock = vi.fn().mockResolvedValue(undefined);
      if (wordAudio) wordAudio.play = playMock;

      await fireEvent.click(getByLabelText("Play word audio"));
      expect(playMock).toHaveBeenCalled();
    });

    it("swallows rejected word-audio play() (autoplay-policy block)", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { findByRole, container, getByLabelText } = render(DrillCard, {
        item: clozeItem,
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));

      const wordAudio = container.querySelector(
        'audio[src="/api/media/tts_vsak.mp3"]',
      ) as HTMLAudioElement | null;
      const playMock = vi.fn().mockRejectedValue(new Error("blocked"));
      if (wordAudio) wordAudio.play = playMock;

      await fireEvent.click(getByLabelText("Play word audio"));
      await new Promise<void>((resolve) => queueMicrotask(() => resolve()));
      expect(playMock).toHaveBeenCalled();
    });
  });

  describe("card with null audio_url", () => {
    it("renders cleanly without audio element or play button", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        audio_url: null,
      });
      const { container } = render(DrillCard, { item, direction: "recognition", onRate });
      expect(container.querySelector("audio")).toBeNull();
      expect(container.querySelector('button[aria-label="Play audio"]')).toBeNull();
    });
  });

  describe("keyboard shortcuts", () => {
    it("Space reveals the card when not yet revealed", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole, queryByRole } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await findByRole("button", { name: "Show" });
      await fireEvent.keyDown(window, { key: " " });
      expect(await findByRole("button", { name: "Good" })).toBeTruthy();
      expect(queryByRole("button", { name: "Show" })).toBeNull();
    });

    it("Enter reveals the card when not yet revealed", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await findByRole("button", { name: "Show" });
      await fireEvent.keyDown(window, { key: "Enter" });
      expect(await findByRole("button", { name: "Good" })).toBeTruthy();
    });

    it("preventDefault is called for Space so the page does not scroll", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await findByRole("button", { name: "Show" });
      const event = new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true });
      window.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
    });

    it("ignores keydown when event.repeat is true (no reveal)", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole, queryByRole } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await findByRole("button", { name: "Show" });
      await fireEvent.keyDown(window, { key: " ", repeat: true });
      expect(queryByRole("button", { name: "Show" })).toBeTruthy();
    });

    it("ignores keydown with metaKey/ctrlKey/altKey held", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await findByRole("button", { name: "Show" });
      await fireEvent.keyDown(window, { key: " ", metaKey: true });
      await fireEvent.keyDown(window, { key: " ", ctrlKey: true });
      await fireEvent.keyDown(window, { key: " ", altKey: true });
      expect(await findByRole("button", { name: "Show" })).toBeTruthy();
    });

    it("ignores keydown when target is an input element", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await findByRole("button", { name: "Show" });

      const input = document.createElement("input");
      container.appendChild(input);
      input.focus();
      await fireEvent.keyDown(input, { key: " " });

      expect(await findByRole("button", { name: "Show" })).toBeTruthy();
      input.remove();
    });

    it("ignores keydown when target is a textarea element", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await findByRole("button", { name: "Show" });

      const textarea = document.createElement("textarea");
      container.appendChild(textarea);
      textarea.focus();
      await fireEvent.keyDown(textarea, { key: " " });

      expect(await findByRole("button", { name: "Show" })).toBeTruthy();
      textarea.remove();
    });

    it("ignores keydown when target is a select element", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await findByRole("button", { name: "Show" });

      const select = document.createElement("select");
      container.appendChild(select);
      select.focus();
      await fireEvent.keyDown(select, { key: " " });

      expect(await findByRole("button", { name: "Show" })).toBeTruthy();
      select.remove();
    });

    it("ignores keydown when target is contenteditable", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await findByRole("button", { name: "Show" });

      const editable = document.createElement("div");
      editable.setAttribute("contenteditable", "true");
      editable.tabIndex = 0;
      container.appendChild(editable);
      editable.focus();
      await fireEvent.keyDown(editable, { key: " " });

      expect(await findByRole("button", { name: "Show" })).toBeTruthy();
      editable.remove();
    });

    it("ignores other keys when not revealed", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await findByRole("button", { name: "Show" });
      await fireEvent.keyDown(window, { key: "1" });
      expect(await findByRole("button", { name: "Show" })).toBeTruthy();
    });

    it("'1' grades again after reveal", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.keyDown(window, { key: "1" });
      expect(onRate).toHaveBeenCalledWith("again", expect.any(Number));
    });

    it("'2' grades hard after reveal", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.keyDown(window, { key: "2" });
      expect(onRate).toHaveBeenCalledWith("hard", expect.any(Number));
    });

    it("'3' grades good after reveal", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.keyDown(window, { key: "3" });
      expect(onRate).toHaveBeenCalledWith("good", expect.any(Number));
    });

    it("'4' grades easy after reveal", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.keyDown(window, { key: "4" });
      expect(onRate).toHaveBeenCalledWith("easy", expect.any(Number));
    });

    it("Space grades good after reveal (Anki convention)", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.keyDown(window, { key: " " });
      expect(onRate).toHaveBeenCalledWith("good", expect.any(Number));
    });

    it("Enter grades good after reveal (Anki convention)", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.keyDown(window, { key: "Enter" });
      expect(onRate).toHaveBeenCalledWith("good", expect.any(Number));
    });

    it("ignores unrelated keys after reveal (no grading)", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.keyDown(window, { key: "5" });
      expect(onRate).not.toHaveBeenCalled();
    });

    it("ignores grade keys while a grade is in flight (keyboard path)", async () => {
      let resolveRate: () => void = () => {};
      const onRate = vi.fn(
        () =>
          new Promise<void>((resolve) => {
            resolveRate = resolve;
          }),
      );
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.keyDown(window, { key: "3" });
      await fireEvent.keyDown(window, { key: "1" });
      expect(onRate).toHaveBeenCalledTimes(1);
      resolveRate();
    });

    it("ignores a second button click while a grade is in flight", async () => {
      let resolveRate: () => void = () => {};
      const onRate = vi.fn(
        () =>
          new Promise<void>((resolve) => {
            resolveRate = resolve;
          }),
      );
      const item = makeSRSItemDetail({});
      const { findByRole } = render(DrillCard, { item, direction: "recognition", onRate });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      await fireEvent.click(await findByRole("button", { name: "Good" }));
      await fireEvent.click(await findByRole("button", { name: "Again" }));
      expect(onRate).toHaveBeenCalledTimes(1);
      resolveRate();
    });
  });

  describe("answer hierarchy on reveal", () => {
    it("shows a keyboard hint inside the card", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({});
      const { container, findByRole } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await findByRole("button", { name: "Show" });
      expect(container.textContent).toContain("Space to flip");
      expect(container.textContent).toContain("1–4 to grade");
    });

    it("de-emphasizes the prompt image once revealed (revealed class on prompt container)", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "izgubiti",
        translation: "lose",
        image_url: "/api/media/izgubiti.jpg",
      });
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "production",
        onRate,
      });
      const prompt = container.querySelector(".prompt");
      expect(prompt?.classList.contains("revealed")).toBe(false);

      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(prompt?.classList.contains("revealed")).toBe(true);
    });
  });

  describe("gender article + POS disambiguation", () => {
    it("recognition front prefixes the headword with the gender article", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({ text: "orden", article: "en", translation: "order" });
      const { container } = render(DrillCard, { item, direction: "recognition", onRate });
      const mainText = container.querySelector(".main-text");
      expect(mainText?.textContent).toBe("en orden");
    });

    it("shows the part of speech when the surface is ambiguous", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({ text: "fange", pos: "noun", article: "" });
      const { container } = render(DrillCard, { item, direction: "recognition", onRate });
      expect(container.querySelector(".main-text")?.textContent).toContain("fange");
      expect(container.querySelector(".main-text")?.textContent).toContain("(noun)");
    });

    it("omits the POS when not provided (unambiguous surface)", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({ text: "bil", pos: "", article: "en" });
      const { container } = render(DrillCard, { item, direction: "recognition", onRate });
      const text = container.querySelector(".main-text")?.textContent ?? "";
      expect(text).toBe("en bil");
      expect(text).not.toContain("(");
    });

    it("production back prefixes the article and shows POS on the answer headword", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "fange",
        article: "en",
        pos: "noun",
        card_type: "vocab",
      });
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      const answer = container.querySelector(".answer-text.slovene");
      expect(answer?.textContent).toBe("en fange (noun)");
    });

    it("renders a bare headword when neither article nor POS is set", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({ text: "hund", article: "", pos: "" });
      const { container } = render(DrillCard, { item, direction: "recognition", onRate });
      expect(container.querySelector(".main-text")?.textContent).toBe("hund");
    });
  });

  describe("rich back-of-card extras", () => {
    const withExtras = () =>
      makeSRSItemDetail({
        text: "være",
        translation: "to be",
        card_type: "vocab",
        image_url: null,
        extras: [
          { label: "IPA", html: "/ˈʋæːɾə/", tier: "summary" },
          { label: "Inflections", html: "<table><tr><td>er</td></tr></table>", tier: "details" },
          { label: "Dictionary entry", html: "<h2>være</h2>", tier: "deep" },
        ],
      });

    it("does not render extras before reveal", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { container } = render(DrillCard, {
        item: withExtras(),
        direction: "recognition",
        onRate,
      });
      expect(container.querySelector(".extras-summary")).toBeNull();
      expect(container.querySelector(".extras-details")).toBeNull();
    });

    it("renders summary extras inline and tiered fields in a Details disclosure on reveal", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { findByRole, container } = render(DrillCard, {
        item: withExtras(),
        direction: "recognition",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));

      // Summary tier is always visible inline.
      const summary = container.querySelector(".extras-summary");
      expect(summary?.textContent).toContain("IPA");
      expect(summary?.textContent).toContain("/ˈʋæːɾə/");

      // details + deep tiers live in one collapsed <details> labelled "Details".
      const details = container.querySelector("details.extras-details");
      expect(details).toBeTruthy();
      expect(details?.querySelector("summary")?.textContent).toBe("Details");
      expect(container.innerHTML).toContain("<td>er</td>");

      // The dictionary entry is nested behind its own disclosure inside Details.
      const deep = container.querySelector("details.extra-deep");
      expect(deep?.querySelector("summary")?.textContent).toBe("Dictionary entry");
      expect(deep?.innerHTML).toContain("<h2>være</h2>");
    });

    it("renders extras on the production answer side too", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const { findByRole, container } = render(DrillCard, {
        item: withExtras(),
        direction: "production",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(container.querySelector(".extras-summary")?.textContent).toContain("/ˈʋæːɾə/");
      expect(container.querySelector("details.extra-deep")).toBeTruthy();
    });

    it("renders no Details disclosure when only summary extras exist", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "være",
        extras: [{ label: "IPA", html: "/ˈʋæːɾə/", tier: "summary" }],
      });
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(container.querySelector(".extras-summary")).toBeTruthy();
      expect(container.querySelector("details.extras-details")).toBeNull();
    });

    it("renders Details with no summary section when only collapsed extras exist", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({
        text: "være",
        extras: [{ label: "Inflections", html: "<i>er</i>", tier: "details" }],
      });
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(container.querySelector(".extras-summary")).toBeNull();
      expect(container.querySelector("details.extras-details")).toBeTruthy();
      expect(container.querySelector("details.extra-deep")).toBeNull();
    });

    it("renders nothing extra when the item has no extras (undefined)", async () => {
      const onRate = vi.fn().mockResolvedValue(undefined);
      const item = makeSRSItemDetail({ text: "være" });
      const { findByRole, container } = render(DrillCard, {
        item,
        direction: "recognition",
        onRate,
      });
      await fireEvent.click(await findByRole("button", { name: "Show" }));
      expect(container.querySelector(".extras-summary")).toBeNull();
      expect(container.querySelector("details.extras-details")).toBeNull();
    });
  });
});
