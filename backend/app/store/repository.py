"""SQLite + app-owned directory persistence.

* Source files are written once (temp file + atomic rename) under
  ``<data_dir>/sources/<sha256>.<ext>`` and never modified.
* Baselines are immutable: a SQLite trigger aborts any UPDATE of the
  ``baselines`` table, and each row stores the SHA-256 of its canonical JSON.
* Variant saves are optimistic: ``UPDATE ... WHERE version = expected``; a
  stale version raises ``StaleVersion`` (HTTP 409), never last-write-wins.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..models import (
    ChangeLogEntry,
    ImportRecord,
    ProcessGraph,
    ReviewDecision,
    Variant,
    VariantChanges,
    utcnow,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, record_json TEXT NOT NULL, source_path TEXT
);
CREATE TABLE IF NOT EXISTS extractions (import_id TEXT PRIMARY KEY REFERENCES imports(id), json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS baselines (
    process_id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES imports(id),
    graph_json TEXT NOT NULL, graph_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS baselines_immutable BEFORE UPDATE ON baselines
BEGIN SELECT RAISE(ABORT, 'baselines are immutable'); END;
CREATE TABLE IF NOT EXISTS variants (
    id TEXT PRIMARY KEY, process_id TEXT NOT NULL REFERENCES baselines(process_id),
    name TEXT NOT NULL, version INTEGER NOT NULL, json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    process_id TEXT NOT NULL, element_id TEXT NOT NULL, state TEXT NOT NULL, note TEXT NOT NULL, at TEXT NOT NULL,
    PRIMARY KEY (process_id, element_id)
);
CREATE TABLE IF NOT EXISTS review_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, process_id TEXT NOT NULL, element_id TEXT NOT NULL,
    state TEXT NOT NULL, note TEXT NOT NULL, at TEXT NOT NULL
);
"""
DB_SCHEMA_VERSION = "1"


class NotFound(Exception):
    pass


class StaleVersion(Exception):
    def __init__(self, current: int):
        super().__init__(f"stale version; current is {current}")
        self.current = current


def canonical_json(model) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Repository:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.sources_dir = data_dir / "sources"
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "processflow.sqlite3"
        self._lock = threading.RLock()
        with self._conn() as c:
            c.executescript(SCHEMA)
            c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('db_schema_version', ?)", (DB_SCHEMA_VERSION,))

    @contextmanager
    def _conn(self):
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")
            try:
                yield conn
            finally:
                conn.close()

    @contextmanager
    def _tx(self):
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------- sources
    def store_source(self, data: bytes, sha256: str, ext: str) -> Path:
        dest = self.sources_dir / f"{sha256}{ext}"
        if dest.exists():
            return dest
        fd, tmp = tempfile.mkstemp(dir=self.sources_dir, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        try:
            os.chmod(dest, 0o444)
        except OSError:
            pass
        return dest

    def source_bytes(self, import_id: str) -> tuple[bytes, ImportRecord]:
        with self._conn() as c:
            row = c.execute("SELECT record_json, source_path FROM imports WHERE id=?", (import_id,)).fetchone()
        if not row or not row["source_path"]:
            raise NotFound(import_id)
        return Path(row["source_path"]).read_bytes(), ImportRecord.model_validate_json(row["record_json"])

    # ------------------------------------------------------------- imports
    def save_import(self, rec: ImportRecord, source_path: Path | None = None) -> None:
        rec.updated_at = utcnow()
        with self._tx() as c:
            c.execute(
                "INSERT INTO imports(id, created_at, record_json, source_path) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET record_json=excluded.record_json, "
                "source_path=COALESCE(excluded.source_path, imports.source_path)",
                (rec.id, rec.created_at, rec.model_dump_json(), str(source_path) if source_path else None),
            )

    def import_with_baselines(self, rec: ImportRecord, graphs: list[ProcessGraph], source_path: Path | None,
                              extraction: dict | None = None) -> None:
        """Single transaction: either the import and all its baselines exist, or none do."""
        rec.updated_at = utcnow()
        with self._tx() as c:
            c.execute(
                "INSERT INTO imports(id, created_at, record_json, source_path) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET record_json=excluded.record_json, "
                "source_path=COALESCE(excluded.source_path, imports.source_path)",
                (rec.id, rec.created_at, rec.model_dump_json(), str(source_path) if source_path else None))
            if extraction is not None:
                c.execute("INSERT OR REPLACE INTO extractions(import_id, json) VALUES (?,?)",
                          (rec.id, json.dumps(extraction)))
            for g in graphs:
                cj = canonical_json(g)
                c.execute("INSERT INTO baselines(process_id, import_id, graph_json, graph_sha256, created_at) "
                          "VALUES (?,?,?,?,?)", (g.process_id, rec.id, cj, sha256_text(cj), utcnow()))

    def get_import(self, import_id: str) -> ImportRecord:
        with self._conn() as c:
            row = c.execute("SELECT record_json FROM imports WHERE id=?", (import_id,)).fetchone()
        if not row:
            raise NotFound(import_id)
        return ImportRecord.model_validate_json(row["record_json"])

    def list_imports(self) -> list[ImportRecord]:
        with self._conn() as c:
            rows = c.execute("SELECT record_json FROM imports ORDER BY created_at DESC, rowid DESC").fetchall()
        return [ImportRecord.model_validate_json(r["record_json"]) for r in rows]

    def delete_failed_import(self, import_id: str) -> None:
        rec = self.get_import(import_id)
        if rec.status.value != "failed" or rec.process_ids:
            raise ValueError("Only failed imports without processes can be removed.")
        with self._tx() as c:
            c.execute("DELETE FROM extractions WHERE import_id=?", (import_id,))
            c.execute("DELETE FROM imports WHERE id=?", (import_id,))

    def get_extraction(self, import_id: str) -> dict:
        with self._conn() as c:
            row = c.execute("SELECT json FROM extractions WHERE import_id=?", (import_id,)).fetchone()
        if not row:
            raise NotFound(import_id)
        return json.loads(row["json"])

    # ------------------------------------------------------------- baselines
    def get_baseline(self, process_id: str) -> tuple[ProcessGraph, str]:
        with self._conn() as c:
            row = c.execute("SELECT graph_json, graph_sha256 FROM baselines WHERE process_id=?",
                            (process_id,)).fetchone()
        if not row:
            raise NotFound(process_id)
        if sha256_text(row["graph_json"]) != row["graph_sha256"]:
            raise RuntimeError(f"Baseline {process_id} failed its integrity check.")
        return ProcessGraph.model_validate_json(row["graph_json"]), row["graph_sha256"]

    def list_baselines(self, import_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT process_id, graph_json, graph_sha256 FROM baselines WHERE import_id=? "
                             "ORDER BY rowid", (import_id,)).fetchall()
        out = []
        for r in rows:
            g = json.loads(r["graph_json"])
            out.append({"process_id": r["process_id"], "title": g["title"], "view": g["view"],
                        "node_count": len(g["nodes"]), "edge_count": len(g["edges"]),
                        "issue_count": len(g["issues"]), "baseline_sha256": r["graph_sha256"]})
        return out

    # ------------------------------------------------------------- variants
    def create_variant(self, process_id: str, name: str) -> Variant:
        graph, gsha = self.get_baseline(process_id)
        now = utcnow()
        v = Variant(id=new_id("var"), process_id=process_id, name=name, version=1, baseline_sha256=gsha,
                    source_sha256=graph.source.sha256, changes=VariantChanges(),
                    change_log=[ChangeLogEntry(version=1, at=now, summary=["Created from baseline"])],
                    created_at=now, updated_at=now)
        with self._tx() as c:
            c.execute("INSERT INTO variants(id, process_id, name, version, json, created_at, updated_at) "
                      "VALUES (?,?,?,?,?,?,?)", (v.id, process_id, name, 1, v.model_dump_json(), now, now))
        return v

    def get_variant(self, variant_id: str) -> Variant:
        with self._conn() as c:
            row = c.execute("SELECT json FROM variants WHERE id=?", (variant_id,)).fetchone()
        if not row:
            raise NotFound(variant_id)
        return Variant.model_validate_json(row["json"])

    def list_variants(self, process_id: str) -> list[Variant]:
        with self._conn() as c:
            rows = c.execute("SELECT json FROM variants WHERE process_id=? ORDER BY created_at, rowid",
                             (process_id,)).fetchall()
        return [Variant.model_validate_json(r["json"]) for r in rows]

    def save_variant(self, variant_id: str, *, expected_version: int, name: str, changes: VariantChanges,
                     summary: list[str]) -> Variant:
        with self._tx() as c:
            row = c.execute("SELECT json, version FROM variants WHERE id=?", (variant_id,)).fetchone()
            if not row:
                raise NotFound(variant_id)
            if row["version"] != expected_version:
                raise StaleVersion(row["version"])
            v = Variant.model_validate_json(row["json"])
            now = utcnow()
            v.version = expected_version + 1
            v.name = name
            v.changes = changes
            v.updated_at = now
            v.change_log.append(ChangeLogEntry(version=v.version, at=now, summary=summary))
            cur = c.execute("UPDATE variants SET name=?, version=?, json=?, updated_at=? WHERE id=? AND version=?",
                            (name, v.version, v.model_dump_json(), now, variant_id, expected_version))
            if cur.rowcount != 1:  # pragma: no cover - guarded by BEGIN IMMEDIATE
                raise StaleVersion(row["version"])
        return v

    # ------------------------------------------------------------- reviews
    def set_review(self, d: ReviewDecision) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO reviews(process_id, element_id, state, note, at) VALUES (?,?,?,?,?) "
                      "ON CONFLICT(process_id, element_id) DO UPDATE SET state=excluded.state, "
                      "note=excluded.note, at=excluded.at",
                      (d.process_id, d.element_id, d.state.value, d.note, d.at))
            c.execute("INSERT INTO review_history(process_id, element_id, state, note, at) VALUES (?,?,?,?,?)",
                      (d.process_id, d.element_id, d.state.value, d.note, d.at))

    def list_reviews(self, process_id: str) -> list[ReviewDecision]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM reviews WHERE process_id=?", (process_id,)).fetchall()
        return [ReviewDecision(process_id=r["process_id"], element_id=r["element_id"], state=r["state"],
                               note=r["note"], at=r["at"]) for r in rows]
