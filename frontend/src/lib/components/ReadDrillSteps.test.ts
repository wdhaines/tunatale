import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/svelte";
import type { Cue } from "#lib/api.js";
import type { PlaybackController } from "#lib/playback/playbackController.svelte.js";
import type { StoryDrill } from "#lib/playback/sectionLine.js";
import { captionBlurPref } from "#lib/stores/captionBlurPref.svelte.js";
import ReadDrillSteps from "./ReadDrillSteps.svelte";

// One story drill written out on its Read tab (tunatale-cs3p slice 3).

function cue(index: number, role: string, text: string, target: number | null): Cue {
  return {
    index,
    start_ms: index * 1000,
    end_ms: index * 1000 + 800,
    section_index: 0,
    section_type: "affix_drill",
    phrase_index: index,
    role,
    language_code: "ceb",
    text,
    ref: target === null ? null : { kind: "drill", target_index: target },
  };
}

const opener = cue(1, "narrator", "Change the sentence.", 0);
const model = cue(2, "model", "Nia siya dinhi.", 0);
const p1 = cue(3, "prompt", "si Huwan", 1);
const a1 = cue(4, "answer", "Nia si Huwan dinhi.", 1);
const p2 = cue(5, "prompt", "sila", null); // a cue with no ref: seek to it instead
const drill: StoryDrill = {
  opener,
  instruction: opener.text,
  model,
  label: model.text,
  steps: [
    { prompt: p1, answer: a1 },
    { prompt: p2, answer: null },
  ],
};

function controller(current: Cue | null = null): PlaybackController {
  return {
    currentCue: current,
    playRef: vi.fn(),
    seekToCue: vi.fn(),
  } as unknown as PlaybackController;
}

beforeEach(() => {
  localStorage.clear();
  captionBlurPref.set(true);
});

describe("ReadDrillSteps", () => {
  it("writes out the instruction, the model and every step", () => {
    const { getByText } = render(ReadDrillSteps, { props: { drill, controller: null } });
    expect(getByText("Change the sentence.")).toBeTruthy();
    expect(getByText("Nia siya dinhi.")).toBeTruthy();
    expect(getByText("si Huwan")).toBeTruthy();
    expect(getByText("sila")).toBeTruthy();
  });

  it("offers no ▶ without a player", () => {
    const { queryByRole } = render(ReadDrillSteps, { props: { drill, controller: null } });
    expect(queryByRole("button", { name: "Play this step" })).toBeNull();
    expect(queryByRole("button", { name: "Play the model sentence" })).toBeNull();
  });

  it("plays a step from its prompt by ref, and seeks to a cue that has none", async () => {
    const ctrl = controller();
    const { getAllByRole, getByRole } = render(ReadDrillSteps, {
      props: { drill, controller: ctrl },
    });
    const [first, second] = getAllByRole("button", { name: "Play this step" });
    await fireEvent.click(first);
    expect(ctrl.playRef).toHaveBeenCalledWith({ kind: "drill", target_index: 1 });
    await fireEvent.click(second);
    expect(ctrl.seekToCue).toHaveBeenCalledWith(p2);
    await fireEvent.click(getByRole("button", { name: "Play the model sentence" }));
    expect(ctrl.playRef).toHaveBeenCalledWith({ kind: "drill", target_index: 0 });
  });

  it("blurs each answer while captions are blurred, until it is tapped", async () => {
    const { getByRole, queryByRole, getByText } = render(ReadDrillSteps, {
      props: { drill, controller: null },
    });
    await fireEvent.click(getByRole("button", { name: "Show the answer" }));
    expect(queryByRole("button", { name: "Show the answer" })).toBeNull();
    expect(getByText("Nia si Huwan dinhi.").tagName).toBe("SPAN");
  });

  it("shows the answers plainly with captions on", () => {
    captionBlurPref.set(false);
    const { queryByRole, getByText } = render(ReadDrillSteps, {
      props: { drill, controller: null },
    });
    expect(queryByRole("button", { name: "Show the answer" })).toBeNull();
    expect(getByText("Nia si Huwan dinhi.")).toBeTruthy();
  });

  it("marks the step playing now, and none while another recording plays", () => {
    const playing = render(ReadDrillSteps, { props: { drill, controller: controller(a1) } });
    expect(playing.container.querySelector("li.current")?.textContent).toContain("si Huwan");
    playing.unmount();

    const elsewhere = render(ReadDrillSteps, {
      props: { drill, controller: controller({ ...a1, section_type: "natural_speed" }) },
    });
    expect(elsewhere.container.querySelector("li.current")).toBeNull();
    elsewhere.unmount();

    const narration = render(ReadDrillSteps, {
      props: { drill, controller: controller({ ...a1, ref: null }) },
    });
    expect(narration.container.querySelector("li.current")).toBeNull();
  });

  it("leaves out the model line for a drill that has none", () => {
    const { queryByText } = render(ReadDrillSteps, {
      props: { drill: { ...drill, model: null, label: drill.instruction }, controller: null },
    });
    expect(queryByText("Model")).toBeNull();
  });
});
