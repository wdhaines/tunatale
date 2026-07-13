import { test, expect } from '@playwright/test';
import * as path from 'node:path';
import * as fs from 'node:fs';
import * as http from 'node:http';
import { fileURLToPath } from 'node:url';
import { backendAvailable, seedSRSItems, resetSRSItems } from './helpers';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

test.describe('card image upload', () => {
	test('upload image via modal shows thumbnail on card', async ({ page, request }) => {
		test.skip(!(await backendAvailable(request)), 'Backend not available');
		await resetSRSItems(request);

		const itemText = 'testimg-' + Date.now();
		await seedSRSItems(request, [
			{ text: itemText, translation: 'image test' },
		]);

		await page.goto('/cards');
		const row = page.locator('.row').filter({ hasText: itemText });
		await expect(row).toBeVisible({ timeout: 10000 });

		// No thumbnail before upload
		await expect(row.locator('.col-img img')).toHaveCount(0);

		// Open row menu → Change image…
		await row.getByRole('button', { name: new RegExp(`^Actions for ${itemText}`) }).click();
		await row.getByRole('menuitem', { name: 'Change image…' }).click();

		// Modal should be open
		const modal = page.getByRole('dialog', { name: 'Edit image' });
		await expect(modal).toBeVisible();

		// Upload the fixture PNG
		const fileInput = modal.locator('input[type="file"]');
		await fileInput.setInputFiles(path.join(__dirname, 'fixtures', 'sample.png'));

		// Wait for the upload to complete and modal to close (onupdated fires)
		await expect(modal).not.toBeVisible({ timeout: 10000 });

		// Thumbnail should now be visible in the row
		await expect(row.locator('.col-img img')).toBeVisible({ timeout: 5000 });

		// Clean up
		await resetSRSItems(request);
	});

	test('remove image from card', async ({ page, request }) => {
		test.skip(!(await backendAvailable(request)), 'Backend not available');
		await resetSRSItems(request);

		const itemText = 'testremove-' + Date.now();
		await seedSRSItems(request, [
			{ text: itemText, translation: 'remove test' },
		]);

		// First upload an image via the backend API (PUT, not POST)
		const itemsRes = await request.get(`http://localhost:8001/api/srs/items?search=${itemText}`);
		const items = await itemsRes.json();
		const itemId = items.items[0].id;
		const uploadRes = await request.put(
			`http://localhost:8001/api/srs/items/${itemId}/image/upload`,
			{
				multipart: {
					file: {
						name: 'sample.png',
						mimeType: 'image/png',
						buffer: fs.readFileSync(path.join(__dirname, 'fixtures', 'sample.png')),
					},
				},
			},
		);
		expect(uploadRes.ok()).toBeTruthy();

		// Reload cards page
		await page.goto('/cards');
		const row = page.locator('.row').filter({ hasText: itemText });
		await expect(row.locator('.col-img img')).toBeVisible({ timeout: 10000 });

		// Open modal → click Remove
		await row.getByRole('button', { name: new RegExp(`^Actions for ${itemText}`) }).click();
		await row.getByRole('menuitem', { name: 'Change image…' }).click();

		const modal = page.getByRole('dialog', { name: 'Edit image' });
		await expect(modal).toBeVisible();
		await modal.getByRole('button', { name: 'Remove' }).click();

		// Modal closes, thumbnail disappears
		await expect(modal).not.toBeVisible({ timeout: 10000 });
		await expect(row.locator('.col-img img')).toHaveCount(0);

		await resetSRSItems(request);
	});

	test('set image via paste URL', async ({ page, request }) => {
		test.skip(!(await backendAvailable(request)), 'Backend not available');
		await resetSRSItems(request);

		const itemText = 'testurl-' + Date.now();
		await seedSRSItems(request, [
			{ text: itemText, translation: 'url test' },
		]);

		// Start a tiny local HTTP server serving the fixture PNG
		const fixturePng = fs.readFileSync(path.join(__dirname, 'fixtures', 'sample.png'));
		const server = http.createServer((_req, res) => {
			res.writeHead(200, { 'Content-Type': 'image/png' });
			res.end(fixturePng);
		});
		await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
		const addr = server.address() as { port: number } | null;
		const port = addr?.port ?? 0;

		try {
			await page.goto('/cards');
			const row = page.locator('.row').filter({ hasText: itemText });
			await expect(row).toBeVisible({ timeout: 10000 });

			// Open modal
			await row.getByRole('button', { name: new RegExp(`^Actions for ${itemText}`) }).click();
			await row.getByRole('menuitem', { name: 'Change image…' }).click();

			const modal = page.getByRole('dialog', { name: 'Edit image' });
			await expect(modal).toBeVisible();

			// Fill the paste URL input with our local server
			await modal.getByPlaceholder('https://example.com/image.jpg').fill(`http://127.0.0.1:${port}/img.png`);
			await modal.getByRole('button', { name: 'Set' }).click();

			// Modal closes after successful set
			await expect(modal).not.toBeVisible({ timeout: 10000 });

			// Thumbnail should appear
			await expect(row.locator('.col-img img')).toBeVisible({ timeout: 5000 });
		} finally {
			await resetSRSItems(request);
			server.close();
		}
	});
});
