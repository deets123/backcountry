import { expect, test } from "@playwright/test";

// A 1x1 PNG (e2e/tile.png) stands in for every USGS tile: the test never contacts basemap.nationalmap.gov.
const TILES = /basemap\.nationalmap\.gov/;

test("save a trip, go offline, reopen it, see where I am, move a camp, delete it", async ({ page, context }) => {
  await context.route(TILES, (route) => route.fulfill({ status: 200, contentType: "image/png", path: "e2e/tile.png", headers: { "access-control-allow-origin": "*" } }));
  await context.grantPermissions(["geolocation"]);
  page.on("dialog", (d) => d.accept());

  // Plan and open a 1-night trip on the synthetic grid.
  await page.goto("/");
  const options = page.locator("select[name='trailhead'] optgroup[label='Other start points from the data'] option");
  await expect(options).not.toHaveCount(0);
  await page.selectOption('select[name="trailhead"]', (await options.first().getAttribute("value"))!);
  await page.fill('input[name="total"]', "2.4-5.1");
  await page.fill('input[name="nights"]', "1");
  await page.fill('input[name="daily"]', "0.5-4");
  await page.fill('input[name="maxGainDay"]', "");
  await page.click("#generate");
  await expect(page.locator(".card")).toHaveCount(3, { timeout: 30_000 });
  await page.click("#open-trip");

  // Save it.
  await expect(page.locator("#save-offline")).toContainText(/about \d+ map tiles/);
  await page.click("#save-offline");
  await expect(page.locator("#saved-line")).toContainText("Saved for offline", { timeout: 60_000 });
  await expect(page.locator("#incomplete")).toHaveCount(0);
  await page.waitForFunction(() => navigator.serviceWorker?.controller != null);
  const tileUrl = await page.evaluate(async () => {
    const name = (await caches.keys()).find((n) => n.startsWith("trip-"))!;
    return (await (await caches.open(name)).keys())[0]!.url;
  });

  // Offline: the app opens from the service worker on Saved trips, and the saved tiles come from the cache.
  await context.unroute(TILES);
  await context.setOffline(true);
  await page.reload();
  await expect(page.locator("#offline-note")).toContainText("Offline: showing saved trips");
  expect(await page.evaluate(async (u) => (await fetch(u)).ok, tileUrl)).toBe(true);
  await page.click("[data-open]");
  await expect(page.locator("h1")).toContainText("Candidate 1");
  await expect(page.locator("#known-gaps")).toBeVisible();

  // GPS: a fix on day 1 of the route.
  const [lon, lat] = await page.evaluate(() => {
    const s = (window as unknown as { backcountry: { store: { get(): { run: { candidates: { pieces: { points: number[][] }[] }[] }; selected: number } } } }).backcountry.store.get();
    const p = s.run.candidates[s.selected - 1]!.pieces[0]!.points;
    return [(p[0]![0]! + p[1]![0]!) / 2, (p[0]![1]! + p[1]![1]!) / 2];
  });
  await context.setGeolocation({ longitude: lon!, latitude: lat!, accuracy: 10 });
  await page.click("#locate");
  await expect(page.locator("#gps-line")).toContainText(/mile \d+\.\d of \d+\.\d · Day 1 · .* to Camp 1/);

  // Move tonight's camp to here; the days table and the saved record follow.
  const before = await page.locator("table").first().innerText();
  await page.click("#camp-here");
  await expect(page.locator("#reset-camps")).toBeVisible();
  expect(await page.locator("table").first().innerText()).not.toBe(before);
  await expect(page.locator(".note").filter({ hasText: "Camps moved by you on this phone" })).toHaveCount(1);
  await page.reload();
  await expect(page.locator(".saved-trip")).toContainText("camps moved by you");

  // Delete it: record and tiles are gone.
  await page.click("[data-delete]");
  await expect(page.locator("#panel")).toContainText("No saved trips yet");
  expect(await page.evaluate(async () => (await caches.keys()).filter((n) => n.startsWith("trip-")).length)).toBe(0);
});

test("a slow save stays with its own trip, Cancel works mid-save, and the offline map notes are honest", async ({ page, context }) => {
  // Slow stub tiles so the save takes a few seconds.
  await context.route(TILES, async (route) => {
    await new Promise((r) => setTimeout(r, 40));
    await route.fulfill({ status: 200, contentType: "image/png", path: "e2e/tile.png", headers: { "access-control-allow-origin": "*" } });
  });
  page.on("dialog", (d) => d.accept());
  await page.goto("/");
  const options = page.locator("select[name='trailhead'] optgroup[label='Other start points from the data'] option");
  await expect(options).not.toHaveCount(0);
  await page.selectOption('select[name="trailhead"]', (await options.first().getAttribute("value"))!);
  await page.fill('input[name="total"]', "2.4-5.1");
  await page.fill('input[name="nights"]', "1");
  await page.fill('input[name="daily"]', "0.5-4");
  await page.fill('input[name="maxGainDay"]', "");
  await page.click("#generate");
  await expect(page.locator(".card")).toHaveCount(3, { timeout: 30_000 });

  // Cancel mid-save: the tap lands (the button isn't rebuilt under it) and nothing is kept.
  await page.click("#open-trip");
  await page.click("#save-offline");
  await expect(page.locator("#save-progress")).toContainText(/Saving map tiles: [1-9]/);
  await page.click("#save-cancel");
  await expect(page.locator("#status")).toContainText("Save cancelled; nothing was kept.");
  await expect.poll(() => page.evaluate(async () => (await caches.keys()).filter((n) => n.startsWith("trip-")).length)).toBe(0);

  // Start saving candidate 1, then open candidate 2 while it runs: candidate 2 is not marked saved.
  await page.click("#save-offline");
  await expect(page.locator("#save-progress")).toBeVisible();
  await page.click("#back-compare");
  await page.locator(".card").nth(1).click();
  await page.click("#open-trip");
  await expect(page.locator("h1")).toContainText("Candidate 2");
  await expect(page.locator("#panel")).toContainText("Another trip is being saved");
  await expect(page.locator("#status")).toContainText("for offline.", { timeout: 60_000 });
  await expect(page.locator("#saved-line")).toHaveCount(0);
  await expect(page.locator("#save-offline")).toContainText("Save for offline");

  // Offline: the saved trip opens without a false "not saved" note; satellite says it isn't saved.
  await page.waitForFunction(() => navigator.serviceWorker?.controller != null);
  await context.setOffline(true);
  await page.reload();
  await page.click("[data-open]");
  await expect(page.locator("h1")).toContainText("Candidate 1");
  // Inside the saved area there is no note. (On a tall phone screen, the first fit to the trip's width also
  // shows land above and below the saved box; a note there is true, so only the inside view is asserted.)
  await page.evaluate(() => {
    const app = (window as unknown as { backcountry: { store: { get(): { saved: { box: number[] }[] } }; map: { fitBounds(b: number[]): void } } }).backcountry;
    const [w, s, e, n] = app.store.get().saved[0]!.box as [number, number, number, number];
    const cx = (w + e) / 2, cy = (s + n) / 2, dx = (e - w) / 6, dy = (n - s) / 6;
    app.map.fitBounds([cx - dx, cy - dy, cx + dx, cy + dy]);
  });
  await page.waitForTimeout(500);
  await expect(page.locator("#map-note")).toHaveCount(0);
  await page.selectOption("#basemap", "satellite");
  await expect(page.locator("#map-note")).toContainText("Satellite imagery isn't saved for offline use");
});
