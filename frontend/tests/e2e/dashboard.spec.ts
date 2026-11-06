import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

// Runs against a live stack (docker compose up) with data loaded:
//   E2E_EMAIL / E2E_PASSWORD = admin credentials, E2E_SYMBOL = a loaded equity
const EMAIL = process.env.E2E_EMAIL || "admin@example.com";
const PASSWORD = process.env.E2E_PASSWORD || "change-me-admin-password";
const SYMBOL = process.env.E2E_SYMBOL || "AAPL";

// Log in once (the API rate-limits login attempts) and reuse the session cookie.
test.use({ storageState: "test-results/.auth.json" });

test.beforeAll(async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: undefined });
  const page = await ctx.newPage();
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/$/);
  await ctx.storageState({ path: "test-results/.auth.json" });
  await ctx.close();
});

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("dashboard renders market overview with data source labels", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Market Dashboard" })).toBeVisible();
  const overview = page.getByTestId("market-overview");
  await expect(overview.getByText("S&P 500").first()).toBeVisible();
  // end-of-day data must never be labelled LIVE
  await expect(overview.getByTestId("data-status").first()).toHaveText(/END OF DAY|HISTORICAL|STALE|UNAVAILABLE/);
  await expect(overview.getByText("LIVE", { exact: true })).toHaveCount(0);
});

test("stock search opens the detail page with chart and score", async ({ page, isMobile }) => {
  if (isMobile) await page.getByLabel("Open menu").click();
  await page.getByLabel("Search symbol").locator("visible=true").fill(SYMBOL.toLowerCase());
  await page.getByLabel("Search symbol").locator("visible=true").press("Enter");
  await expect(page).toHaveURL(new RegExp(`/stocks/${SYMBOL}$`));
  await expect(page.getByTestId("price-chart")).toBeVisible();
  await expect(page.getByText("Long-Term Investment Score")).toBeVisible();
  await page.getByRole("tab", { name: "Weekly" }).click();
  await expect(page.getByTestId("price-chart")).toBeVisible();
});

test("watchlist add and remove", async ({ page }) => {
  await page.goto("/watchlist");
  await page.getByLabel("Add symbol").fill(SYMBOL);
  await page.getByRole("button", { name: "Add" }).click();
  const table = page.getByTestId("stock-table");
  await expect(table.getByRole("link", { name: SYMBOL, exact: true })).toBeVisible();
  await page.getByLabel(`Remove ${SYMBOL}`).click();
  await expect(page.getByLabel(`Remove ${SYMBOL}`)).toHaveCount(0);
});

test("portfolio create and add holding", async ({ page }) => {
  await page.goto("/portfolio");
  const name = `E2E ${Date.now()}`;
  await page.getByPlaceholder("New portfolio").fill(name);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByLabel("Portfolio")).toContainText(name);
  const form = page.locator("form").filter({ hasText: "Average cost" });
  await form.locator("input").nth(0).fill(SYMBOL);
  await form.locator("input").nth(1).fill("10");
  await form.locator("input").nth(2).fill("100");
  await form.locator("input").nth(3).fill("2025-01-02");
  await form.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("link", { name: SYMBOL, exact: true })).toBeVisible();
  await expect(page.getByText("Market value")).toBeVisible();
});

test("signal center and LINE flex preview", async ({ page }) => {
  await page.goto("/signals");
  await expect(page.getByTestId("signal-groups")).toBeVisible();
  await page.goto("/settings/line");
  await page.getByRole("button", { name: "Preview" }).click();
  const preview = page.getByTestId("flex-preview");
  await expect(preview.getByText(new RegExp(`${SYMBOL} — ACCUMULATION WATCH`))).toBeVisible();
  await expect(preview.getByText("Investment Score", { exact: true })).toBeVisible();
});

test("layout has no horizontal page overflow", async ({ page }) => {
  for (const path of ["/", "/watchlist", "/signals", "/portfolio"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, path).toBeLessThanOrEqual(1);
  }
});
