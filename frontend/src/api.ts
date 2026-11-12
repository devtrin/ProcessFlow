import type { Extraction, ImportView, ProcessBundle, ProcessGraph, ProviderStatus, ReviewDecision, Variant, VariantChanges } from "./types";

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: Record<string, unknown> = {}) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(BASE + path, init);
  } catch {
    throw new ApiError(0, "BACKEND_UNREACHABLE", "The ProcessFlow backend is not reachable. Is it running on 127.0.0.1:8000?");
  }
  if (resp.status === 204) return undefined as T;
  let body: unknown = null;
  try {
    body = await resp.json();
  } catch {
    /* non-JSON */
  }
  if (!resp.ok) {
    const b = (body ?? {}) as { code?: string; message?: string; details?: Record<string, unknown> };
    throw new ApiError(resp.status, b.code ?? `HTTP_${resp.status}`, b.message ?? `Request failed (HTTP ${resp.status}).`, b.details ?? {});
  }
  return body as T;
}

const json = (method: string, data: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

export const api = {
  provider: () => call<ProviderStatus>("/provider"),
  imports: () => call<ImportView[]>("/imports"),
  getImport: (id: string) => call<ImportView>(`/imports/${encodeURIComponent(id)}`),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return call<ImportView>("/imports", { method: "POST", body: fd });
  },
  dismissImport: (id: string) => call<void>(`/imports/${encodeURIComponent(id)}`, { method: "DELETE" }),
  extraction: (id: string) => call<Extraction>(`/imports/${encodeURIComponent(id)}/extraction`),
  infer: (id: string, acknowledgeRemote: boolean) =>
    call<ImportView>(`/imports/${encodeURIComponent(id)}/infer`, json("POST", { acknowledge_remote: acknowledgeRemote })),
  process: (pid: string) => call<ProcessBundle>(`/processes/${encodeURIComponent(pid)}`),
  review: (pid: string, elementId: string, state: string, note: string) =>
    call<ReviewDecision>(`/processes/${encodeURIComponent(pid)}/reviews`, json("POST", { element_id: elementId, state, note })),
  createVariant: (pid: string, name: string) => call<Variant>(`/processes/${encodeURIComponent(pid)}/variants`, json("POST", { name })),
  variant: (vid: string) => call<{ variant: Variant; resolved: ProcessGraph; baseline_unchanged: boolean }>(`/variants/${encodeURIComponent(vid)}`),
  saveVariant: (vid: string, expectedVersion: number, name: string, changes: VariantChanges) =>
    call<{ variant: Variant; resolved: ProcessGraph }>(`/variants/${encodeURIComponent(vid)}`, json("PUT", { expected_version: expectedVersion, name, changes })),
  exportUrl: (kind: "variant" | "baseline", id: string) =>
    kind === "variant" ? `${BASE}/variants/${encodeURIComponent(id)}/export` : `${BASE}/processes/${encodeURIComponent(id)}/export`,
};
