/**
 * The verb chart component (tunatale-h0q3.47): the desktop grid and the phone cards
 * (owner's option A) are both rendered; CSS picks one, so jsdom sees both. Geometry is
 * not asserted here (jsdom has no layout); the grid arithmetic is verbChartLayout.test.ts.
 */
import { describe, expect, it } from "vitest";
import { fireEvent, render } from "@testing-library/svelte";
import type { VerbChart as Chart } from "#lib/api.js";
import chartJson from "../../../../backend/app/plugins/languages/ceb/data/verb_chart.json";
import VerbChart from "./VerbChart.svelte";

const chart = chartJson as Chart;

function card(container: HTMLElement, rowId: string): HTMLElement {
  return container.querySelector(`section.card[data-row="${rowId}"]`) as HTMLElement;
}

describe("VerbChart", () => {
  it("keeps the how-to-read intro behind a closed disclosure", () => {
    const { container } = render(VerbChart, { props: { chart } });
    const intro = container.querySelector("details.intro") as HTMLDetailsElement;
    expect(intro.open).toBe(false);
    expect(intro.querySelector("summary")?.textContent).toBe("How to read the chart");
  });

  it("separates a card's aspect from its hint with a space", () => {
    const { container } = render(VerbChart, { props: { chart } });
    expect(card(container, "agent-pan").querySelector(".card-head span")?.textContent).toBe(
      "Distributive many, or again and again",
    );
  });

  it("draws the column heads and the intro's italics", () => {
    const { container, getAllByRole } = render(VerbChart, { props: { chart } });
    const heads = getAllByRole("columnheader").map((th) => th.childNodes[0].textContent);
    expect(heads).toEqual([
      "Verb type",
      "Trigger",
      "Aspect",
      "Volitional",
      "Potential",
      "Realis",
      "Irrealis",
      "Dependent",
      "Imperative",
      "Realis",
      "Irrealis",
      "Dependent",
    ]);
    expect(container.querySelector(".intro em")?.textContent).toBe("ang");
  });

  it("shows each example in the Handbook spelling, with the source's spelling on hover and its tag", () => {
    const { container } = render(VerbChart, { props: { chart } });
    const first = container.querySelector(
      'tr[data-row="agent-mo"].line-examples .ex',
    ) as HTMLElement;
    expect(first.querySelector(".ceb")?.textContent).toBe(
      "Mìádto (nìádto, ning-ádto, ming-ádto) ko dídto gahápon",
    );
    expect(first.getAttribute("title")).toBe(
      "Miadtu (niadtu, ning-adtu, ming-adtu) ku didtu gahápun",
    );
    expect(first.querySelector(".src")?.textContent).toBe("W");
    const tags = [...container.querySelectorAll(".grid .src")].map((el) => el.textContent);
    expect(tags).toContain("Wp");
    expect(tags).not.toContain("P");
  });

  it("leaves the literary rows and chips out until the box is ticked", async () => {
    const { container, getByLabelText } = render(VerbChart, { props: { chart } });
    // The grid and the cards only: the legend always shows a literary sample chip.
    const has = (label: string) =>
      [...container.querySelectorAll(".grid .chip, .cards .chip")].some(
        (c) => c.textContent === label,
      );
    expect(container.querySelector('tr[data-row="patient-paga"]')).toBeNull();
    expect(has("gina-")).toBe(false);
    expect(has("naga-")).toBe(false);

    await fireEvent.click(getByLabelText("Show literary and archaic forms"));

    expect(container.querySelector('tr[data-row="patient-paga"]')).not.toBeNull();
    expect(has("gina-")).toBe(true);
    expect(has("naga-")).toBe(true);
  });

  it("highlights the row a link targets, and opens its phone card", () => {
    const { container } = render(VerbChart, { props: { chart, targetRow: "patient-on" } });
    const target = [...container.querySelectorAll("tr.target")].map((tr) =>
      tr.getAttribute("data-row"),
    );
    expect(new Set(target)).toEqual(new Set(["patient-on"]));
    expect(card(container, "patient-on").classList.contains("target")).toBe(true);
    expect(
      card(container, "patient-on").querySelector(".card-head")?.getAttribute("aria-expanded"),
    ).toBe("true");
    expect(
      card(container, "agent-mo").querySelector(".card-head")?.getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("opens a phone card on a tap: every column with its affix and examples, and closes it again", async () => {
    const { container } = render(VerbChart, { props: { chart } });
    const agent = card(container, "agent-mo");
    expect(agent.querySelectorAll(".entry")).toHaveLength(0);
    expect([...agent.querySelectorAll(".summary .chip")].map((c) => c.textContent)).toEqual([
      "mi-, ni-",
      "mo-",
      "∅, pag-",
      "naka-, ka-",
      "maka-, ka-",
    ]);

    await fireEvent.click(agent.querySelector(".card-head")!);
    const entries = [...agent.querySelectorAll(".entry")].map(
      (e) =>
        `${e.querySelector(".entry-label")?.textContent}:${[...e.querySelectorAll(".chip")].map((c) => c.textContent).join("|")}`,
    );
    expect(entries).toEqual([
      "Realis:mi-, ni-|ming-, ning-",
      "Irrealis:mo-",
      "Dependent:mo-",
      "Imperative:∅, pag-",
      "Realis:naka-, ka-",
      "Irrealis:maka-, ka-",
      "Dependent:maka-, ka-",
    ]);

    await fireEvent.click(agent.querySelector(".card-head")!);
    expect(agent.querySelectorAll(".entry")).toHaveLength(0);
  });

  it("says a side is the same as the perfective instead of repeating it", async () => {
    const { container } = render(VerbChart, { props: { chart } });
    const progressive = card(container, "agent-mag");
    await fireEvent.click(progressive.querySelector(".card-head")!);
    expect(progressive.querySelector(".side-note")?.textContent).toBe(
      "Potential: same as the perfective",
    );
  });

  it("lists the legend, the footnotes and the sources", () => {
    const { container } = render(VerbChart, { props: { chart } });
    expect([...container.querySelectorAll(".legend .chip")].map((c) => c.textContent)).toEqual([
      "mo-",
      "maga-",
      "naga-",
      "mapaN-",
    ]);
    expect([...container.querySelectorAll(".footnotes li")].map((li) => li.id)).toEqual([
      "fn-1",
      "fn-2",
      "fn-3",
      "fn-4",
      "fn-5",
      "fn-6",
      "fn-7",
      "fn-8",
    ]);
    expect(container.querySelector("#fn-2 .affix")?.textContent).toBe("-a");
    expect([...container.querySelectorAll(".sources .src")].map((s) => s.textContent)).toEqual([
      "W",
      "B",
      "Wp",
    ]);
  });

  it("points the two jump bars at the grid and at the cards", () => {
    const { container } = render(VerbChart, { props: { chart } });
    const hrefs = (cls: string) =>
      [...container.querySelectorAll(`.jump.${cls} a`)].map((a) => a.getAttribute("href"));
    expect(hrefs("wide")).toEqual([
      "#group-agent",
      "#group-patient",
      "#group-circumstance",
      "#group-instrument",
      "#group-stative",
    ]);
    expect(hrefs("narrow")).toEqual([
      "#card-group-agent",
      "#card-group-patient",
      "#card-group-circumstance",
      "#card-group-instrument",
      "#card-group-stative",
    ]);
    expect(container.querySelector("#group-agent")).not.toBeNull();
    expect(container.querySelector("#card-group-agent")).not.toBeNull();
  });

  it("omits an open card's side that has no forms", async () => {
    const tiny: Chart = {
      ...chart,
      groups: [
        {
          id: "agent",
          label: "Agent",
          hint: "",
          verb_type: "dynamic",
          footnote: null,
          rows: [{ ...chart.groups[0].rows[0], id: "only", potential: null }],
        },
      ],
    };
    const { container } = render(VerbChart, { props: { chart: tiny, targetRow: "only" } });
    const sides = [...card(container, "only").querySelectorAll(".side")].map((h) => h.textContent);
    expect(sides).toEqual(["Volitional · done on purpose"]);
    expect(container.querySelector("td.none")?.getAttribute("colspan")).toBe("3");
  });
});
