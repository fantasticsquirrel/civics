const { test, expect } = require('@playwright/test');

const bootstrap = 'playwright-bootstrap-secret-32-bytes';
let token;

async function openWorkspace(page) {
  await page.goto('/');
  await page.getByLabel('Bearer token').fill(token);
  await page.getByRole('button', { name: 'Open workspace' }).click();
  await expect(page.getByRole('heading', { name: 'Feed' })).toBeVisible();
}

test.beforeAll(async ({ request }) => {
  await request.post('/api/admin/seed', { headers: { Authorization: `Bearer ${bootstrap}` } });
  const response = await request.post('/api/admin/accounts', {
    headers: { Authorization: `Bearer ${bootstrap}` },
    data: { account_name: `E2E ${Date.now()}`, email: `e2e-${Date.now()}@example.com`, role: 'admin' },
  });
  expect(response.ok()).toBeTruthy();
  token = (await response.json()).api_token;
  const auth = { Authorization: `Bearer ${token}` };
  await request.post('/api/admin/sync-demo-bills', { headers: auth });
  await request.post('/api/admin/run-audits', { headers: auth });
  await request.post('/api/admin/generate-matches', { headers: auth });
});

test('authenticated responsive workspace supports every view and bill detail', async ({ page }) => {
  await openWorkspace(page);
  for (const name of ['Bills', 'Alerts', 'Saved Views', 'Settings', 'Admin']) {
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
  }
  await page.getByRole('button', { name: 'Bills', exact: true }).click();
  await page.locator('.bill').first().click();
  await expect(page.getByRole('heading', { name: 'Audit provenance' })).toBeVisible();
  await expect(page.getByText('Official record')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
});

test('saved views are functional and token never enters URL', async ({ page }) => {
  await openWorkspace(page);
  await page.getByRole('button', { name: 'Bills', exact: true }).click();
  await page.getByLabel('Search bills').fill('library');
  await page.getByRole('button', { name: 'Saved Views', exact: true }).click();
  await page.getByPlaceholder('View name').fill('Federal education');
  await page.getByRole('button', { name: 'Save current bill filters' }).click();
  await expect(page.getByText('Federal education')).toBeVisible();
  await page.getByRole('button', { name: 'Apply', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Bills', exact: true })).toBeVisible();
  await expect(page.getByLabel('Search bills')).toHaveValue('library');
  expect(page.url()).not.toContain(token);
});

test('admin can version taxonomy and persist user settings', async ({ page }) => {
  await openWorkspace(page);
  await page.getByRole('button', { name: 'Admin', exact: true }).click();
  await page.getByPlaceholder('category-slug').fill('e2e-energy');
  await page.getByPlaceholder('Category name').fill('E2E Energy');
  await page.getByPlaceholder('Neutral description').fill('Energy generation and grid reliability.');
  await page.getByPlaceholder('term, phrase').fill('energy, grid');
  await page.getByRole('button', { name: 'Add category' }).click();
  const category = page.locator('#adminCategories p').filter({ hasText: 'E2E Energy' });
  await expect(category).toContainText('active');
  await category.getByRole('button', { name: 'Deactivate' }).click();
  await expect(category).toContainText('inactive');

  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByLabel('Education').check();
  await page.getByLabel('Minimum severity').selectOption('medium');
  await page.getByLabel('Jurisdictions').fill('US,MO');
  await page.getByRole('button', { name: 'Save interests' }).click();
  await page.getByLabel('Digest frequency').selectOption('daily');
  await page.getByRole('checkbox', { name: 'Email' }).check();
  await page.getByRole('button', { name: 'Save settings' }).click();
  await page.getByRole('button', { name: 'Bills', exact: true }).click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByLabel('Minimum severity')).toHaveValue('medium');
  await expect(page.getByLabel('Jurisdictions')).toHaveValue('US,MO');
  await expect(page.getByLabel('Digest frequency')).toHaveValue('daily');
  await expect(page.getByRole('checkbox', { name: 'Email' })).toBeChecked();
});
