import { expect, test } from "@playwright/test";

test("plan, compare, open a trip, download its GPX", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#map canvas")).toBeVisible();
  const trailhead = page.locator('select[name="trailhead"]');
  await expect(trailhead.locator("optgroup[label='Other start points from the data'] option")).not.toHaveCount(0);
  const first = await trailhead.locator("optgroup[label='Other start points from the data'] option").first().getAttribute("value");
  await trailhead.selectOption(first!);
  await page.fill('input[name="total"]', "2.4-5.1");
  await page.fill('input[name="nights"]', "1");
  await page.fill('input[name="daily"]', "0.5-4");
  await page.fill('input[name="maxGainDay"]', "");
  await page.selectOption('select[name="type"]', "loop");
  await page.click("#generate");
  await expect(page.locator(".card")).toHaveCount(3, { timeout: 30_000 });
  await expect(page.locator(".status.error")).toHaveCount(0);  // the compare view replaced the plan panel; no error anywhere
  await page.locator(".card").nth(1).click();
  await expect(page.locator(".card.selected")).toContainText("Candidate 2");
  await page.click("#open-trip");
  await expect(page.locator("h1")).toContainText("Candidate 2");
  await expect(page.locator("#known-gaps")).toContainText("Not checked by this tool");
  await expect(page.locator("table")).toContainText("Day 1");
  const [download] = await Promise.all([page.waitForEvent("download"), page.click('a[href$="/candidates/2.gpx"]')]);
  expect(download.suggestedFilename()).toMatch(/candidate_2\.gpx$/);
  const text = (await (await download.createReadStream()).toArray()).join("");  // chunks stringify as UTF-8
  expect(text).toContain("<trk>");
});

test("a bad range is refused with the field named, and the map stays", async ({ page }) => {
  await page.goto("/");
  const options = page.locator("select[name='trailhead'] optgroup[label='Other start points from the data'] option");
  await expect(options).not.toHaveCount(0);
  await page.selectOption('select[name="trailhead"]', (await options.first().getAttribute("value"))!);
  await page.fill('input[name="total"]', "26-18");
  await page.click("#generate");
  await expect(page.locator("#status")).toContainText("total must look like 18-26");
  await expect(page.locator("#map canvas")).toBeVisible();
});

async function pickDataStart(page: import("@playwright/test").Page) {
  const options = page.locator("select[name='trailhead'] optgroup[label='Other start points from the data'] option");
  await expect(options).not.toHaveCount(0);
  await page.selectOption('select[name="trailhead"]', (await options.first().getAttribute("value"))!);
}

test("every view opens scrolled to its top", async ({ page }) => {
  await page.goto("/");
  await pickDataStart(page);
  await page.fill('input[name="total"]', "2.4-5.1");
  await page.fill('input[name="nights"]', "1");
  await page.fill('input[name="daily"]', "0.5-4");
  await page.fill('input[name="maxGainDay"]', "");
  await page.click("#generate");
  await expect(page.locator(".card")).toHaveCount(3, { timeout: 30_000 });
  await page.click("#open-trip");
  await expect(page.locator("h1")).toContainText("Candidate 1");
  await page.locator("#panel").evaluate((el) => { el.scrollTop = el.scrollHeight; });  // thumb scrolled to the bottom buttons
  expect(await page.locator("#panel").evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
  await page.click("#replan");
  await expect(page.locator("#generate")).toBeVisible();
  expect(await page.locator("#panel").evaluate((el) => el.scrollTop)).toBe(0);
});

test("attribution and the base-map switcher are reachable on a phone", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#map canvas")).toBeVisible();
  for (const sel of [".maplibregl-ctrl-attrib", "#basemap"]) {
    const box = (await page.locator(sel).boundingBox())!;
    const hit = await page.evaluate(([x, y, s]) => document.elementFromPoint(x as number, y as number)?.closest(s as string) != null,
      [box.x + box.width / 2, box.y + box.height / 2, sel]);
    expect(hit, `${sel} is covered`).toBe(true);
  }
});

test("attribution and the base-map switcher are reachable beside the docked panel", async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 });
  await page.goto("/");
  await expect(page.locator("#map canvas")).toBeVisible();
  for (const sel of [".maplibregl-ctrl-attrib", "#basemap"]) {
    const box = (await page.locator(sel).boundingBox())!;
    const hit = await page.evaluate(([x, y, s]) => document.elementFromPoint(x as number, y as number)?.closest(s as string) != null,
      [box.x + box.width / 2, box.y + box.height / 2, sel]);
    expect(hit, `${sel} is covered`).toBe(true);
  }
});

test("data-derived start points are drawn as a map layer, not one DOM marker each", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("select[name='trailhead'] optgroup[label='Other start points from the data'] option")).not.toHaveCount(0);
  await expect(page.locator("#map canvas")).toBeVisible();
  await page.waitForTimeout(1500);  // markers are added once the map has loaded
  await expect(page.locator(".marker")).toHaveCount(0);  // the grid's yaml trailheads are off the network, so no badges at all
});
