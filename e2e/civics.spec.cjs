const { test, expect } = require('@playwright/test');

test('live civics dashboard renders seeded MVP data and bill detail', async ({ page, request }) => {
  const seed = await request.post('/api/admin/generate-matches');
  expect(seed.ok()).toBeTruthy();

  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Civics Radar' })).toBeVisible();
  await expect(page.locator('#billCount')).toHaveText('3');
  await expect(page.locator('#matchCount')).not.toHaveText('—');
  await expect(page.getByRole('heading', { name: 'Civic Classroom and Library Grant Act' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Tenant Stability and Emergency Rental Assistance Act' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Prescription Drug Transparency and Rural Clinic Support Act' })).toBeVisible();

  await page.getByRole('heading', { name: 'Civic Classroom and Library Grant Act' }).click();
  await expect(page.locator('#detail')).toContainText('Audit flags');
  await expect(page.locator('#detail')).toContainText('Education');
  await expect(page.locator('#detail')).toContainText('USA.gov: find elected officials');
});

test('live API audits are idempotent', async ({ request }) => {
  const first = await request.post('/api/admin/run-audits');
  expect(first.ok()).toBeTruthy();
  const body = await first.json();
  expect(body.audit_runs_created).toBe(0);

  const dashboard = await request.get('/api/dashboard');
  expect(dashboard.ok()).toBeTruthy();
  const data = await dashboard.json();
  expect(data.matches.length).toBeGreaterThanOrEqual(3);
  expect(data.notifications.length).toBe(data.matches.length);
});
