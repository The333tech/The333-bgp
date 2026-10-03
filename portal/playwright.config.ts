import { defineConfig } from "playwright/test";

const testPort = Number(process.env.THE333_PLAYWRIGHT_PORT ?? "5178");

export default defineConfig({
  testDir: "./tests",
  timeout: 45000,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${testPort}`,
    channel: process.platform === "win32" ? "msedge" : undefined,
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${testPort} --strictPort`,
    url: `http://127.0.0.1:${testPort}`,
    reuseExistingServer: false,
  },
});
