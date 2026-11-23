import AxeBuilder from "@axe-core/playwright";
import { execSync, spawn } from "node:child_process";
import path from "node:path";
import { expect, type Page, test } from "@playwright/test";

const API = "http://localhost:8000/api/v1";
const ROOT = path.resolve(__dirname, "../../../..");
const SHOTS = path.join(ROOT, "artifacts/screenshots");

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

/** Wait for finite CSS animations/transitions (a toast fading in changes the blended colour axe measures). */
async function settle(page: Page) {
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => undefined)),
    ),
  );
}

async function axe(page: Page, label: string) {
  for (const t of ["light", "dark"] as const) {
    await page.evaluate((v) => (document.documentElement.dataset.theme = v), t);
    await settle(page);
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

// only the rupee amount: the tile label also contains a date, whose digits must not be mistaken for revenue
const rupees = (text: string) =>
  Number((/₹\s?([\d,]+)/.exec(text)?.[1] ?? "0").replace(/,/g, ""));

// the pattern kalpa_a[p]i.main:app matches the uv wrapper AND the real server process; the [p] makes the PowerShell process running it does not match (and kill) itself
function killApi() {
  execSync(
    `powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'kalpa_a[p]i.main:app' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"`,
  );
}
function startApi() {
  spawn(
    "bash",
    [
      "-c",
      `cd "${ROOT}" && source data/run/env.sh && exec uv run uvicorn kalpa_api.main:app --port 8000 --app-dir services/api > data/run/api.log 2>&1`,
    ],
    {
      detached: true,
      stdio: "ignore",
      windowsHide: true,
    },
  ).unref();
}

test.describe
  .serial("Phase 8 real-time operations and MLOps in the browser", () => {
  let page: Page;
  let csrf = "";
  let versionsBefore = 0;

  // uvicorn closes idle keep-alive sockets after 5 s; a poll with a 5 s interval can reuse one just as it closes
  // (ECONNRESET). That is a harness race, not an API failure, so only connection resets are retried.
  const retry = async <T>(fn: () => Promise<T>): Promise<T> => {
    for (let i = 0; ; i++) {
      try {
        return await fn();
      } catch (e) {
        if (i >= 3 || !/ECONNRESET|socket hang up/.test(String(e))) throw e;
        await new Promise((r) => setTimeout(r, 500));
      }
    }
  };
  const get = async (url: string) =>
    retry(async () =>
      (await page.context().request.get(`${API}${url}`)).json(),
    );
  const post = async (url: string, data?: unknown) =>
    retry(() =>
      page.context().request.post(`${API}${url}`, {
        headers: { "X-CSRF-Token": csrf },
        data,
      }),
    );

  test.beforeAll(async ({ browser }) => {
    const ctx = await browser.newContext({
      viewport: { width: 1280, height: 900 },
    });
    page = await ctx.newPage();
    const r = await ctx.request.post(`${API}/auth/signup`, {
      data: {
        email: `rt-${Date.now()}@example.com`,
        password: "correct-horse-battery",
        name: "Planner Pat",
        org_name: "Realtime Org",
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

  test("backtest through the worker sets reorder points and reports progress", async () => {
    test.setTimeout(1_200_000);
    const job = await (
      await post("/models/backtests", {
        models: ["seasonal_naive", "lightgbm_tweedie"],
        folds: 2,
      })
    ).json();
    expect(job.status).toBe("queued");
    let status = "queued";
    for (
      let i = 0;
      i < 500 && status !== "succeeded" && status !== "failed";
      i++
    ) {
      await page.waitForTimeout(2000);
      status = (await get(`/jobs/${job.id}`)).status;
    }
    expect(status).toBe("succeeded");
    const detail = await get(`/jobs/${job.id}`);
    expect(detail.log).toContain("reorder points updated for 600");
    const inv = await get("/inventory?page_size=200");
    expect(
      inv.items.every(
        (i: { reorder_point: number | null }) => i.reorder_point != null,
      ),
    ).toBe(true);
    versionsBefore = (await get("/models/registry")).models.reduce(
      (n: number, m: { versions: unknown[] }) => n + m.versions.length,
      0,
    );
    expect(versionsBefore).toBe(2);
  });

  test("Command Center: simulator drives live revenue, feed, stock watch and a reorder alert with Create PO", async () => {
    test.setTimeout(420_000);
    await page.goto("/app/overview");
    await expect(page.getByText("Live", { exact: true })).toBeVisible();
    const sim = page.getByRole("region", { name: "Live store simulator" });
    await expect(sim.getByText("stopped")).toBeVisible();
    await sim
      .getByLabel("Demand")
      .selectOption({ label: "Forced drift (x2.5)" });
    await sim.getByLabel("Day length").selectOption({ label: "1 min per day" });
    await sim.getByRole("button", { name: "Start simulator" }).click();
    await expect(sim.getByText("running")).toBeVisible({ timeout: 60_000 });

    const revenue = page.getByTestId("kpi-revenue");
    await expect
      .poll(async () => rupees(await revenue.innerText()), { timeout: 60_000 })
      .toBeGreaterThan(0);
    const r1 = rupees(await revenue.innerText());
    await page.waitForTimeout(8000);
    expect(rupees(await revenue.innerText())).toBeGreaterThan(r1); // it ticks

    const feed = page.getByRole("region", { name: "Live activity" });
    await expect
      .poll(() => feed.getByRole("listitem").count(), { timeout: 30_000 })
      .toBeGreaterThanOrEqual(3);
    await expect(feed.getByText("sim").first()).toBeVisible();
    await expect
      .poll(() =>
        page
          .getByRole("region", { name: "Stock watch" })
          .getByRole("listitem")
          .count(),
      )
      .toBeGreaterThan(0);

    const toastEl = page
      .locator("[data-sonner-toast]")
      .filter({ hasText: /below its reorder point/ })
      .first();
    await expect(toastEl).toBeVisible({ timeout: 300_000 }); // stock really depleted past a reorder point
    await toastEl.getByRole("button", { name: "Create PO" }).click();
    await expect(
      page
        .locator("[data-sonner-toast]")
        .filter({ hasText: /Draft PO created/ })
        .first(),
    ).toBeVisible({ timeout: 20_000 });
    const pos = await get("/purchase-orders");
    const items = Array.isArray(pos) ? pos : (pos.items ?? []);
    expect(items.some((p: { status: string }) => p.status === "draft")).toBe(
      true,
    );

    await page.getByRole("button", { name: /Notifications/ }).click();
    await expect(
      page
        .getByRole("region", { name: "Notifications" })
        .getByRole("region", { name: "Alerts" }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await page
      .getByRole("region", { name: "Alerts" })
      .first()
      .scrollIntoViewIfNeeded();
    await shotBoth(page, "command-center-live");
    await axe(page, "Command Center (live)");
  });

  test("Jobs: the simulator job is live, a retrain shows progress and a log, and cancel stops it for good", async () => {
    test.setTimeout(420_000);
    await page.goto("/app/models");
    await page.getByRole("button", { name: "Retrain now" }).click();
    await expect(
      page
        .locator("[data-sonner-toast]")
        .filter({ hasText: "Retrain queued" })
        .first(),
    ).toBeVisible();
    await page.goto("/app/jobs");
    const retrain = page
      .getByRole("row")
      .filter({ hasText: "retrain" })
      .first();
    await expect(retrain).toBeVisible({ timeout: 20_000 });
    await expect(
      page
        .getByRole("row")
        .filter({ hasText: "simulator" })
        .filter({ hasText: "running" })
        .first(),
    ).toBeVisible();
    const bar = retrain.getByRole("progressbar");
    await expect
      .poll(async () => Number(await bar.getAttribute("aria-valuenow")), {
        timeout: 180_000,
      })
      .toBeGreaterThanOrEqual(15);
    await retrain.getByRole("button", { name: "Log" }).click();
    await expect(page.getByRole("region", { name: "Job log" })).toContainText(
      "loaded 600 series",
    );
    await shotBoth(page, "jobs-live");
    await axe(page, "Jobs");
    const id = (await get("/jobs?limit=20")).find(
      (j: { type: string }) => j.type === "retrain",
    ).id as string;
    await retrain.getByRole("button", { name: /^Cancel retrain/ }).click();
    await expect(retrain.getByText("cancelled")).toBeVisible({
      timeout: 20_000,
    });
    await page.waitForTimeout(8000); // the worker must not overwrite the status with a success or a failure
    expect((await get(`/jobs/${id}`)).status).toBe("cancelled");
    const versions = (await get("/models/registry")).models.reduce(
      (n: number, m: { versions: unknown[] }) => n + m.versions.length,
      0,
    );
    expect(versions).toBe(versionsBefore); // nothing from the cancelled retrain was persisted
  });

  test("Drift: forced demand raises an alert, the check starts a retrain that finishes, and registry links reach MLflow", async () => {
    test.setTimeout(1_200_000);
    await expect
      .poll(async () => (await get("/models/drift")).status, {
        timeout: 420_000,
        intervals: [5000],
      })
      .toBe("alert");
    await page.goto("/app/models");
    const card = page.getByRole("region", { name: "Drift monitor" });
    await expect(
      card.getByText("alert", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      card.getByRole("list", { name: "Drift alerts" }),
    ).toContainText("PSI");
    await expect(card.getByText(/Live WAPE/)).toBeVisible();
    await shotBoth(page, "models-drift");

    await page.goto("/app/overview");
    await page
      .getByRole("region", { name: "Live store simulator" })
      .getByRole("button", { name: "Stop simulator" })
      .click();
    await expect(
      page
        .getByRole("region", { name: "Live store simulator" })
        .getByText("stopped"),
    ).toBeVisible({ timeout: 30_000 });

    await page.goto("/app/models");
    const dataEndBefore = (await get("/models/drift")).data_end as string;
    await page.getByRole("button", { name: "Check drift now" }).click();
    await expect(
      page
        .locator("[data-sonner-toast]")
        .filter({ hasText: /Drift detected: retrain started/ })
        .first(),
    ).toBeVisible({ timeout: 30_000 });
    const jobs = await get("/jobs?limit=20");
    const dr = jobs.find(
      (j: { type: string; payload?: { reason?: string } }) =>
        j.type === "retrain" && j.payload?.reason === "drift",
    );
    expect(dr).toBeTruthy();
    let status = "queued";
    for (
      let i = 0;
      i < 400 && !["succeeded", "failed", "cancelled"].includes(status);
      i++
    ) {
      await page.waitForTimeout(3000);
      status = (await get(`/jobs/${dr.id}`)).status;
    }
    expect(status).toBe("succeeded");
    await page.reload();
    // the retrain trained on the new days: the cut-off moved forward and the card is back to "not enough data"
    const after = await get("/models/drift");
    expect(after.data_end > dataEndBefore).toBe(true);
    expect(after.enough_data).toBe(false);
    await expect(
      page.getByRole("region", { name: "Drift monitor" }),
    ).toContainText("not enough data");
    const reg = await get("/models/registry");
    const rows = reg.models.flatMap(
      (m: {
        versions: {
          mlflow_url: string;
          mlflow_run_id: string;
          metrics: { wape: number };
        }[];
      }) => m.versions,
    );
    expect(rows.length).toBe(versionsBefore + 2); // the retrain added one version per model
    const linked = rows.find(
      (v: { mlflow_url: string | null }) => v.mlflow_url,
    );
    expect(linked.mlflow_url).toContain("localhost:5000/#/experiments/");
    const run = await (
      await page
        .context()
        .request.get(
          `http://localhost:5000/api/2.0/mlflow/runs/get?run_id=${linked.mlflow_run_id}`,
        )
    ).json();
    const wape = run.run.data.metrics.find(
      (m: { key: string }) => m.key === "wape",
    ).value as number;
    expect(wape).toBeCloseTo(linked.metrics.wape, 6); // the registry number IS the MLflow number
    await expect(
      page.getByRole("link", { name: "Open run" }).first(),
    ).toHaveAttribute("href", /localhost:5000\/#\/experiments\//);
    await expect(page.getByText("promoted by").first()).toBeHidden(); // no promotion yet: history is honestly empty
    await shotBoth(page, "models-registry");
    await axe(page, "Models");
  });

  test("System page shows live backend numbers and passes axe", async () => {
    await page.goto("/app/system");
    await expect(page.getByText("API latency p50 / p95")).toBeVisible();
    await expect(page.getByText("Worker", { exact: true })).toBeVisible();
    const tile = page
      .getByText("API latency p50 / p95")
      .locator("xpath=ancestor::div[contains(@class,'card')]");
    expect(await tile.innerText()).not.toContain("n/a / n/a");
    await expect(
      page
        .getByRole("region", { name: "Latency and throughput" })
        .locator("canvas")
        .first(),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "API docs (Swagger)" }),
    ).toHaveAttribute("href", /localhost:8000\/docs/);
    await expect(page.getByRole("link", { name: "MLflow" })).toHaveAttribute(
      "href",
      /localhost:5000/,
    );
    await page.getByText("View as table").click();
    expect(
      await page
        .getByRole("region", { name: "Latency by minute" })
        .locator("tbody tr")
        .count(),
    ).toBeGreaterThan(0);
    await shotBoth(page, "system");
    await axe(page, "System");
  });

  test("SSE reconnects by itself after an API restart and live data resumes", async () => {
    test.setTimeout(180_000);
    await page.goto("/app/overview");
    await expect(page.getByText("Live", { exact: true })).toBeVisible();
    killApi();
    await expect(page.getByText(/Reconnecting/)).toBeVisible({
      timeout: 40_000,
    });
    startApi();
    await expect(page.getByText("Live", { exact: true })).toBeVisible({
      timeout: 90_000,
    });
    // the simulator in the earlier tests emptied many shelves (correctly: sales are refused, stock never negative)
    const inv = (await get("/inventory?page_size=200")).items.find(
      (i: { on_hand: number }) => i.on_hand >= 1,
    );
    expect(inv, "some item must still be in stock").toBeTruthy();
    const loc = (await get(`/locations/${inv.location_id}`)).name as string;
    const sale = await page.context().request.post(`${API}/pos/checkout`, {
      headers: {
        "X-CSRF-Token": csrf,
        "Idempotency-Key": `reconnect-${Date.now()}`,
      },
      data: {
        location_id: inv.location_id,
        lines: [{ product_id: inv.product_id, qty: 1, unit_price: "10.00" }],
      },
    });
    expect(sale.status()).toBe(200);
    // the sale was made AFTER the restart: seeing it proves the new stream is subscribed and delivering
    await expect(
      page
        .getByRole("region", { name: "Live activity" })
        .getByRole("listitem")
        .first(),
    ).toContainText(`${loc} · 1 unit`, { timeout: 20_000 });
  });

  test("a session survives the access cookie expiring (silent refresh) and the new pages fit a phone", async () => {
    test.setTimeout(120_000);
    await page.context().clearCookies({ name: "kalpa_access" }); // what happens 15 minutes after login
    await page.goto("/app/inventory");
    await expect(
      page.getByRole("heading", { level: 1, name: "Inventory" }),
    ).toBeVisible({ timeout: 30_000 });
    expect(page.url()).toContain("/app/inventory");
    await page.setViewportSize({ width: 375, height: 800 });
    for (const p of [
      "/app/overview",
      "/app/jobs",
      "/app/models",
      "/app/system",
    ]) {
      await page.goto(p);
      await expect(
        page.getByRole("heading", { level: 1 }).first(),
      ).toBeVisible();
      const over = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      expect(over, `${p} horizontal overflow at 375 px`).toBeLessThanOrEqual(0);
    }
  });
});
