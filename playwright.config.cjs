// @ts-check
const { defineConfig } = require('@playwright/test');
const localDb = `/tmp/civics-playwright-${process.pid}-${Date.now()}.db`;

module.exports = defineConfig({
  testDir: './e2e',
  timeout: 30000,
  retries: 0,
  use: {
    baseURL: process.env.CIVICS_BASE_URL || 'http://127.0.0.1:18844',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: process.env.CIVICS_BASE_URL ? undefined : {
    command: `CIVICS_DB=${localDb} CIVICS_BOOTSTRAP_ADMIN_TOKEN=playwright-bootstrap-secret-32-bytes .venv/bin/uvicorn civics_app.main:app --host 127.0.0.1 --port 18844`,
    url: 'http://127.0.0.1:18844/api/health',
    reuseExistingServer: false,
  },
});
