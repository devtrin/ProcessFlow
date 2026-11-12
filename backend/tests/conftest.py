from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.base import InferencePrompt, InferenceResult, ProcessInferenceAdapter
from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "fixtures"


def fixture_bytes(rel: str) -> bytes:
    return (FIX / rel).read_bytes()


class FixtureAdapter(ProcessInferenceAdapter):
    """TEST ONLY. Replays hand-written candidate JSON files from fixtures/inference.

    It is never registered in the application; it exists to test the grounding,
    validation and review pipeline deterministically without a model.
    """

    name = "test-fixture"

    def __init__(self, *responses: str, remote: bool = False):
        self._responses = list(responses)
        self.calls: list[InferencePrompt] = []
        self._remote = remote

    @property
    def model(self) -> str:
        return "fixture-replay"

    @property
    def is_remote(self) -> bool:
        return self._remote

    def readiness(self):
        return True, "test fixture adapter"

    def infer(self, prompt: InferencePrompt) -> InferenceResult:
        self.calls.append(prompt)
        name = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        raw = (FIX / "inference" / name).read_text() if name.endswith(".json") else name
        return InferenceResult(raw_text=raw, provider=self.name, model=self.model)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def make_client(settings):
    def _make(adapter=None, **overrides) -> TestClient:
        s = replace(settings, **overrides) if overrides else settings
        return TestClient(create_app(s, adapter=adapter))
    return _make


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def upload(client: TestClient, rel_or_name: str, data: bytes | None = None):
    name = Path(rel_or_name).name
    body = data if data is not None else fixture_bytes(rel_or_name)
    return client.post("/api/v1/imports", files={"file": (name, body, "application/octet-stream")})


def load_json(rel: str):
    return json.loads((FIX / rel).read_text())
