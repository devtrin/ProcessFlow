import { defineConfig } from "@playwright/test";

// The e2e tests start (and restart) the real FastAPI backend themselves, serving
// the built frontend from frontend/dist. Run `npm run build` first.
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  workers: 1,
  fullyParallel: false,
  reporter: [["list"]],
  use: { trace: "off", screenshot: "off", viewport: { width: 1366, height: 768 } },
  outputDir: "./e2e/.results",
});
