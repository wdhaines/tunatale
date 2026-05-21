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
});
