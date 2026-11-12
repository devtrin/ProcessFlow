import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { newDataDir, startBackend } from "./backend";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
const PY = process.platform === "win32" ? path.join(ROOT, "backend", ".venv", "Scripts", "python.exe") : path.join(ROOT, "backend", ".venv", "bin", "python");

// NFR-12 measurement (headless Chromium in whatever machine runs the test; not "defined hardware").
test("NFR-12: 300-node graph renders and selection responds", async ({ page }) => {
  const dir = newDataDir();
  const file = path.join(dir, "bench-300.bpmn");
  writeFileSync(file, execFileSync(PY, ["-c", "import sys; sys.path.insert(0,'scripts'); from bench_import import make_bpmn; sys.stdout.buffer.write(make_bpmn(300, 200))"], { cwd: path.join(ROOT, "backend") }));
  const backend = await startBackend(dir, 8790);
  try {
    await page.goto(backend.url);
    const t0 = Date.now();
    await page.setInputFiles("[data-testid=file-input]", file);
    await expect(page.locator(".react-flow__node-card, .react-flow__node-round")).toHaveCount(300, { timeout: 60_000 });
    const renderMs = Date.now() - t0;
    const samples: number[] = [];
    for (const id of ["n5", "n55", "n105", "n155", "n205", "n255", "n295", "n15", "n65", "n115"]) {
      await page.evaluate((nid) => {
        (window as unknown as { __t: number }).__t = performance.now();
        document.querySelector<HTMLElement>(`.react-flow__node[data-id="${nid}"]`)!.click();
      }, id);
      await page.waitForFunction((nid) => document.querySelector("[data-testid=inspector-title]")?.textContent?.includes(nid.slice(1)) ?? false, id);
      samples.push(await page.evaluate(() => performance.now() - (window as unknown as { __t: number }).__t));
    }
    samples.sort((a, b) => a - b);
    const p95 = samples[Math.ceil(samples.length * 0.95) - 1];
    const line = `import+layout+render of 300 nodes: ${renderMs} ms; selection->inspector: median ${samples[5].toFixed(0)} ms, p95 ${p95.toFixed(0)} ms (n=${samples.length})`;
    console.log(line);
    mkdirSync(path.join(HERE, "artifacts"), { recursive: true });
    writeFileSync(path.join(HERE, "artifacts", "perf-300-nodes.txt"), line + "\n");
  } finally {
    await backend.stop();
  }
});
