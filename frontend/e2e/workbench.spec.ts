import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FIX, newDataDir, startBackend, type Backend } from "./backend";

const SHOTS = path.join(path.dirname(fileURLToPath(import.meta.url)), "artifacts");

async function importFile(page: Page, rel: string) {
  await page.setInputFiles("[data-testid=file-input]", FIX(rel));
}

const inspector = (page: Page) => page.getByRole("complementary", { name: "Step inspector" });
const item = (page: Page, name: string) => inspector(page).getByTestId(`item-${name}`);

test.describe("ProcessFlow workbench", () => {
  let backend: Backend | null = null;
  test.afterEach(async () => {
    await backend?.stop();
    backend = null;
  });

  test("UC-02: import BPMN → select step → tailor → save → backend restart → reload shows saved variant; baseline intact", async ({ page }) => {
    const dataDir = newDataDir();
    backend = await startBackend(dataDir, 8765);
    await page.goto(backend.url);
    await expect(page.getByTestId("provider-status")).toContainText("Not configured");

    await importFile(page, "bpmn/purchase-with-data.bpmn");
    await expect(page.getByTestId("process-title")).toHaveText("Purchase with data");
    await page.getByTestId("node-Task_Review").click();
    await expect(page.getByTestId("inspector-title")).toHaveText("Review request");
    // Baseline is read-only: no edit controls.
    await expect(inspector(page).getByRole("button", { name: "Tailor out Budget reference" })).toHaveCount(0);

    await page.getByRole("button", { name: "+ New variant" }).click();
    await page.getByLabel("New variant name").fill("Pilot rollout");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByTestId("save-state")).toHaveText("Saved · v1");
    await page.getByTestId("node-Task_Review").click();

    // Tailor out, rename, add.
    await inspector(page).getByRole("button", { name: "Tailor out Budget reference" }).click();
    await expect(item(page, "Budget reference")).toContainText("Tailored out");
    await inspector(page).getByRole("button", { name: "Edit Purchase request" }).click();
    await inspector(page).getByLabel("Name").fill("Purchase request form");
    await inspector(page).getByRole("button", { name: "Apply" }).click();
    await expect(item(page, "Purchase request form")).toContainText("Renamed");
    await inspector(page).getByRole("button", { name: "+ Add output" }).click();
    await inspector(page).getByLabel("New output name").fill("Review checklist");
    await inspector(page).getByRole("button", { name: "Add output", exact: true }).click();
    await expect(item(page, "Review checklist")).toContainText("User-added");
    // Negative case: empty name cannot be added.
    await inspector(page).getByRole("button", { name: "+ Add input" }).click();
    await expect(inspector(page).getByRole("button", { name: "Add input", exact: true })).toBeDisabled();
    await inspector(page).getByRole("button", { name: "Cancel" }).click();

    await expect(page.getByTestId("save-state")).toHaveText("Unsaved changes");
    await page.getByTestId("save-variant").click();
    await expect(page.getByTestId("save-state")).toHaveText("Saved · v2");
    await expect(page.getByTestId("notice")).toContainText("does not verify or approve");

    // ---- full backend restart on the same data directory, then a fresh page load
    await backend.stop();
    backend = await startBackend(dataDir, 8765);
    await page.goto(backend.url);
    await page.getByRole("button", { name: /purchase-with-data\.bpmn/ }).click();
    await expect(page.getByTestId("process-title")).toHaveText("Purchase with data");
    await page.getByLabel("Select variant").selectOption({ label: "Pilot rollout (v2)" });
    await expect(page.getByTestId("save-state")).toHaveText("Saved · v2");
    await page.getByTestId("node-Task_Review").click();
    await expect(item(page, "Budget reference")).toContainText("Tailored out");
    await expect(item(page, "Purchase request form")).toBeVisible();
    await expect(item(page, "Review checklist")).toContainText("User-added");
    await page.screenshot({ path: path.join(SHOTS, "variant-after-restart-1366.png") });

    // ---- baseline untouched
    await page.getByRole("button", { name: "Baseline", exact: true }).click();
    await expect(item(page, "Purchase request")).toContainText("From source");
    await expect(item(page, "Budget reference")).not.toContainText("Tailored out");
    await expect(item(page, "Review checklist")).toHaveCount(0);

    // ---- restore and save again
    await page.getByRole("button", { name: "Variant", exact: true }).click();
    await inspector(page).getByRole("button", { name: "Restore Budget reference" }).click();
    await expect(item(page, "Budget reference")).not.toContainText("Tailored out");
    await page.getByTestId("save-variant").click();
    await expect(page.getByTestId("save-state")).toHaveText("Saved · v3");

    // ---- export records changes and source provenance
    const href = await page.getByRole("link", { name: "Export JSON" }).getAttribute("href");
    const exp = await (await page.request.get(backend.url + href)).json();
    expect(exp.variant.version).toBe(3);
    expect(exp.baseline_unchanged).toBe(true);
    expect(exp.source.filename).toBe("purchase-with-data.bpmn");
    expect(exp.variant.changes.overrides.map((o: { name: string }) => o.name)).toEqual(["Purchase request form"]);
    expect(exp.variant.change_log.at(-1).summary.join(" ")).toContain("Restored 'Budget reference'");
  });

  test("keyboard: Tab to a step and press Enter to select it", async ({ page }) => {
    backend = await startBackend(newDataDir(), 8766);
    await page.goto(backend.url);
    await importFile(page, "handoff/example-no-di.bpmn");
    await expect(page.getByTestId("node-Task_Review")).toBeVisible();
    const target = page.locator('.react-flow__node[data-id="Task_Review"]');
    await target.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("inspector-title")).toHaveText("Review request");
    // Find step is keyboard reachable too.
    await page.getByLabel("Find step").fill("notify");
    await page.getByRole("option", { name: /Notify requester/ }).press("Enter");
    await expect(page.getByTestId("inspector-title")).toHaveText("Notify requester");
  });

  test("XML without DI renders with auto layout; with DI offers source layout; semantics shown", async ({ page }) => {
    backend = await startBackend(newDataDir(), 8767);
    await page.goto(backend.url);
    await importFile(page, "handoff/example-no-di.bpmn");
    await expect(page.locator(".react-flow__node-card, .react-flow__node-round")).toHaveCount(6);
    await expect(page.getByRole("button", { name: "Source layout" })).toBeDisabled();
    await expect(page.locator(".react-flow__edge-text", { hasText: "Yes" })).toBeVisible();
    await expect(page.locator(".react-flow__edge-text", { hasText: "No (default)" })).toBeVisible();
    await expect(page.locator(".band-label", { hasText: "Manager" })).toBeVisible();
    await importFile(page, "handoff/example-with-di.bpmn");
    await expect(page.getByRole("button", { name: "Source layout" })).toBeEnabled();
    await page.getByRole("button", { name: "Source layout" }).click();
    await expect(page.locator(".react-flow__node-card, .react-flow__node-round")).toHaveCount(6);
  });

  test("NFR-08: HTML/script in labels is rendered as text and never executed", async ({ page }) => {
    backend = await startBackend(newDataDir(), 8768);
    await page.goto(backend.url);
    await importFile(page, "bpmn/xss-labels.bpmn");
    await expect(page.getByTestId("process-title")).toHaveText("<b>Bold</b> process");
    await expect(page.getByTestId("node-X_Task")).toContainText("<script>window.__pfXss=2</script>Task");
    await page.getByTestId("node-X_Task").click();
    await expect(page.getByTestId("inspector-title")).toHaveText("<script>window.__pfXss=2</script>Task");
    expect(await page.evaluate(() => (window as unknown as { __pfXss?: number }).__pfXss)).toBeUndefined();
    expect(await page.locator("script:not([src])").count()).toBe(0);
    expect(await page.locator("img[src=x]").count()).toBe(0);
  });

  test("malicious XML is rejected with a visible diagnostic", async ({ page }) => {
    backend = await startBackend(newDataDir(), 8769);
    await page.goto(backend.url);
    await importFile(page, "handoff/invalid-xxe.xml");
    await expect(page.getByTestId("upload-error")).toContainText("XML_DTD_FORBIDDEN");
    await expect(page.getByText("Failed")).toBeVisible();
  });

  test("PDF without a provider: text extracted, drafting clearly unavailable, nothing invented", async ({ page }) => {
    backend = await startBackend(newDataDir(), 8770);
    await page.goto(backend.url);
    await importFile(page, "handoff/example-process-narrative.pdf");
    await expect(page.getByText("Text extracted")).toBeVisible();
    await expect(page.getByText("1 page(s), 11 text blocks extracted.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Draft process with AI" })).toBeDisabled();
    await expect(page.getByText(/no steps are invented without a model/)).toBeVisible();
    await expect(page.getByTestId("process-title")).toHaveCount(0);
  });

  for (const vp of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
    test(`NFR-07 layout at ${vp.width}x${vp.height}: canvas, inspector and controls fit`, async ({ page }) => {
      await page.setViewportSize(vp);
      backend = await startBackend(newDataDir(), 8771);
      await page.goto(backend.url);
      await importFile(page, "bpmn/purchase-with-data.bpmn");
      await page.getByTestId("node-Task_Review").click();
      await page.getByRole("button", { name: "+ New variant" }).click();
      await page.getByLabel("New variant name").fill("Layout check");
      await page.getByRole("button", { name: "Create" }).click();
      await page.getByTestId("node-Task_Review").click();
      await inspector(page).getByRole("button", { name: "Tailor out Budget reference" }).click();
      const insp = await inspector(page).boundingBox();
      const canvas = await page.getByTestId("canvas").boundingBox();
      const save = await page.getByTestId("save-variant").boundingBox();
      expect(insp && insp.x + insp.width).toBeLessThanOrEqual(vp.width);
      expect(insp!.width).toBeGreaterThanOrEqual(320);
      expect(canvas!.width).toBeGreaterThan(vp.width * 0.4);
      expect(canvas!.height).toBeGreaterThan(300);
      expect(save!.x + save!.width).toBeLessThanOrEqual(vp.width);
      const scroll = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }));
      expect(scroll.w).toBeLessThanOrEqual(vp.width);
      expect(scroll.h).toBeLessThanOrEqual(vp.height);
      await page.screenshot({ path: path.join(SHOTS, `workbench-${vp.width}x${vp.height}.png`) });
    });
  }
});
