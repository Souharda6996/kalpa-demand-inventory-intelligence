import path from "node:path";
import { expect, test } from "@playwright/test";

test("status page shows 4 green pills (API, DB, Redis, Worker)", async ({ page }) => {
  await page.goto("/status");
  for (const name of ["API", "Database", "Redis", "Worker"]) {
    const pill = page.getByText(name, { exact: true });
    await expect(pill).toBeVisible();
    await expect(pill).toHaveClass(/text-success/); // red pills use text-danger
  }
  await page.screenshot({ path: path.resolve(__dirname, "../../../../artifacts/screenshots/status-green.png") });
});
