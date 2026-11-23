import { defineConfig } from "@playwright/test";

// Drives the locally installed Google Chrome (no browser download). Services must already be running:
// API on :8000 (Postgres + Redis) and `next start` on :3000 - see docs/PROGRESS.md.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    channel: "chrome",
    headless: true,
    trace: "retain-on-failure",
  },
});
