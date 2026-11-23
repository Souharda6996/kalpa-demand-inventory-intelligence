import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

const API = "http://localhost:8000/api/v1";
const PUBLIC = ["/", "/login", "/signup", "/status"];
const APP = ["/app/overview", "/app/inventory", "/app/forecasts", "/app/pos", "/app/data", "/app/settings"];

async function scan(page: Page, url: string, theme: "light" | "dark") {
  await page.goto(url);
  await page.waitForLoadState("networkidle");
  await page.evaluate((t) => (document.documentElement.dataset.theme = t), theme);
  const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  return res.violations.map((v) => `${v.id} (${v.impact}) x${v.nodes.length}: ${v.help} | e.g. ${v.nodes[0]?.target.join(" ")}`);
}

test.describe("accessibility (axe, WCAG 2.0/2.1 A + AA)", () => {
  test.setTimeout(300_000);

  for (const theme of ["light", "dark"] as const) {
    test(`public pages, ${theme}`, async ({ page }) => {
      const problems: string[] = [];
      for (const url of PUBLIC) for (const v of await scan(page, url, theme)) problems.push(`${url}: ${v}`);
      expect(problems, problems.join("\n")).toEqual([]);
    });
  }

  test("signed-in pages, light and dark", async ({ page, context }) => {
    const email = `a11y-${Date.now()}@example.com`;
    const r = await context.request.post(`${API}/auth/signup`, {
      data: { email, password: "correct-horse-battery", name: "A11y", org_name: "A11y Org" },
    });
    expect(r.status()).toBe(201);
    const csrf = (await context.cookies()).find((c) => c.name === "kalpa_csrf")?.value ?? "";
    const demo = await context.request.post(`${API}/data/demo`, { headers: { "X-CSRF-Token": csrf }, timeout: 120_000 });
    expect(demo.status()).toBe(200);
    const problems: string[] = [];
    for (const theme of ["light", "dark"] as const) {
      for (const url of APP) for (const v of await scan(page, url, theme)) problems.push(`[${theme}] ${url}: ${v}`);
    }
    expect(problems, problems.join("\n")).toEqual([]);
  });
});
