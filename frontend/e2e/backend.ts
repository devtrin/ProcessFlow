import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

const ROOT = path.resolve(HERE, "..", "..");
const PY = process.platform === "win32"
  ? path.join(ROOT, "backend", ".venv", "Scripts", "python.exe")
  : path.join(ROOT, "backend", ".venv", "bin", "python");

export const FIX = (rel: string) => path.join(ROOT, "fixtures", rel);
export const newDataDir = () => mkdtempSync(path.join(tmpdir(), "pf-e2e-"));

export interface Backend { url: string; proc: ChildProcess; stop: () => Promise<void> }

export async function startBackend(dataDir: string, port: number, extraEnv: Record<string, string> = {}): Promise<Backend> {
  const proc = spawn(PY, ["-m", "app", "--port", String(port)], {
    cwd: path.join(ROOT, "backend"),
    env: { ...process.env, PF_DATA_DIR: dataDir, PF_INFERENCE_PROVIDER: "none", ...extraEnv },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  proc.stdout?.on("data", (d) => (log += d));
  proc.stderr?.on("data", (d) => (log += d));
  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`${url}/api/v1/health`);
      if (r.ok) break;
    } catch { /* not up yet */ }
    if (proc.exitCode !== null) throw new Error(`backend exited early:\n${log}`);
    await new Promise((r) => setTimeout(r, 150));
  }
  const stop = () => new Promise<void>((resolve) => {
    if (proc.exitCode !== null) return resolve();
    proc.once("exit", () => resolve());
    proc.kill("SIGTERM");
  });
  return { url, proc, stop };
}
