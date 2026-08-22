import { test as base, expect } from '@playwright/test';

const workerIndex = () => Number(process.env.TEST_PARALLEL_INDEX ?? 0);

export const PORTS = {
	backend: () => 8001 + 2 * workerIndex(),
	frontend: () => 5174 + workerIndex()
};

export const test = base.extend<object, { backendURL: string }>({
	backendURL: [async ({}, use) => { await use(`http://localhost:${PORTS.backend()}`); }, { scope: 'worker' }],
	baseURL: async ({}, use) => { await use(`http://localhost:${PORTS.frontend()}`); }
});

export { expect };
