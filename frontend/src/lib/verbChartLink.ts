/**
 * The address that opens the verb chart at a form (tunatale-h0q3.47): its row, with the
 * affixes to mark in it. The backend writes the same address into a card's verb forms
 * (`ceb/chart_links.py::href`), so the chart page reads one shape from both.
 */
export interface ChartPlace {
  row: string;
  affixes: string[];
}

export function chartHref(place: ChartPlace): string {
  const params = new URLSearchParams({ row: place.row });
  for (const a of place.affixes) params.append("affix", a);
  return `/reference/verb-chart?${params.toString()}`;
}
