// @ts-check
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './e2e',
  timeout: 30000,
  retries: 0,
  use: {
    baseURL: process.env.CIVICS_BASE_URL || 'https://civics.multihost.ing',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
});
