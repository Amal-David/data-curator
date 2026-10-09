import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

async function ready(page: Page) {
  await page.goto("/");
  await expect(page.getByText("Local engine connected")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run pipeline", exact: true }),
  ).toBeEnabled();
}

async function listDatasets(request: APIRequestContext) {
  const response = await request.get("/api/datasets");
  expect(response.ok()).toBeTruthy();
  return (await response.json()).datasets as {
    id: string;
    name: string;
    role: string;
    record_count: number;
  }[];
}

async function complete(page: Page) {
  await expect(
    page.getByRole("heading", {
      name: "A clearer dataset. A complete paper trail.",
    }),
  ).toBeVisible({ timeout: 45_000 });
  await expect(
    page.getByRole("link", { name: "Export curated JSONL" }),
  ).toBeVisible();
}

async function exportBytes(page: Page) {
  const pending = page.waitForEvent("download");
  await page.getByRole("link", { name: "Export curated JSONL" }).click();
  const download = await pending;
  expect(download.suggestedFilename()).toMatch(/curated\.jsonl$/);
  const path = await download.path();
  expect(path).not.toBeNull();
  return readFile(path!);
}

test("demo to recipe, inspectable decisions, verified export, retry and persisted history", async ({
  page,
  request,
}, testInfo) => {
  const externalRequests: string[] = [];
  page.on("request", (event) => {
    if (!event.url().startsWith("http://127.0.0.1:8766/"))
      externalRequests.push(event.url());
  });
  await ready(page);
  await page
    .getByRole("button", { name: "Load demo data", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Demo datasets are ready" }),
  ).toBeVisible();
  const firstInputs = await listDatasets(request);
  await page
    .getByRole("button", { name: "Load demo data", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Load demo data", exact: true }),
  ).toBeEnabled();
  expect((await listDatasets(request)).map((d) => d.id)).toEqual(
    firstInputs.map((d) => d.id),
  );
  await expect(
    page
      .locator(".dataset-group-label")
      .filter({ hasText: "Calibration targets" }),
  ).toBeVisible();
  await expect(
    page
      .locator(".dataset-group-label")
      .filter({ hasText: "Evaluation holdouts" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /02\s*Calibrate/ }).click();
  await page
    .getByLabel("Task description")
    .fill("Practical data engineering and reliable Python programming.");
  await page.getByRole("button", { name: /03\s*Create/ }).click();
  await expect(
    page.getByText("Generation is disabled", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("switch", { name: "Enable synthetic generation" }),
  ).not.toBeChecked();
  await page.getByRole("button", { name: /04\s*Compose/ }).click();
  await page.getByLabel("Record budget").fill("100");
  await page.getByLabel("Random seed").fill("123");
  await page.getByRole("button", { name: "Save recipe", exact: true }).click();
  const save = page.getByRole("dialog", { name: "Save reusable recipe" });
  await save.getByLabel("Recipe name").fill("Browser verified recipe");
  await save.getByRole("button", { name: "Save recipe", exact: true }).click();
  await expect(save).not.toBeVisible();
  await expect(
    page
      .getByLabel("Load saved recipe")
      .getByRole("option", { name: "Browser verified recipe" }),
  ).toBeAttached();
  await page.getByRole("button", { name: "Run pipeline", exact: true }).click();
  await complete(page);
  await expect(
    page.getByText("Estimated tokens", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Skipped: disabled by default", { exact: false }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Decision explorer" }).click();
  await page.getByLabel("Filter decisions by action").selectOption("reject");
  await expect(
    page.getByText("exact duplicate", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText("holdout overlap", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Manifest", exact: true }).click();
  await expect(page.locator(".json-preview")).toContainText("output_sha256");
  const manifest = JSON.parse(await page.locator(".json-preview").innerText());
  const firstOutput = await exportBytes(page);
  expect(createHash("sha256").update(firstOutput).digest("hex")).toBe(
    manifest.output_sha256,
  );
  const exportedRecords = firstOutput
    .toString("utf8")
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  expect(exportedRecords.length).toBe(manifest.output_records);
  expect(new Set(exportedRecords.map((record) => record.id)).size).toBe(
    exportedRecords.length,
  );
  await page.getByRole("tab", { name: "Curated records", exact: true }).click();
  await page
    .getByRole("button", { name: /^Inspect record / })
    .first()
    .click();
  await expect(
    page.getByRole("dialog", { name: "Record inspector" }),
  ).toContainText("Provenance & lineage");
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await page.screenshot({
    path: testInfo.outputPath("completed-workflow.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Retry run", exact: true }).click();
  await complete(page);
  expect(await exportBytes(page)).toEqual(firstOutput);
  await page.reload();
  await page.getByRole("button", { name: /Run history/ }).click();
  await expect(page.getByRole("table")).toContainText(
    "Browser verified recipe",
  );
  await page
    .getByRole("button", { name: "Open run", exact: false })
    .first()
    .click();
  await complete(page);
  expect(await exportBytes(page)).toEqual(firstOutput);
  expect(externalRequests).toEqual([]);
});

test("failed imports are atomic and inspector displays untrusted markup and code as text", async ({
  page,
  request,
}) => {
  await ready(page);
  const before = await listDatasets(request);
  await page.getByRole("button", { name: "Import data", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Import a local dataset" });
  await dialog
    .getByLabel("Dataset file")
    .setInputFiles({
      name: "broken.jsonl",
      mimeType: "application/json",
      buffer: Buffer.from('{"text":"valid first row"}\n{"text":42}\n'),
    });
  await dialog
    .getByRole("button", { name: "Import dataset", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  expect((await listDatasets(request)).map((d) => d.id)).toEqual(
    before.map((d) => d.id),
  );
  await dialog
    .getByLabel("Dataset file")
    .setInputFiles({
      name: "invalid-utf8.txt",
      mimeType: "text/plain",
      buffer: Buffer.from([0xc3, 0x28]),
    });
  await dialog
    .getByRole("button", { name: "Import dataset", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText("not valid UTF-8");
  const markup =
    '<img src=x onerror="globalThis.__curatorInjected=true"><script>globalThis.__curatorInjected=true</script> Untrusted dataset content stays text.';
  await dialog
    .getByLabel("Dataset file")
    .setInputFiles({
      name: "safe-markup.jsonl",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify({ text: markup })),
    });
  await dialog.getByLabel("Dataset name").fill("Untrusted markup fixture");
  await dialog
    .getByRole("button", { name: "Import dataset", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: /Untrusted markup fixture/ }).click();
  const inspector = page.getByRole("dialog", {
    name: "Untrusted markup fixture",
  });
  await expect(inspector.locator("pre").first()).toHaveText(markup);
  expect(await inspector.locator("img, script").count()).toBe(0);
  expect(
    await page.evaluate(() => Object.hasOwn(globalThis, "__curatorInjected")),
  ).toBe(false);
  await inspector.getByRole("button", { name: "Done", exact: true }).click();
  await page.getByRole("button", { name: "Import data", exact: true }).click();
  const code =
    "def Calculate(Value):\n    if Value > 0:\n        return Value * 2\n    return 0\n";
  await dialog
    .getByLabel("Dataset file")
    .setInputFiles({
      name: "indentation.py",
      mimeType: "text/plain",
      buffer: Buffer.from(code),
    });
  await expect(dialog.getByLabel("Content kind")).toHaveValue("code");
  await dialog
    .getByRole("button", { name: "Import dataset", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: /indentation/ }).click();
  expect(
    await page
      .getByRole("dialog", { name: "indentation", exact: true })
      .locator("pre")
      .first()
      .textContent(),
  ).toBe(code);
});

test("invalid weights, blank numbers and unconfigured generation cannot start runs", async ({
  page,
  request,
}) => {
  await request.post("/api/demo", { data: {} });
  await ready(page);
  const baseline = (await (await request.get("/api/runs")).json()).runs.length;
  await page.getByRole("button", { name: /04\s*Compose/ }).click();
  const weights = page.getByRole("spinbutton", { name: /^Weight for / });
  for (const weight of await weights.all()) await weight.fill("0");
  await page.getByRole("button", { name: "Run pipeline", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "at least one positive weight",
  );
  await weights.first().fill("1");
  await page.getByLabel("Record budget").fill("");
  await page.getByRole("button", { name: "Run pipeline", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("finite number");
  await page.getByLabel("Record budget").fill("10");
  await page.getByRole("button", { name: /03\s*Create/ }).click();
  await page
    .getByRole("switch", { name: "Enable synthetic generation" })
    .click();
  await page.getByRole("button", { name: "Run pipeline", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("local model name");
  expect((await (await request.get("/api/runs")).json()).runs.length).toBe(
    baseline,
  );
});

test("cancel a real running pipeline, then retry successfully without changing inputs", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(120_000);
  const records = Array.from({ length: 1800 }, (_, i) => ({
    text:
      Array.from(
        { length: 70 },
        (_, j) => `word${i}_${j}_${(i * 7919 + j * 104729) % 999983}`,
      ).join(" ") +
      ". Each unique record supports cancellable local processing.",
  }));
  const upload = await request.post("/api/datasets", {
    data: {
      name: "Cancellation workload",
      role: "source",
      filename: "cancellation.jsonl",
      content: records.map((record) => JSON.stringify(record)).join("\n"),
    },
  });
  expect(upload.ok()).toBeTruthy();
  const dataset = await upload.json();
  await ready(page);
  for (const checkbox of await page
    .getByRole("checkbox", { name: /^Include / })
    .all())
    await checkbox.uncheck();
  await page
    .getByRole("checkbox", {
      name: "Include Cancellation workload",
      exact: true,
    })
    .check();
  await page.getByRole("button", { name: "Run pipeline", exact: true }).click();
  await page.getByRole("button", { name: "Cancel run", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Run cancelled.", exact: true }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(
    page.getByRole("link", { name: "Export curated JSONL" }),
  ).not.toBeVisible();
  expect(
    (await (await request.get(`/api/datasets/${dataset.id}`)).json())
      .record_count,
  ).toBe(records.length);
  await page.screenshot({
    path: testInfo.outputPath("cancelled-run.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Retry run", exact: true }).click();
  await complete(page);
  const exported = await exportBytes(page);
  expect(exported.length).toBeGreaterThan(0);
  expect(
    (await (await request.get(`/api/datasets/${dataset.id}`)).json())
      .record_count,
  ).toBe(records.length);
});
