import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { assertNoCassetteMisses, cassetteMissLogName } from './cassette-misses';

// tunatale-1l26.7. The e2e backends append one line per LLM cassette miss to a
// per-worker file; global teardown calls assertNoCassetteMisses over them. A
// miss the app swallows fail-soft (the gloss pass does, by design) is otherwise
// a WARNING nobody reads, which is how a lesson shipped unglossed for a day
// while every gate stayed green.
describe('assertNoCassetteMisses', () => {
	let dir: string;
	beforeEach(() => {
		dir = mkdtempSync(join(tmpdir(), 'tt-cassette-misses-'));
	});
	afterEach(() => {
		rmSync(dir, { recursive: true, force: true });
	});

	it('passes when no backend wrote a miss file', () => {
		expect(() => assertNoCassetteMisses([join(dir, 'a.log'), join(dir, 'b.log')])).not.toThrow();
	});

	it('passes on an empty miss file', () => {
		const f = join(dir, 'a.log');
		writeFileSync(f, '');
		expect(() => assertNoCassetteMisses([f])).not.toThrow();
	});

	it('throws on any miss, naming the hash, the prompt, and the file it came from', () => {
		const clean = join(dir, 'a.log');
		const dirty = join(dir, 'b.log');
		writeFileSync(clean, '');
		writeFileSync(dirty, 'sha256:b6897cfc57d5e773\tno entry\tBelow are Slovene dialogue lines.\n');
		expect(() => assertNoCassetteMisses([clean, dirty])).toThrow(
			/1 LLM cassette miss[\s\S]*b\.log[\s\S]*sha256:b6897cfc57d5e773[\s\S]*Below are Slovene dialogue lines/
		);
	});

	it('counts misses across every worker, not just the first dirty file', () => {
		const a = join(dir, 'a.log');
		const b = join(dir, 'b.log');
		writeFileSync(a, 'sha256:1\tno entry\tp1\nsha256:2\tno entry\tp2\n');
		writeFileSync(b, 'sha256:3\texhausted after 1\tp3\n');
		expect(() => assertNoCassetteMisses([a, b])).toThrow(/3 LLM cassette misses/);
	});

	it('says how to fix it, since the reader is looking at a teardown error, not a spec', () => {
		const f = join(dir, 'a.log');
		writeFileSync(f, 'sha256:1\tno entry\tp\n');
		expect(() => assertNoCassetteMisses([f])).toThrow(/LLM_MODE=patch/);
	});
});

describe('cassetteMissLogName', () => {
	it('is per worker, so two backends never interleave writes into one file', () => {
		expect(cassetteMissLogName(0)).not.toBe(cassetteMissLogName(1));
		expect(cassetteMissLogName(0)).toMatch(/\.log$/); // *.log is gitignored
	});
});
