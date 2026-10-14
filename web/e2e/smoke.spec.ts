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
