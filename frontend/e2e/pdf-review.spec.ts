import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FIX, newDataDir, startBackend, type Backend } from "./backend";

const SHOTS = path.join(path.dirname(fileURLToPath(import.meta.url)), "artifacts");

/**
 * TEST-ONLY stub of Ollama's /api/chat that replays a HAND-WRITTEN candidate
 * (fixtures/inference/purchase-honest.json). It exercises the real Ollama adapter,
 * schema validation, evidence grounding and the review UI. It is not a model and
 * says nothing about real extraction quality.
 */
function startStubOllama(port: number, fixture: string): Promise<{ server: Server; requests: unknown[] }> {
  const requests: unknown[] = [];
  const content = readFileSync(FIX(`inference/${fixture}`), "utf-8");
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      requests.push(JSON.parse(body || "{}"));
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ model: "fixture-stub", message: { role: "assistant", content } }));
    });
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve({ server, requests })));
}

test("UC-01 (stubbed model): PDF → AI-assisted draft → evidence → review decision persists; ambiguity flagged, not invented", async ({ page }) => {
  const stub = await startStubOllama(11999, "purchase-honest.json");
  const dataDir = newDataDir();
  const env = { PF_INFERENCE_PROVIDER: "ollama", PF_OLLAMA_BASE_URL: "http://127.0.0.1:11999", PF_OLLAMA_MODEL: "fixture-stub" };
  let backend: Backend = await startBackend(dataDir, 8780, env);
  try {
    await page.goto(backend.url);
    await expect(page.getByTestId("provider-status")).toContainText("ollama · fixture-stub");
    await page.setInputFiles("[data-testid=file-input]", FIX("handoff/example-process-narrative.pdf"));
    await page.getByRole("button", { name: "Draft process with AI" }).click();
    await expect(page.getByTestId("process-title")).toHaveText("PURCHASE REQUEST HANDLING (FICTIONAL TEST FIXTURE)");
    expect(stub.requests).toHaveLength(1);
    const req = stub.requests[0] as { format: { type: string }; messages: { content: string }[] };
    expect(req.format.type).toBe("object");
    expect(req.messages[1].content).toContain("[page 1 | p1b3] 2. The manager reviews");

    await expect(page.getByText("6 steps need review")).toBeVisible();
    await expect(page.getByTestId("issue-list").or(page.getByRole("button", { name: "Review purchase request" }))).toBeVisible();
    await page.getByRole("tab", { name: /Diagnostics/ }).click();
    await expect(page.getByTestId("issue-list")).toContainText("AMBIGUITY");
    await expect(page.getByTestId("issue-list")).toContainText("may need another review");
    await expect(page.locator(".react-flow__node", { hasText: /another review/i })).toHaveCount(0);

    await page.getByTestId("node-S2").click();
    const insp = page.getByRole("complementary", { name: "Step inspector" });
    await expect(insp.getByText("AI-inferred. Compare with the source evidence")).toBeVisible();
    await insp.getByRole("tab", { name: "Source evidence" }).click();
    await expect(insp.getByText("Quote found on page").first()).toBeVisible();
    await insp.getByRole("button", { name: "Show page 1 text" }).first().click();
    await expect(insp.locator(".page-text p.hit")).toContainText("The manager reviews the purchase request");
    await page.screenshot({ path: path.join(SHOTS, "pdf-draft-evidence-1366.png") });
    await insp.getByRole("tab", { name: "Details" }).click();
    await insp.getByRole("button", { name: "Mark verified" }).click();
    await expect(insp.locator(".review-box .badge")).toHaveText("Verified");
    await expect(page.getByText("5 steps need review")).toBeVisible();

    // restart backend; review decision and draft survive, baseline still marked needs_review
    await backend.stop();
    backend = await startBackend(dataDir, 8780, env);
    await page.goto(backend.url);
    await page.getByRole("button", { name: /example-process-narrative\.pdf/ }).click();
    await expect(page.getByText("5 steps need review")).toBeVisible();
    await page.getByTestId("node-S2").click();
    await expect(insp.locator(".review-box .badge")).toHaveText("Verified");
    const exp = await (await page.request.get(`${backend.url}/api/v1/processes/${(await page.request.get(`${backend.url}/api/v1/imports`).then((r) => r.json()))[0].process_ids[0]}/export`)).json();
    expect(exp.baseline.nodes.find((n: { id: string }) => n.id === "S2").review_state).toBe("needs_review");
    expect(exp.reviews.map((r: { element_id: string; state: string }) => [r.element_id, r.state])).toEqual([["S2", "verified"]]);
  } finally {
    await backend.stop();
    stub.server.close();
  }
});
