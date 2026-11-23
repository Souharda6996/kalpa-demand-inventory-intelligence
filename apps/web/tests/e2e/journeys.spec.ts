import path from "node:path";
import { expect, type Page, test } from "@playwright/test";

const SHOTS = path.resolve(__dirname, "../../../../artifacts/screenshots");
const email = `e2e-${Date.now()}@example.com`;
const PASSWORD = "correct-horse-battery";

async function theme(page: Page, t: "light" | "dark") {
  await page.evaluate((v) => (document.documentElement.dataset.theme = v), t);
}

/** Screenshot the current page in both themes. */
async function shotBoth(page: Page, name: string) {
  for (const t of ["light", "dark"] as const) {
    await theme(page, t);
    await page.waitForTimeout(350); // let the 150 ms colour transitions finish before capturing
    await page.screenshot({ path: path.join(SHOTS, `${name}-${t}.png`), fullPage: true });
  }
  await theme(page, "light");
}

async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "horizontal page overflow in px").toBeLessThanOrEqual(0);
}

test.describe.serial("core journeys", () => {
  let page: Page;
  test.beforeAll(async ({ browser }) => {
    page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
  });
  test.afterAll(async () => page.context().close());

  test("landing page renders and is responsive at 375px", async () => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Know what you will sell");
    await shotBoth(page, "landing");
    await page.setViewportSize({ width: 375, height: 812 });
    await noHorizontalOverflow(page);
    await page.screenshot({ path: path.join(SHOTS, "landing-375.png"), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 800 });
  });

  test("signup -> onboarding (demo data) -> inventory visible", async () => {
    await page.goto("/signup");
    await shotBoth(page, "signup");
    await page.getByLabel("Your name").fill("E2E Owner");
    await page.getByLabel("Organisation").fill("E2E Retail");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page).toHaveURL(/\/onboarding/);
    await page.getByRole("button", { name: "Continue" }).click(); // org step (name prefilled)
    await page.getByLabel("Store name").fill("Koramangala");
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: /Load demo data/ }).click();
    await expect(page).toHaveURL(/\/app\/overview/, { timeout: 200_000 });
    await expect(page.getByText("Sales rows")).toBeVisible();
    await shotBoth(page, "overview");

    await page.getByRole("link", { name: "Inventory", exact: true }).first().click();
    await expect(page.getByText(/600 rows/)).toBeVisible();
    await shotBoth(page, "inventory");
  });

  test("keyboard: Ctrl+K palette navigates", async () => {
    await page.goto("/app/overview");
    await expect(page.getByText("Sales rows")).toBeVisible(); // client-rendered => hydrated, key handler attached
    await page.keyboard.press("Control+k");
    await page.getByPlaceholder("Type a command or page...").fill("Settings");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/app\/settings/);
    await expect(page.getByRole("heading", { name: "Members & roles" })).toBeVisible();
    await shotBoth(page, "settings");
  });

  test("POS: sell an item, stock drops, PDF receipt downloads", async () => {
    await page.goto("/app/pos");
    await page.getByLabel("Search products").fill("kaju");
    await page.getByRole("button", { name: /Kaju Katli/ }).first().click();
    await page.getByLabel("Price for Kaju Katli").fill("500");
    await shotBoth(page, "pos");
    await page.getByRole("button", { name: /Check out/ }).click();
    await expect(page.getByRole("status")).toContainText("Receipt #");
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download PDF" }).click()]);
    expect(download.suggestedFilename()).toMatch(/^receipt-.*\.pdf$/);
  });

  test("Forecast Explorer: run the baseline backtest, see bands + leaderboard", async () => {
    test.setTimeout(1_200_000);
    await page.goto("/app/forecasts");
    await expect(page.getByText("No forecasts yet")).toBeVisible();
    await page.getByRole("button", { name: "Run baseline backtest" }).click();
    await expect(page.getByRole("heading", { name: "Baseline leaderboard" })).toBeVisible({ timeout: 1_100_000 });
    await expect(page.getByRole("row", { name: /AutoETS/ })).toBeVisible();
    await expect(page.getByRole("row", { name: /LightGBM/ })).toBeVisible();
    await expect(page.getByRole("row", { name: /Seasonal Naive/ })).toBeVisible();
    await expect(page.getByRole("row", { name: /Croston/ })).toBeVisible();
    await expect(page.locator("canvas").first()).toBeVisible();
    await expect(page.getByLabel("Model metrics")).toContainText("WAPE");
    await expect(page.getByLabel("Model metrics")).toContainText("source: run");
    await page.getByLabel("Model", { exact: true }).selectOption({ label: "LightGBM (Tweedie)" });
    await expect(page.getByLabel("Model metrics")).toContainText("LightGBM");
    await shotBoth(page, "forecasts");
    // Model Arena: leaderboard, calibration section, champion promotion (owner)
    await page.goto("/app/arena");
    await expect(page.getByRole("heading", { name: "Interval calibration" })).toBeVisible();
    await expect(page.getByRole("row", { name: /LightGBM/ })).toBeVisible();
    await page.getByRole("row", { name: /AutoETS/ }).getByRole("button", { name: "Promote" }).click();
    await expect(page.getByRole("row", { name: /AutoETS/ })).toContainText("champion");
    await shotBoth(page, "arena");
  });

  test("Data wizard: bad CSV shows readable errors, good CSV commits", async () => {
    await page.goto("/app/data");
    const bad = "store,sku,date,units\nS1,A1,2024-01-01,-4\nS1,A1,not-a-date,2\n";
    await page.getByLabel("Sales CSV").setInputFiles({ name: "bad.csv", mimeType: "text/csv", buffer: Buffer.from(bad) });
    await expect(page.locator("ul[role=alert]")).toContainText(/units|date/);
    await page.getByRole("button", { name: "Commit to database" }).click();
    await expect(page.getByText("CSV failed validation")).toBeVisible();

    const good = "store,sku,date,units\nS1,A1,2024-01-01,3\nS1,A1,2024-01-03,5\n";
    await page.getByLabel("Sales CSV").setInputFiles({ name: "good.csv", mimeType: "text/csv", buffer: Buffer.from(good) });
    await page.getByRole("button", { name: "Commit to database" }).click();
    await expect(page.getByText(/Loaded 3 rows/)).toBeVisible();
    await shotBoth(page, "data");
  });

  test("app pages have no horizontal overflow at 375px; sign out returns to login", async () => {
    await page.setViewportSize({ width: 375, height: 812 });
    for (const p of ["/app/overview", "/app/inventory", "/app/pos", "/app/settings", "/app/data"]) {
      await page.goto(p);
      await page.waitForLoadState("networkidle");
      await noHorizontalOverflow(page);
    }
    await page.screenshot({ path: path.join(SHOTS, "data-375.png"), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login/);
    await page.goto("/app/overview");
    await expect(page).toHaveURL(/\/login/); // guarded
  });
});
