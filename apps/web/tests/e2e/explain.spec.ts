import AxeBuilder from "@axe-core/playwright";
import path from "node:path";
import { expect, type Page, test } from "@playwright/test";

const API = "http://localhost:8000/api/v1";
const SHOTS = path.resolve(__dirname, "../../../../artifacts/screenshots");

async function shotBoth(page: Page, name: string) {
  for (const t of ["light", "dark"] as const) {
    await page.evaluate((v) => (document.documentElement.dataset.theme = v), t);
    await page.waitForTimeout(350);
    await page.screenshot({
      path: path.join(SHOTS, `${name}-${t}.png`),
      fullPage: true,
    });
  }
  await page.evaluate(() => (document.documentElement.dataset.theme = "light"));
}

async function axe(page: Page, label: string) {
  for (const t of ["light", "dark"] as const) {
    await page.evaluate((v) => (document.documentElement.dataset.theme = v), t);
    await page.waitForTimeout(350); // colours transition for 150 ms; audit the settled page
    const res = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const problems = res.violations.map(
      (v) =>
        `${v.id} x${v.nodes.length}: ${v.help} | ${v.nodes[0]?.target.join(" ")}`,
    );
    expect(problems, `${label} [${t}] accessibility`).toEqual([]);
  }
  await page.evaluate(() => (document.documentElement.dataset.theme = "light"));
}

test.describe.serial("Phase 7 explainability + copilot in the browser", () => {
  let page: Page;
  let csrf = "";

  test.beforeAll(async ({ browser }) => {
    const ctx = await browser.newContext({
      viewport: { width: 1280, height: 900 },
    });
    page = await ctx.newPage();
    const r = await ctx.request.post(`${API}/auth/signup`, {
      data: {
        email: `exp-${Date.now()}@example.com`,
        password: "correct-horse-battery",
        name: "Planner Pat",
        org_name: "Explain Org",
      },
    });
    expect(r.status()).toBe(201);
    csrf =
      (await ctx.cookies()).find((c) => c.name === "kalpa_csrf")?.value ?? "";
    const demo = await ctx.request.post(`${API}/data/demo`, {
      headers: { "X-CSRF-Token": csrf },
      timeout: 180_000,
    });
    expect(demo.status()).toBe(200);
  });
  test.afterAll(async () => page.context().close());

  test("TFT + LightGBM + Seasonal Naive backtest runs through the worker", async () => {
    test.setTimeout(2_400_000);
    const ctx = page.context();
    const job = await (
      await ctx.request.post(`${API}/models/backtests`, {
        headers: { "X-CSRF-Token": csrf },
        data: {
          models: ["seasonal_naive", "lightgbm_tweedie", "tft"],
          folds: 2,
        },
      })
    ).json();
    expect(job.status).toBe("queued");
    let status = "queued";
    for (
      let i = 0;
      i < 1100 && status !== "succeeded" && status !== "failed";
      i++
    ) {
      await page.waitForTimeout(2000);
      status = (await (await ctx.request.get(`${API}/jobs/${job.id}`)).json())
        .status;
    }
    expect(status).toBe("succeeded");
  });

  test("Explain panel: narrative, TFT weights + attention, SHAP, and a clear message for models without attributions", async () => {
    test.setTimeout(240_000);
    const narrativeResponse = page.waitForResponse(
      (r) => r.url().includes("/narrative") && r.status() === 200,
    );
    await page.goto("/app/forecasts");
    const nr = await (await narrativeResponse).json();
    const panel = page.getByRole("region", { name: "Explain this forecast" });
    await expect(panel).toBeVisible();
    const shown = (await panel.getByTestId("narrative").innerText()).trim();
    expect(shown).toBe(nr.text); // the screen shows exactly what the API produced
    expect(shown).toMatch(/^Demand for .+ is expected to /);
    const numbers = (
      shown.match(/(?<![A-Za-z0-9_])\d[\d,]*(?:\.\d+)?/g) ?? []
    ).map((n) => Number(n.replaceAll(",", "")));
    const facts = JSON.stringify({
      ...nr.facts,
      run_id: undefined,
      location_id: undefined,
      product_id: undefined,
    });
    const allowed = new Set((facts.match(/\d+(?:\.\d+)?/g) ?? []).map(Number));
    for (const n of numbers)
      expect(
        allowed.has(n),
        `number ${n} in "${shown}" must come from the facts`,
      ).toBe(true);

    await page
      .getByLabel("Model", { exact: true })
      .selectOption({ label: "Temporal Fusion Transformer" });
    await expect(
      panel.getByRole("heading", { name: "Static (who)" }),
    ).toBeVisible();
    await expect(panel.getByRole("heading", { name: /^Past/ })).toBeVisible();
    await expect(panel.getByRole("heading", { name: /^Future/ })).toBeVisible();
    await expect(
      panel.getByRole("heading", {
        name: "Attention over the lookback window",
      }),
    ).toBeVisible();
    await expect(
      panel.getByRole("img", { name: /Attention over the last 56 days/ }),
    ).toBeVisible();
    for (const label of ["store", "sku", "category"])
      await expect(
        panel.getByText(label, { exact: true }).first(),
      ).toBeVisible();
    await panel.getByText("View as table").click();
    expect(await panel.locator("details table tbody tr").count()).toBe(56);
    await shotBoth(page, "explain-tft");
    await axe(page, "Forecast Explorer + Explain (TFT)");

    await page
      .getByLabel("Model", { exact: true })
      .selectOption({ label: "LightGBM (Tweedie)" });
    await expect(panel.getByRole("heading", { name: /^SHAP/ })).toBeVisible();
    await expect(panel.getByText("log-demand").first()).toBeVisible();
    await shotBoth(page, "explain-shap");

    await page
      .getByLabel("Model", { exact: true })
      .selectOption({ label: "Seasonal Naive" });
    await expect(panel.getByText(/has no feature attributions/)).toBeVisible();
  });

  test("Copilot drawer: answers cite sources, refuses off-topic questions, keyboard accessible", async () => {
    await page.goto("/app/overview");
    const open = page.getByRole("button", { name: "Copilot" });
    await open.click();
    const dlg = page.getByRole("dialog", { name: "Copilot" });
    await expect(dlg).toBeVisible();
    await expect(dlg.getByText(/rule-based mode|AI mode/)).toBeVisible();
    await expect(dlg.getByLabel("Ask the copilot")).toBeFocused();

    await dlg
      .getByRole("button", { name: "Which model is the most accurate?" })
      .click();
    await expect(dlg.getByText(/has the lowest WAPE/)).toBeVisible();
    await expect(dlg.getByText(/source: latest backtest run/)).toBeVisible();

    await dlg.getByLabel("Ask the copilot").fill("What should I reorder?");
    await dlg.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(dlg.getByText(/order lines are recommended/)).toBeVisible();

    await dlg
      .getByLabel("Ask the copilot")
      .fill("What is the capital of France?");
    await dlg.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(
      dlg.getByText(/I can only answer questions about this organisation/),
    ).toBeVisible();
    await expect(dlg.getByText("out of scope")).toBeVisible();

    await shotBoth(page, "copilot-drawer");
    await axe(page, "Copilot drawer");
    await page.keyboard.press("Escape");
    await expect(dlg).toBeHidden();
    await expect(open).toBeFocused(); // focus returns to the button that opened it
  });

  test("the app works with the copilot never opened (no key, no drawer)", async () => {
    for (const p of [
      "/app/overview",
      "/app/inventory",
      "/app/replenishment",
      "/app/arena",
    ]) {
      await page.goto(p);
      await expect(
        page.getByRole("heading", { level: 1 }).first(),
      ).toBeVisible();
    }
  });
});
