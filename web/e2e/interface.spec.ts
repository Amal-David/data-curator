import { expect, test } from "@playwright/test";

test("desktop and narrow layouts preserve usable controls and dialog typing", async ({
  page,
  request,
}, testInfo) => {
  await request.post("/api/demo", { data: {} });
  await page.setViewportSize({ width: 1440, height: 1060 });
  await page.goto("/");
  await expect(page.getByText("Local engine connected")).toBeVisible();
  await page.getByRole("button", { name: "Save recipe", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Save reusable recipe" });
  const field = dialog.getByLabel("Recipe name");
  await field.fill("");
  await field.pressSequentially("Keyboard focus stays here", { delay: 15 });
  await expect(field).toHaveValue("Keyboard focus stays here");
  await expect(field).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("studio-overview.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Run pipeline", exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "A clearer dataset. A complete paper trail.",
    }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Decision explorer" }).click();
  await page.getByLabel("Filter decisions by action").selectOption("reject");
  await expect(
    page.getByText("holdout overlap", { exact: true }),
  ).toBeVisible();
  await page.locator("details").first().getByText("Decision details").click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("studio-decisions.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await expect(
    page.getByRole("button", { name: /Curation studio/ }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("studio-mobile.png"),
    fullPage: true,
  });
});
