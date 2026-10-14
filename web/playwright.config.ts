import { defineConfig } from "@playwright/test";

const API = "http://127.0.0.1:8765";
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: { baseURL: "http://127.0.0.1:4173", viewport: { width: 390, height: 844 } },  // a phone-sized viewport
  webServer: [
    { command: "cd .. && uv run python scripts/test_api_server.py", url: `${API}/api/regions`, reuseExistingServer: false, timeout: 60_000 },
    { command: `BACKCOUNTRY_API=${API} npm run build && BACKCOUNTRY_API=${API} npm run preview -- --port 4173`, url: "http://127.0.0.1:4173", reuseExistingServer: false, timeout: 120_000 },
  ],
});
