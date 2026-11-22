/**
 * One lesson line's cards, tapped on and off (tunatale-h0q3.67). The toggle rule is
 * pinned in sentenceStage.test.ts; this pins what the line shows for each state.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/svelte";
import {
  initStage,
  type SentenceLine,
  type SentenceLineToken,
} from "#lib/grading/sentenceStage.js";
import SentenceLineEditor from "./SentenceLineEditor.svelte";

const tok = (
  start: number,
  end: number,
  over: Partial<SentenceLineToken> = {},
): SentenceLineToken => ({
  start,
  end,
  part: null,
  state: "off",
  origin: "mine",
  reviewed: false,
  gloss: "",
  in_deck: false,
  ...over,
});

const line = (over: Partial<SentenceLine> = {}): SentenceLine => ({
  line_key: "sent:test-01:3",
  speaker: "Kari",
  text: "Bra, takk, og du?",
  english: "Fine, thanks, and you?",
  linked: false,
  dialogue_index: 2,
  spoken_map: [0, 1, 2, 3],
  tokens: [
    tok(0, 3, { gloss: "fine" }),
    tok(5, 9, { state: "on", origin: "lesson" }),
    tok(11, 13),
    tok(14, 16, { state: "declined", origin: "lesson" }),
  ],
  whole: tok(0, 17),
  ...over,
});

function mount(l: SentenceLine = line()) {
  const stage = $state(initStage(l));
  const onchange = vi.fn();
  const view = render(SentenceLineEditor, { props: { line: l, stage, onchange } });
  const word = (w: string) => view.getByRole("button", { name: new RegExp(`^${w}\\+?$`) });
  return { view, stage, onchange, word };
}

describe("SentenceLineEditor", () => {
  it("shows the server's words as buttons, the punctuation between them as text", () => {
    const { view } = mount();
    const say = view.container.querySelector(".say") as HTMLElement;
    expect(say.textContent).toBe("Bra, takk, og du?");
    expect([...say.querySelectorAll("button")].map((b) => b.textContent)).toEqual([
      "Bra",
      "takk",
      "og",
      "du",
    ]);
    expect(view.getByText("Kari")).toBeTruthy();
    expect(view.getByText("Fine, thanks, and you?")).toBeTruthy();
    expect(view.getByText("1 card")).toBeTruthy();
  });

  it("marks a card on, a lesson card turned off, and an off word", () => {
    const { word } = mount();
    expect(word("takk").className).toContain("on");
    expect(word("takk").getAttribute("aria-pressed")).toBe("true");
    expect(word("du").className).toContain("declined");
    expect(word("du").getAttribute("aria-pressed")).toBe("false");
    expect(word("og").className).not.toMatch(/on|declined/);
  });

  it("a tapped word becomes a blank of mine, captioned with the line's gloss or a note", async () => {
    const { view, onchange, word } = mount();
    await fireEvent.click(word("Bra"));
    await fireEvent.click(word("og"));
    expect(onchange).toHaveBeenCalledTimes(2);
    expect(word("Bra").className).toContain("mine");
    const caps = [...view.container.querySelectorAll(".cap")].map((c) => c.textContent?.trim());
    expect(caps).toEqual(["Bra+ fine", "og+ meaning written when you save"]);
    expect(view.getByText("3 cards")).toBeTruthy();
  });

  it("a word the lesson blanks part of shows the part, and the tap toggles it", async () => {
    const l = line({
      text: "Bilen er rød.",
      tokens: [tok(0, 5, { part: [3, 5], state: "on", origin: "lesson" }), tok(6, 8)],
    });
    const { view, stage } = mount(l);
    const bilen = view.getByRole("button", { name: "Bilen" });
    expect(bilen.querySelector(".part")?.textContent).toBe("en");
    expect(bilen.querySelector(".part")?.className).toContain("on");
    await fireEvent.click(bilen);
    expect(stage.cards["3-5"].on).toBe(false);
    expect(bilen.querySelector(".part")?.className).toContain("declined");
  });

  it("the whole-sentence pill toggles", async () => {
    const { view } = mount();
    const pill = view.getByRole("button", { name: "whole sentence" });
    await fireEvent.click(pill);
    expect(
      view.getByRole("button", { name: "whole sentence ✓" }).getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("a linked line's last card is refused in the page", async () => {
    const l = line({ linked: true });
    l.tokens[1] = { ...l.tokens[1], in_deck: true };
    const { view, word } = mount(l);
    await fireEvent.click(word("takk"));
    expect(view.getByRole("alert").textContent).toContain("keeps at least one card");
    expect(word("takk").getAttribute("aria-pressed")).toBe("true");
  });

  it("a reviewed card asks first: Keep it leaves it, Turn it off drops it", async () => {
    const l = line({
      tokens: [tok(5, 9, { state: "on", origin: "lesson", reviewed: true })],
      whole: tok(0, 17, { state: "on", origin: "lesson", reviewed: true }),
    });
    const { view, onchange, word } = mount(l);
    await fireEvent.click(word("takk"));
    expect(view.getByRole("alert").textContent).toContain("takk has been reviewed");
    await fireEvent.click(view.getByRole("button", { name: "Keep it" }));
    expect(view.queryByRole("alert")).toBeNull();
    await fireEvent.click(view.getByRole("button", { name: "whole sentence ✓" }));
    expect(view.getByRole("alert").textContent).toContain(
      "The whole-sentence card has been reviewed",
    );
    await fireEvent.click(view.getByRole("button", { name: "Turn it off" }));
    expect(view.getByRole("button", { name: "whole sentence" }).getAttribute("aria-pressed")).toBe(
      "false",
    );
    expect(onchange).toHaveBeenCalledTimes(4);
  });

  it("two blanks of the same word each get their caption (review F5)", async () => {
    const l = line({
      text: "det er det",
      tokens: [tok(0, 3), tok(4, 6), tok(7, 10)],
      whole: tok(0, 10),
    });
    const { view } = mount(l);
    const dets = view.getAllByRole("button", { name: "det" });
    await fireEvent.click(dets[0]);
    await fireEvent.click(dets[1]);
    expect(view.container.querySelectorAll(".cap").length).toBe(2);
  });

  it("works with no onchange", async () => {
    const stage = $state(
      initStage(line({ tokens: [tok(5, 9, { state: "on", origin: "lesson", reviewed: true })] })),
    );
    const view = render(SentenceLineEditor, {
      props: {
        line: line({ tokens: [tok(5, 9, { state: "on", origin: "lesson", reviewed: true })] }),
        stage,
      },
    });
    await fireEvent.click(view.getByRole("button", { name: "takk" }));
    await fireEvent.click(view.getByRole("button", { name: "Keep it" }));
    await fireEvent.click(view.getByRole("button", { name: "takk" }));
    await fireEvent.click(view.getByRole("button", { name: "Turn it off" }));
    expect(stage.cards["5-9"].on).toBe(false);
  });
});
