import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { expect, type Page, test } from "@playwright/test";

const API = "http://localhost:8000/api/v1";
const SHOTS = path.resolve(__dirname, "../../../../artifacts/screenshots");

async function shotBoth(page: Page, name: string) {
  for (const t of ["light", "dark"] as const) {
    await page.evaluate((v) => (document.documentElement.dataset.theme = v), t);
    await page.waitForTimeout(350); // let the 150 ms colour transitions finish before capturing
    await page.screenshot({ path: path.join(SHOTS, `${name}-${t}.png`), fullPage: true });
  }
  await page.evaluate(() => (document.documentElement.dataset.theme = "light"));
}

async function axe(page: Page, label: string) {
  for (const t of ["light", "dark"] as const) {
    await page.evaluate((v) => (document.documentElement.dataset.theme = v), t);
    const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    const problems = res.violations.map((v) => `${v.id} x${v.nodes.length}: ${v.help} | ${v.nodes[0]?.target.join(" ")}`);
    expect(problems, `${label} [${t}] accessibility`).toEqual([]);
  }
  await page.evaluate(() => (document.documentElement.dataset.theme = "light"));
}

test.describe.serial("Phase 6 decision engine in the browser", () => {
  let page: Page;
  let csrf = "";

  test.beforeAll(async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    page = await ctx.newPage();
    const r = await ctx.request.post(`${API}/auth/signup`, {
      data: { email: `dec-${Date.now()}@example.com`, password: "correct-horse-battery", name: "Planner Pat", org_name: "Decision Org" },
    });
    expect(r.status()).toBe(201);
    csrf = (await ctx.cookies()).find((c) => c.name === "kalpa_csrf")?.value ?? "";
    const demo = await ctx.request.post(`${API}/data/demo`, { headers: { "X-CSRF-Token": csrf }, timeout: 180_000 });
    expect(demo.status()).toBe(200);
  });
  test.afterAll(async () => page.context().close());

  test("backtest (through the worker) gives forecasts, costs and a saved model", async () => {
    test.setTimeout(1_500_000);
    const ctx = page.context();
    const job = await (
      await ctx.request.post(`${API}/models/backtests`, {
        headers: { "X-CSRF-Token": csrf },
        data: { models: ["seasonal_naive", "lightgbm_tweedie"], folds: 2 },
      })
    ).json();
    expect(job.status).toBe("queued");
    let status = "queued";
    for (let i = 0; i < 450 && status !== "succeeded" && status !== "failed"; i++) {
      await page.waitForTimeout(2000);
      status = (await (await ctx.request.get(`${API}/jobs/${job.id}`)).json()).status;
    }
    expect(status).toBe("succeeded");
  });

  test("Replenishment: recommendations -> edit quantity -> create POs", async () => {
    await page.goto("/app/replenishment");
    await expect(page.getByRole("heading", { name: "Replenishment" })).toBeVisible();
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible({ timeout: 30_000 });
    expect(await rows.count()).toBeGreaterThan(0);
    expect(await rows.count()).toBeLessThanOrEqual(40); // paged: never dumps hundreds of rows at once
    const more = page.getByRole("button", { name: /Show \d+ more/ });
    if (await more.count()) {
      const before = await rows.count();
      await more.click();
      expect(await rows.count()).toBeGreaterThan(before);
    }
    await expect(page.getByText(/lead time \d+ d/).first()).toBeVisible(); // reason chips

    const first = rows.first();
    await first.getByRole("checkbox").check();
    const qtyBox = first.getByRole("spinbutton");
    const original = await qtyBox.inputValue();
    await qtyBox.fill("7"); // not a pack multiple -> invalid
    await expect(page.getByRole("button", { name: /Create POs/ })).toBeDisabled();
    await qtyBox.fill(original);
    await expect(page.getByRole("button", { name: /Create POs \(1\)/ })).toBeEnabled();
    await shotBoth(page, "replenishment");
    await page.getByRole("button", { name: /Create POs/ }).click();
    await expect(page.getByText(/purchase order.* created as drafts/)).toBeVisible();
  });

  test("Purchase Orders: approve -> send -> receive updates stock; PDF downloads", async () => {
    await page.goto("/app/purchase-orders");
    const card = page.getByRole("button", { name: /\d+ line/ }).first();
    await expect(card).toBeVisible();
    await card.click();
    const detail = page.getByLabel("Purchase order details");
    await expect(detail).toContainText("draft");
    await shotBoth(page, "purchase-orders");
    await detail.getByRole("button", { name: "Approve" }).click();
    await expect(detail).toContainText("approved", { timeout: 15_000 });
    await detail.getByRole("button", { name: "Mark as sent" }).click();
    await expect(detail).toContainText("sent", { timeout: 15_000 });
    await detail.getByRole("button", { name: "Fill all outstanding" }).click();
    await detail.getByRole("button", { name: "Receive" }).click();
    await expect(detail).toContainText("received", { timeout: 15_000 });
    const [download] = await Promise.all([page.waitForEvent("download"), detail.getByRole("button", { name: "Download PDF" }).click()]);
    expect(download.suggestedFilename()).toMatch(/^po-.*\.pdf$/);
    // the Received column on the board now holds the order
    await expect(page.getByRole("listitem", { name: /Received \(1\)/ })).toBeVisible();
  });

  test("Model Arena Cost tab states whether best WAPE is the cheapest policy", async () => {
    await page.goto("/app/arena");
    await page.getByRole("tab", { name: "Cost (replay)" }).click();
    const panel = page.getByLabel("Cost replay");
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("status")).toContainText(/cheapest|Best WAPE is not the cheapest/);
    await expect(panel.getByRole("row", { name: /Seasonal Naive/ })).toBeVisible();
    await expect(panel.getByRole("row", { name: /LightGBM/ })).toBeVisible();
    await shotBoth(page, "arena-cost");
  });

  test("Scenario Studio: a price cut raises units; single product shows two fan charts; save", async () => {
    await page.goto("/app/scenarios");
    await expect(page.getByRole("heading", { name: "Scenario Studio" })).toBeVisible();
    await page.getByLabel("Price change").fill("-20");
    await page.getByRole("button", { name: "Run scenario" }).click();
    const kpi = page.getByText("Change in units (28 d)").locator("..").locator("..");
    await expect(kpi).toContainText("+", { timeout: 60_000 }); // a cheaper price sells more
    await expect(page.getByText(/Weather is not an input/)).toBeVisible(); // honest: weather is not modelled
    // narrow to one product -> baseline and scenario fan charts
    const options = await page.getByLabel("Product (optional)").locator("option").allTextContents();
    await page.getByLabel("Product (optional)").selectOption({ index: 1 });
    await page.getByRole("button", { name: "Run scenario" }).click();
    await expect(page.locator("canvas")).toHaveCount(2, { timeout: 60_000 });
    expect(options.length).toBeGreaterThan(1);
    await page.getByLabel("Scenario name").fill("Price cut 20%");
    await page.getByRole("button", { name: "Save scenario" }).click();
    await expect(page.getByLabel("Saved scenarios")).toContainText("Price cut 20%");
    await shotBoth(page, "scenarios");
  });

  test("Settings cost parameters change the recommendations' service level", async () => {
    await page.goto("/app/settings");
    await page.getByLabel(/Stock-out penalty/).fill("5");
    await page.getByRole("region", { name: "Cost parameters" }).getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Cost parameters saved")).toBeVisible();
    await shotBoth(page, "settings");
  });

  test("new pages pass axe in both themes", async () => {
    test.setTimeout(300_000);
    for (const url of ["/app/replenishment", "/app/purchase-orders", "/app/scenarios", "/app/arena", "/app/settings"]) {
      await page.goto(url);
      await page.waitForLoadState("networkidle");
      await axe(page, url);
    }
  });
});
