/**
 * The confirm-before-write cloze rewrite (tunatale-keb0).
 *
 * The control used to sit on the drill card's answer side and write on the
 * single click that produced a sentence — the row went dirty immediately, so a
 * sync firing before the learner had read it had already rewritten the Anki
 * note in place. This dialog is the replacement: it asks, it shows, it writes
 * only when told.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, waitFor } from "@testing-library/svelte";
import { makeSRSItemDetail } from "../../test/factories";

vi.mock("$lib/api", () => ({
  api: {
    proposeClozeSentence: vi.fn(),
    setClozeSentence: vi.fn(),
  },
}));

import { api } from "$lib/api";
import ClozeSentenceModal from "./ClozeSentenceModal.svelte";

const mockPropose = vi.mocked(api.proposeClozeSentence);
const mockSet = vi.mocked(api.setClozeSentence);

const clozeItem = () =>
  makeSRSItemDetail({
    id: 42,
    text: "han",
    card_type: "cloze",
    source_sentence: "{{c1::han}} kommer i morgen",
    source_sentence_translation: "he is coming tomorrow",
  });

const proposal = (over: Record<string, unknown> = {}) => ({
  current: {
    sentence: "{{c1::han}} kommer i morgen",
    translation: "he is coming tomorrow",
    status: "underdetermined",
    competitors: ["hun", "jeg"],
  },
  candidate: {
    sentence: "Kari ringte. {{c1::Han}} tar toget.",
    translation: "Kari called. He takes the train.",
    status: "determined",
    competitors: [],
  },
  recommended: true,
  ...over,
});

const open = (item = clozeItem()) => {
  const onclose = vi.fn();
  const onupdated = vi.fn();
  return {
    onclose,
    onupdated,
    ...render(ClozeSentenceModal, { props: { item, onclose, onupdated } }),
  };
};

describe("ClozeSentenceModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPropose.mockResolvedValue(proposal());
    mockSet.mockResolvedValue({
      sentence: "Kari ringte. {{c1::Han}} tar toget.",
      translation: "Kari called. He takes the train.",
    });
  });

  describe("nothing is written until the human says so", () => {
    it("suggesting a sentence does not store it", async () => {
      const { getByRole } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      await waitFor(() => expect(mockPropose).toHaveBeenCalledWith(42));
      expect(mockSet).not.toHaveBeenCalled();
    });

    it("suggesting twice still stores nothing", async () => {
      const { getByRole } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      await waitFor(() => expect(mockPropose).toHaveBeenCalledTimes(1));
      await fireEvent.click(getByRole("button", { name: /suggest another/i }));
      await waitFor(() => expect(mockPropose).toHaveBeenCalledTimes(2));
      expect(mockSet).not.toHaveBeenCalled();
    });

    it("cancelling after a suggestion stores nothing", async () => {
      const { getByRole, onclose, onupdated } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      await waitFor(() => expect(mockPropose).toHaveBeenCalled());
      await fireEvent.click(getByRole("button", { name: /cancel/i }));
      expect(mockSet).not.toHaveBeenCalled();
      expect(onclose).toHaveBeenCalled();
      expect(onupdated).not.toHaveBeenCalled();
    });

    it("stores the accepted sentence, and only then", async () => {
      const { getByRole, onupdated } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      await waitFor(() => expect(mockPropose).toHaveBeenCalled());
      await fireEvent.click(getByRole("button", { name: /use this sentence/i }));
      await waitFor(() =>
        expect(mockSet).toHaveBeenCalledWith(42, "Kari ringte. {{c1::Han}} tar toget."),
      );
      expect(onupdated).toHaveBeenCalled();
    });
  });

  describe("what the human is shown before deciding", () => {
    it("shows the stored sentence blanked, before any call", () => {
      const { getByText } = open();
      expect(getByText("___ kommer i morgen")).toBeTruthy();
      expect(getByText("he is coming tomorrow")).toBeTruthy();
      expect(mockPropose).not.toHaveBeenCalled();
    });

    it("shows the proposal blanked, with its own translation", async () => {
      const { getByRole, getByText } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      // The proposal's English, not the stored one: a sentence carrying its
      // predecessor's translation is the defect this dialog exists to stop.
      expect(await waitFor(() => getByText("Kari ringte. ___ tar toget."))).toBeTruthy();
      expect(getByText("Kari called. He takes the train.")).toBeTruthy();
    });

    it("names the words that also fit the current blank", async () => {
      const { getByRole, getByText } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      // The competitor LIST is the useful part; "underdetermined" is a normal
      // verdict for the closed-class words this feature is for.
      expect(await waitFor(() => getByText(/2 other words also fit: hun, jeg/))).toBeTruthy();
    });

    it("says when only the answer fits", async () => {
      const { getByRole, getByText } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      expect(await waitFor(() => getByText(/Only .han. fits this blank/))).toBeTruthy();
    });

    it("says so plainly when the judge returned nothing usable", async () => {
      // "unknown" is not "the sentence is fine" — the model said nothing the
      // judge could parse, and a reader must not read silence as approval.
      mockPropose.mockResolvedValue(
        proposal({
          current: {
            sentence: "{{c1::han}} kommer i morgen",
            translation: "he is coming tomorrow",
            status: "unknown",
            competitors: [],
          },
        }),
      );
      const { getByRole, getByText } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      expect(await waitFor(() => getByText(/no usable verdict/i))).toBeTruthy();
    });

    it("still says more than one word fits when the judge named none", async () => {
      mockPropose.mockResolvedValue(
        proposal({
          candidate: {
            sentence: "{{c1::Han}} liker kaffe.",
            translation: "He likes coffee.",
            status: "underdetermined",
            competitors: [],
          },
          recommended: false,
        }),
      );
      const { getByRole, getByText } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      expect(await waitFor(() => getByText(/More than one word fits/))).toBeTruthy();
    });

    it("flags the machine's preference without acting on it", async () => {
      const { getByRole, getByText } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      expect(await waitFor(() => getByText("recommended"))).toBeTruthy();
      expect(mockSet).not.toHaveBeenCalled();
    });

    it("offers a worse candidate unflagged rather than hiding it", async () => {
      mockPropose.mockResolvedValue(
        proposal({
          candidate: {
            sentence: "{{c1::Han}} liker kaffe.",
            translation: "He likes coffee.",
            status: "underdetermined",
            competitors: ["hun", "jeg", "du"],
          },
          recommended: false,
        }),
      );
      const { getByRole, getByText, queryByText } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      expect(await waitFor(() => getByText("___ liker kaffe."))).toBeTruthy();
      expect(queryByText("recommended")).toBeNull();
    });
  });

  describe("hand editing", () => {
    it("starts on the stored sentence so a one-word fix needs no model call", () => {
      const { getByLabelText } = open();
      expect((getByLabelText(/sentence to store/i) as HTMLTextAreaElement).value).toBe(
        "{{c1::han}} kommer i morgen",
      );
      expect(mockPropose).not.toHaveBeenCalled();
    });

    it("stores a plain typed sentence, markup and all left to the server", async () => {
      const { getByRole, getByLabelText } = open();
      const box = getByLabelText(/sentence to store/i);
      await fireEvent.input(box, { target: { value: "Kari ringte. Han tar toget." } });
      await fireEvent.click(getByRole("button", { name: /use this sentence/i }));
      await waitFor(() => expect(mockSet).toHaveBeenCalledWith(42, "Kari ringte. Han tar toget."));
    });

    it("stores an edit made to a suggestion, not the suggestion", async () => {
      const { getByRole, getByLabelText } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      await waitFor(() => expect(mockPropose).toHaveBeenCalled());
      const box = getByLabelText(/sentence to store/i);
      await fireEvent.input(box, { target: { value: "Kari ringte. Han tok toget." } });
      await fireEvent.click(getByRole("button", { name: /use this sentence/i }));
      await waitFor(() => expect(mockSet).toHaveBeenCalledWith(42, "Kari ringte. Han tok toget."));
    });

    it("will not store the sentence already stored", () => {
      const { getByRole } = open();
      // Nothing to confirm yet: the write would mark the row dirty and push an
      // identical sentence to Anki for no reason.
      expect(
        (getByRole("button", { name: /use this sentence/i }) as HTMLButtonElement).disabled,
      ).toBe(true);
    });
  });

  describe("when things go wrong", () => {
    it("says so when the generator produced nothing, rather than showing a blank", async () => {
      mockPropose.mockResolvedValue(proposal({ candidate: null, recommended: false }));
      const { getByRole, getByText } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      expect(await waitFor(() => getByText(/No new sentence came back/))).toBeTruthy();
    });

    it("surfaces a failed suggestion instead of leaving the button spinning", async () => {
      mockPropose.mockRejectedValue(new Error("LLM not configured"));
      const { getByRole, getByText } = open();
      await fireEvent.click(getByRole("button", { name: /suggest a sentence/i }));
      expect(await waitFor(() => getByText("LLM not configured"))).toBeTruthy();
      expect(
        (getByRole("button", { name: /suggest a sentence/i }) as HTMLButtonElement).disabled,
      ).toBe(false);
    });

    it("surfaces a rejected sentence and keeps the dialog open", async () => {
      mockSet.mockRejectedValue(new Error("'han' does not occur in that sentence"));
      const { getByRole, getByLabelText, getByText, onupdated } = open();
      await fireEvent.input(getByLabelText(/sentence to store/i), {
        target: { value: "Kari tar toget." },
      });
      await fireEvent.click(getByRole("button", { name: /use this sentence/i }));
      expect(await waitFor(() => getByText(/does not occur in that sentence/))).toBeTruthy();
      expect(onupdated).not.toHaveBeenCalled();
    });

    it("disables the save while one is in flight", async () => {
      let release: (v: { sentence: string; translation: string }) => void = () => {};
      mockSet.mockImplementation(() => new Promise((r) => (release = r)));
      const { getByRole, getByLabelText } = open();
      await fireEvent.input(getByLabelText(/sentence to store/i), {
        target: { value: "Kari ringte. Han tar toget." },
      });
      const save = getByRole("button", { name: /use this sentence/i }) as HTMLButtonElement;
      await fireEvent.click(save);
      await waitFor(() => expect(save.disabled).toBe(true));
      release({ sentence: "x", translation: "y" });
    });
  });

  it("closes on Escape", async () => {
    const { onclose } = open();
    await fireEvent.keyDown(document.querySelector(".backdrop")!, { key: "Escape" });
    expect(onclose).toHaveBeenCalled();
  });

  it("closes on the header's × without writing", async () => {
    const { getByRole, onclose, onupdated } = open();
    await fireEvent.click(getByRole("button", { name: /close/i }));
    expect(onclose).toHaveBeenCalled();
    expect(onupdated).not.toHaveBeenCalled();
    expect(mockSet).not.toHaveBeenCalled();
  });

  it("does not close on a keypress inside the dialog", async () => {
    // Escape must reach the backdrop handler, but typing in the edit box must
    // not: an Escape-like key bubbling out of the textarea would discard a
    // hand-written sentence.
    const { onclose } = open();
    await fireEvent.keyDown(document.querySelector(".modal")!, { key: "Escape" });
    expect(onclose).not.toHaveBeenCalled();
  });

  it("closes on a backdrop click but not on a click inside", async () => {
    const { onclose } = open();
    await fireEvent.click(document.querySelector(".modal")!);
    expect(onclose).not.toHaveBeenCalled();
    await fireEvent.click(document.querySelector(".backdrop")!);
    expect(onclose).toHaveBeenCalled();
  });
});
