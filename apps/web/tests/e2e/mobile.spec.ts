import path from "node:path";
import { expect, test } from "@playwright/test";

const API = "http://localhost:8000/api/v1";
const PAGES = ["/app/overview", "/app/inventory", "/app/forecasts", "/app/pos", "/app/settings", "/app/data"];

test("app pages have no horizontal overflow at 375px (after sign-in)", async ({ page, context }) => {
  test.setTimeout(120_000);
  const r = await context.request.post(`${API}/auth/signup`, {
    data: { email: `mobile-${Date.now()}@example.com`, password: "correct-horse-battery", name: "Mobile Tester", org_name: "Mobile Org" },
  });
  expect(r.status()).toBe(201);
  await page.setViewportSize({ width: 375, height: 812 });
  for (const url of PAGES) {
    await page.goto(url);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    const offenders = overflow > 0
      ? await page.evaluate(() =>
          [...document.querySelectorAll("body *")]
            .filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1)
            .slice(0, 6)
            .map((e) => `${e.tagName.toLowerCase()}.${String(e.className).slice(0, 60)} right=${Math.round(e.getBoundingClientRect().right)}`))
      : [];
    expect(overflow, `${url} horizontal overflow (px); offenders: ${offenders.join(" | ")}`).toBeLessThanOrEqual(0);
  }
  await page.goto("/app/overview");
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: path.resolve(__dirname, "../../../../artifacts/screenshots/overview-375.png"), fullPage: true });
});

test("desktop shows exactly one Sign out button, and it signs out", async ({ page, context }) => {
  const r = await context.request.post(`${API}/auth/signup`, {
    data: { email: `desk-${Date.now()}@example.com`, password: "correct-horse-battery", name: "Desk Tester", org_name: "Desk Org" },
  });
  expect(r.status()).toBe(201);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/app/overview");
  const btn = page.getByRole("button", { name: "Sign out" });
  await expect(btn).toHaveCount(1); // the mobile-only button must be hidden on desktop
  await btn.click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/app/overview");
  await expect(page).toHaveURL(/\/login/); // guarded
});
