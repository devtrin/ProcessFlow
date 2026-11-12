"""Regenerate schema/process-graph.schema.json from the Pydantic models."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import ProcessGraph  # noqa: E402

out = Path(__file__).resolve().parents[2] / "schema" / "process-graph.schema.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(ProcessGraph.model_json_schema(), indent=2) + "\n", encoding="utf-8")
print(f"wrote {out}")
