import path from "node:path";
import { expect, test } from "@playwright/test";

test("landing hero shows the live demo fan chart from the public endpoint", async ({ page }) => {
  await page.goto("/");
  const fig = page.getByLabel("Live demo forecast");
  await expect(fig).toBeVisible({ timeout: 20_000 });
  await expect(fig).toContainText("Kaju Katli");
  await expect(fig).toContainText("Synthetic demo data");
  await expect(fig.locator("canvas")).toBeVisible();
  for (const t of ["light", "dark"] as const) {
    await page.evaluate((v) => (document.documentElement.dataset.theme = v), t);
    await page.screenshot({ path: path.resolve(__dirname, `../../../../artifacts/screenshots/landing-hero-${t}.png`), fullPage: true });
  }
});
