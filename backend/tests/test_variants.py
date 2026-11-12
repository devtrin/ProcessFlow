"""FR-18..FR-22, FR-25, NFR-06, NFR-17: tailoring, persistence across restart, export."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import jsonschema
import pytest

from .conftest import ROOT, fixture_bytes, upload


@pytest.fixture
def setup(make_client):
    c = make_client()
    rec = upload(c, "bpmn/purchase-with-data.bpmn").json()
    pid = next(p["process_id"] for p in rec["processes"] if p["view"] == "process")
    return c, rec, pid


def _changes(**kw):
    return {"overrides": kw.get("overrides", []), "added": kw.get("added", [])}


def test_t18_to_t22_tailor_save_restart_reload(setup, make_client, settings):
    c, rec, pid = setup
    base_before = c.get(f"/api/v1/processes/{pid}").json()
    v = c.post(f"/api/v1/processes/{pid}/variants", json={"name": "Pilot rollout"}).json()
    assert v["version"] == 1 and v["baseline_sha256"] == base_before["baseline_sha256"]
    added = {"node_id": "Task_Review", "item": {
        "id": "user_1", "name": "Review checklist", "description": "", "direction": "output",
        "state": "included", "origin": "user_added", "source_refs": []}}
    body = {"expected_version": 1, "name": "Pilot rollout", "changes": _changes(
        overrides=[
            {"node_id": "Task_Review", "item_id": "Task_Review::DIA_Budget::DOR_Budget", "state": "excluded",
             "reason": "Not used in pilot"},
            {"node_id": "Task_Review", "item_id": "Task_Review::DIA_Request::DOR_Request",
             "name": "Purchase request form", "description": "PR-01"}],
        added=[added])}
    r = c.put(f"/api/v1/variants/{v['id']}", json=body)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["variant"]["version"] == 2
    log = saved["variant"]["change_log"][-1]["summary"]
    assert any("Tailored out 'Budget reference'" in s for s in log)
    assert any("Renamed 'Purchase request' to 'Purchase request form'" in s for s in log)
    assert any("Added output 'Review checklist'" in s for s in log)

    # ---- full restart: brand-new app instance on the same data directory
    c2 = make_client()
    got = c2.get(f"/api/v1/variants/{v['id']}").json()
    assert got["variant"]["version"] == 2 and got["baseline_unchanged"] is True
    node = next(n for n in got["resolved"]["nodes"] if n["id"] == "Task_Review")
    ins = {i["id"]: i for i in node["inputs"]}
    assert ins["Task_Review::DIA_Budget::DOR_Budget"]["state"] == "excluded"
    assert ins["Task_Review::DIA_Request::DOR_Request"]["name"] == "Purchase request form"
    assert ins["Task_Review::DIA_Request::DOR_Request"]["edited_from_id"] == "Task_Review::DIA_Request::DOR_Request"
    assert [o["name"] for o in node["outputs"]] == ["Review result", "Review checklist"]
    assert node["outputs"][1]["origin"] == "user_added"

    # ---- baseline unchanged (FR-22): same hash, same items, original names
    base_after = c2.get(f"/api/v1/processes/{pid}").json()
    assert base_after["baseline_sha256"] == base_before["baseline_sha256"]
    bnode = next(n for n in base_after["baseline"]["nodes"] if n["id"] == "Task_Review")
    assert [(i["name"], i["state"]) for i in bnode["inputs"]] == [("Purchase request", "included"),
                                                                  ("Budget reference", "included")]
    src = c2.get(f"/api/v1/imports/{rec['id']}/source").content
    assert hashlib.sha256(src).hexdigest() == rec["sha256"]

    # ---- restore (FR-20): drop the overrides; item is back to baseline
    restore = {"expected_version": 2, "name": "Pilot rollout",
               "changes": _changes(added=[added])}
    r = c2.put(f"/api/v1/variants/{v['id']}", json=restore).json()
    node = next(n for n in r["resolved"]["nodes"] if n["id"] == "Task_Review")
    assert [(i["name"], i["state"]) for i in node["inputs"]] == [("Purchase request", "included"),
                                                                ("Budget reference", "included")]
    assert any("Restored 'Budget reference'" in s for s in r["variant"]["change_log"][-1]["summary"])


def test_nfr17_stale_write_rejected(setup):
    c, _, pid = setup
    v = c.post(f"/api/v1/processes/{pid}/variants", json={"name": "A"}).json()
    ok = c.put(f"/api/v1/variants/{v['id']}", json={"expected_version": 1, "name": "A", "changes": _changes()})
    assert ok.status_code == 200
    stale = c.put(f"/api/v1/variants/{v['id']}", json={"expected_version": 1, "name": "B", "changes": _changes()})
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_VERSION"
    assert stale.json()["details"]["current_version"] == 2
    assert c.get(f"/api/v1/variants/{v['id']}").json()["variant"]["name"] == "A"


@pytest.mark.parametrize("changes,fragment", [
    (_changes(overrides=[{"node_id": "Task_Review", "item_id": "nope", "state": "excluded"}]), "unknown baseline item"),
    (_changes(added=[{"node_id": "Nope", "item": {"id": "u", "name": "x", "direction": "input", "origin": "user_added"}}]), "unknown node"),
    (_changes(added=[{"node_id": "Task_Review", "item": {"id": "u", "name": "x", "direction": "input", "origin": "source_explicit"}}]), "user_added"),
    (_changes(added=[{"node_id": "Task_Review", "item": {"id": "u", "name": "  ", "direction": "input", "origin": "user_added"}}]), "empty"),
])
def test_invalid_changes_rejected(setup, changes, fragment):
    c, _, pid = setup
    v = c.post(f"/api/v1/processes/{pid}/variants", json={"name": "A"}).json()
    r = c.put(f"/api/v1/variants/{v['id']}", json={"expected_version": 1, "name": "A", "changes": changes})
    assert r.status_code == 422 and fragment in r.json()["message"]


def test_baseline_table_is_immutable(setup, settings):
    c, _, pid = setup
    db = sqlite3.connect(settings.data_dir / "processflow.sqlite3")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE baselines SET graph_json='{}' WHERE process_id=?", (pid,))
    db.close()


def test_t25_export_contains_provenance_and_validates(setup):
    c, rec, pid = setup
    v = c.post(f"/api/v1/processes/{pid}/variants", json={"name": "Exported"}).json()
    c.put(f"/api/v1/variants/{v['id']}", json={"expected_version": 1, "name": "Exported", "changes": _changes(
        overrides=[{"node_id": "Task_Order", "item_id": "Task_Order::DOA_Ledger::DSR_Ledger", "state": "excluded"}])})
    r = c.get(f"/api/v1/variants/{v['id']}/export")
    assert r.headers["content-disposition"].startswith("attachment")
    data = r.json()
    assert data["schema_version"] == "0.2" and data["source"]["sha256"] == rec["sha256"]
    assert data["variant"]["changes"]["overrides"][0]["state"] == "excluded"
    assert data["baseline_unchanged"] is True
    assert "not BPMN XML" in data["note"]
    item = data["baseline"]["nodes"][next(i for i, n in enumerate(data["baseline"]["nodes"]) if n["id"] == "Task_Order")]["outputs"][0]
    assert item["source_refs"][0]["type"] == "bpmn_xml" and item["source_refs"][0]["line"]
    schema = json.loads((ROOT / "schema" / "process-graph.schema.json").read_text())
    jsonschema.validate(data["baseline"], schema)
    jsonschema.validate(data["resolved"], schema)


def test_schema_file_matches_models():
    from app.models import ProcessGraph
    on_disk = json.loads((ROOT / "schema" / "process-graph.schema.json").read_text())
    assert on_disk == ProcessGraph.model_json_schema(), "run: python scripts/export_schema.py"


def test_source_file_written_once_and_read_only(setup, settings):
    _, rec, _ = setup
    p = Path(settings.data_dir) / "sources" / f"{rec['sha256']}.bpmn"
    assert p.read_bytes() == fixture_bytes("bpmn/purchase-with-data.bpmn")
