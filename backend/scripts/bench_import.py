"""NFR-11 measurement: import time for a generated ~300-node BPMN model.

Usage: python scripts/bench_import.py [--nodes 300] [--runs 5]
Generates a synthetic linear-with-branches process, imports it through the real
API (in-process TestClient, temporary data dir) and prints median/worst times.
"""

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"


def make_bpmn(n: int, pad: int = 7000) -> bytes:
    parts = [f'<definitions xmlns="{NS}" id="D" targetNamespace="urn:bench"><process id="P" name="Bench">',
             '<startEvent id="n0" name="Start"/>']
    flows = []
    for i in range(1, n - 1):
        if i % 10 == 0:
            parts.append(f'<exclusiveGateway id="n{i}" name="Decision {i}?"/>')
        else:
            parts.append(f'<task id="n{i}" name="Task {i} with a reasonably descriptive label"><documentation>'
                         + "x" * pad + "</documentation></task>")
        flows.append((i - 1, i, "Yes" if (i - 1) % 10 == 0 else None))
        if i % 10 == 0 and i + 2 < n - 1:
            flows.append((i, i + 2, "No"))
    parts.append(f'<endEvent id="n{n - 1}" name="End"/>')
    flows.append((n - 2, n - 1, None))
    for k, (s, t, label) in enumerate(flows):
        name = f' name="{label}"' if label else ""
        parts.append(f'<sequenceFlow id="f{k}"{name} sourceRef="n{s}" targetRef="n{t}"/>')
    parts.append("</process></definitions>")
    return "".join(parts).encode()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, default=300)
    ap.add_argument("--runs", type=int, default=5)
    a = ap.parse_args()
    data = make_bpmn(a.nodes)
    times = []
    with tempfile.TemporaryDirectory() as d:
        client = TestClient(create_app(Settings(data_dir=Path(d)), adapter=None))
        for _ in range(a.runs):
            t0 = time.perf_counter()
            r = client.post("/api/v1/imports", files={"file": ("bench.bpmn", data, "application/xml")})
            times.append(time.perf_counter() - t0)
            assert r.status_code == 201, r.text
    nodes = r.json()["processes"][0]["node_count"]
    print(f"file={len(data) / 1e6:.2f} MB nodes={nodes} runs={a.runs} "
          f"median={statistics.median(times):.3f}s worst={max(times):.3f}s")


if __name__ == "__main__":
    main()
