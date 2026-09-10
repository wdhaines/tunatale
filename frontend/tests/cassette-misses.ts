// The e2e half of tunatale-1l26.7: make an LLM cassette miss fail the run.
//
// A miss already raises in the backend (`app/llm/cassette.py::_replay`). That is
// loud when a spec's own request fails on it, and SILENT when a fail-soft caller
// catches it: the gloss pass does, by design, so from 96878b8 (2026-09-09) every
// e2e run missed its gloss entry, logged a WARNING, shipped the lesson with no
// glosses, and went green — locally and in CI, for a day.
//
// Each e2e backend therefore appends every miss to its own file
// (`LLM_CASSETTE_MISS_LOG`, set in playwright.config.ts), and global teardown
// reads them all. In e2e a miss is never a legitimate runtime condition, only a
// fixture gap, so any line is a failure.
//
// Kept apart from global-teardown.ts so vitest can test it: the teardown imports
// playwright.config.ts, whose module scope deletes DBs and runs a build.
import { existsSync, readFileSync } from 'node:fs';

/** Per-worker file name, relative to the backend's cwd. Per worker so two backends never share a file. */
export function cassetteMissLogName(worker: number): string {
	return `e2e-cassette-misses-${worker}.log`;
}

export function assertNoCassetteMisses(files: string[]): void {
	const found: string[] = [];
	for (const file of files) {
		if (!existsSync(file)) continue; // a backend that never missed never opens the file
		for (const line of readFileSync(file, 'utf8').split('\n')) {
			if (line.trim()) found.push(`  ${file}: ${line}`);
		}
	}
	if (found.length === 0) return;
	const noun = found.length === 1 ? 'miss' : 'misses';
	throw new Error(
		`${found.length} LLM cassette ${noun} during the e2e run. The app may have swallowed ` +
			`them (fail-soft), so no spec had to fail, but a miss in e2e is always a fixture gap:\n` +
			`${found.join('\n')}\n` +
			`Fix: re-record the missing entries into backend/tests/cassettes/e2e.json by running the ` +
			`spec that triggers them with the backend in LLM_MODE=patch and ONE worker (two patch-mode ` +
			`backends race on the file). Never delete a line here to get green.`
	);
}
