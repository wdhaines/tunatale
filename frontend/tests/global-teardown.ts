// Fails the e2e run on any LLM cassette miss, including ones the app swallowed
// fail-soft. Rationale and message live in ./cassette-misses.ts (tunatale-1l26.7).
import { WORKER_COUNT } from '../playwright.config';
import { assertNoCassetteMisses, cassetteMissLogName } from './cassette-misses';

export default function globalTeardown(): void {
	assertNoCassetteMisses(
		Array.from({ length: WORKER_COUNT }, (_, i) => `../backend/${cassetteMissLogName(i)}`)
	);
}
