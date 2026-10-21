import { expect, test } from "@playwright/test";

test("a chat proposal shows what changes and runs only when tapped", async ({ page }) => {
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
  const firstRun = await page.evaluate(() => (window as unknown as { backcountry: { store: { get(): { run: { run_id: string } } } } }).backcountry.store.get().run.run_id);

  await page.fill("#chat-input", "make day 1 shorter");
  await page.click("#chat-send");
  const card = page.locator(".proposal");
  await expect(card).toContainText("Miles per day: 0.5-4 mi → 0.5-3 mi");
  await expect(page.locator("#chat-log")).toContainText("This lowers the most miles allowed in a day.");
  // Nothing ran yet.
  expect(await page.evaluate(() => (window as unknown as { backcountry: { store: { get(): { run: { run_id: string } } } } }).backcountry.store.get().run.run_id)).toBe(firstRun);

  await card.locator("[data-run]").click();
  await expect(card).toContainText("Ran these settings", { timeout: 30_000 });
  await expect.poll(() => page.evaluate(() => (window as unknown as { backcountry: { store: { get(): { run: { run_id: string; request: { daily: number[] } } } } } }).backcountry.store.get().run.request.daily)).toEqual([0.5, 3]);
  await expect(page.locator(".card").first()).toBeVisible();
});
