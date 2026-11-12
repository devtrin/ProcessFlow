"""FR-06/07/08/23, NFR-01/09: inference boundary, grounding and review gate.

No real model is available in the test environment. The FixtureAdapter replays
hand-written candidate JSON so that validation and grounding are tested
deterministically; this is NOT evidence of real extraction quality.
"""

from __future__ import annotations

import json
import socket

import httpx
import pytest

from app.ai.anthropic_adapter import AnthropicAdapter
from app.ai.base import CANDIDATE_JSON_SCHEMA, InferenceError, InferencePrompt
from app.ai.ollama_adapter import OllamaAdapter
from app.ai.prompt import SYSTEM_PROMPT, build_prompt
from app.ingest.pdf_inference import infer_process
from app.ingest.pdf_reader import extract_pdf

from .conftest import FixtureAdapter, fixture_bytes, upload


@pytest.fixture(scope="module")
def pdf():
    return extract_pdf(fixture_bytes("handoff/example-process-narrative.pdf"), max_pages=100)


def run(adapter, pdf):
    return infer_process(adapter, pdf, filename="n.pdf", sha256="a" * 64, document_id="imp", process_id="imp__ai1",
                         max_chars=200_000)


def issue_codes(g):
    return [i.code for i in g.issues]


# ---------------------------------------------------------------- provider status / gating


def test_provider_unconfigured_is_explicit_and_makes_no_calls(client, monkeypatch):
    def deny(*a, **k):
        raise AssertionError("outbound connection attempted")
    monkeypatch.setattr(socket.socket, "connect", deny)
    st = client.get("/api/v1/provider").json()
    assert st["provider"] == "none" and st["configured"] is False and st["ready"] is False
    assert "unavailable" in st["message"]
    rec = upload(client, "handoff/example-process-narrative.pdf").json()
    r = client.post(f"/api/v1/imports/{rec['id']}/infer", json={})
    assert r.status_code == 409 and r.json()["code"] == "PROVIDER_NOT_CONFIGURED"
    after = client.get(f"/api/v1/imports/{rec['id']}").json()
    assert after["processes"] == [] and after["status"] == "extracted"


def test_anthropic_requires_explicit_remote_enablement(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    ready, why = AnthropicAdapter(model="claude-opus-5-5", allow_remote=False).readiness()
    assert not ready and "PF_ALLOW_REMOTE_INFERENCE" in why
    with pytest.raises(InferenceError) as e:
        AnthropicAdapter(model="claude-opus-5-5", allow_remote=False).infer(InferencePrompt(system="s", user="u"))
    assert e.value.code == "PROVIDER_NOT_READY"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    ready, why = AnthropicAdapter(model="claude-opus-5-5", allow_remote=True).readiness()
    assert not ready and "credentials" in why
    assert "test-not-a-real-key" not in why


def test_anthropic_request_uses_structured_output():
    req = AnthropicAdapter(model="claude-opus-5-5", allow_remote=True).build_request(
        InferencePrompt(system=SYSTEM_PROMPT, user="doc text"))
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"]["format"] == {"type": "json_schema", "schema": CANDIDATE_JSON_SCHEMA}
    assert req["thinking"] == {"type": "adaptive"}
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert "temperature" not in req


def test_anthropic_adapter_with_fake_client_parses_text_and_refusal():
    honest = (fixture_bytes("inference/purchase-honest.json")).decode()

    class Block:
        type = "text"
        def __init__(self, t): self.text = t

    class Resp:
        def __init__(self, stop, text):
            self.stop_reason, self.content, self.model = stop, [Block(text)], "claude-opus-5-5"
            self.usage = type("U", (), {"input_tokens": 1, "output_tokens": 2})()

    class FakeClient:
        def __init__(self, resp): self.beta = type("B", (), {"messages": self})(); self._r = resp
        def create(self, **kw): self.kw = kw; return self._r

    ok = AnthropicAdapter(model="claude-opus-5-5", allow_remote=True, client=FakeClient(Resp("end_turn", honest)))
    assert json.loads(ok.infer(InferencePrompt(system="s", user="u")).raw_text)["steps"][0]["id"] == "S1"
    refused = AnthropicAdapter(model="claude-opus-5-5", allow_remote=True, client=FakeClient(Resp("refusal", "")))
    with pytest.raises(InferenceError) as e:
        refused.infer(InferencePrompt(system="s", user="u"))
    assert e.value.code == "PROVIDER_REFUSED"


def test_remote_provider_needs_per_request_acknowledgement(make_client):
    c = make_client(adapter=FixtureAdapter("purchase-honest.json", remote=True))
    rec = upload(c, "handoff/example-process-narrative.pdf").json()
    r = c.post(f"/api/v1/imports/{rec['id']}/infer", json={})
    assert r.status_code == 409 and r.json()["code"] == "REMOTE_ACK_REQUIRED"
    r = c.post(f"/api/v1/imports/{rec['id']}/infer", json={"acknowledge_remote": True})
    assert r.status_code == 200 and r.json()["status"] == "needs_review"


def test_ollama_adapter_payload_and_locality(pdf):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "llama-test", "message": {"content": fixture_bytes(
            "inference/purchase-honest.json").decode()}})

    ad = OllamaAdapter(base_url="http://127.0.0.1:11434", model="llama-test", allow_remote=False,
                       transport=httpx.MockTransport(handler))
    assert ad.is_remote is False and ad.readiness()[0]
    g = run(ad, pdf)
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert seen["body"]["format"] == CANDIDATE_JSON_SCHEMA and seen["body"]["stream"] is False
    assert len(g.nodes) == 6
    remote = OllamaAdapter(base_url="http://models.example.com:11434", model="m", allow_remote=False)
    assert remote.is_remote and not remote.readiness()[0]


def test_ollama_unreachable_is_reported(pdf):
    def handler(request):
        raise httpx.ConnectError("refused")
    ad = OllamaAdapter(base_url="http://127.0.0.1:9", model="m", allow_remote=False, transport=httpx.MockTransport(handler))
    with pytest.raises(InferenceError) as e:
        run(ad, pdf)
    assert e.value.code == "PROVIDER_UNREACHABLE"


# ---------------------------------------------------------------- prompt and schema


def test_prompt_contains_page_anchors_and_constraints(pdf):
    p = build_prompt(pdf, "n.pdf")
    assert "[page 1 | p1b3] 2. The manager reviews" in p.user
    for rule in ("not_stated", "character-for-character", "do not add that step"):
        assert rule in p.system
    assert CANDIDATE_JSON_SCHEMA["additionalProperties"] is False
    step = CANDIDATE_JSON_SCHEMA["properties"]["steps"]["items"]
    assert set(step["required"]) == {"id", "kind", "label", "actor", "evidence", "inputs", "outputs"}


def test_structured_output_validation_reprompts_once_then_fails(pdf):
    ad = FixtureAdapter("invalid-shape.json", "invalid-shape.json")
    with pytest.raises(InferenceError) as e:
        run(ad, pdf)
    assert e.value.code == "MODEL_OUTPUT_INVALID" and len(ad.calls) == 2
    assert ad.calls[1].retry_feedback and "steps" in ad.calls[1].retry_feedback


def test_reprompt_recovers_when_second_answer_valid(pdf):
    ad = FixtureAdapter("not json at all", "purchase-honest.json")
    g = run(ad, pdf)
    assert len(ad.calls) == 2 and g.provenance["inference"]["attempts"][0]["valid"] is False


def test_no_steps_is_an_error_not_an_empty_graph(pdf):
    with pytest.raises(InferenceError) as e:
        run(FixtureAdapter("no-steps.json"), pdf)
    assert e.value.code == "NO_PROCESS_FOUND"


# ---------------------------------------------------------------- grounding / review gate


def test_t06_t07_honest_candidate_becomes_reviewable_draft(pdf):
    g = run(FixtureAdapter("purchase-honest.json"), pdf)
    assert g.view == "pdf_draft" and g.review_state == "needs_review"
    assert all(n.review_state.value == "needs_review" for n in g.nodes)
    assert all(e.kind.value == "inferred_sequence" for e in g.edges)
    for n in g.nodes:
        assert n.source_refs and all(r.grounding in ("exact", "normalised") and r.page == 1 for r in n.source_refs)
    review = next(n for n in g.nodes if n.id == "S2")
    assert [i.name for i in review.inputs] == ["Purchase request", "Budget reference"]
    assert review.inputs[0].origin.value == "ai_inferred"
    assert review.source_refs[0].bbox and review.source_refs[0].block_id == "p1b3"
    decision = next(n for n in g.nodes if n.id == "S3")
    assert decision.kind.value == "exclusive_gateway" and decision.actor == "not_stated"
    assert {e.label for e in g.edges if e.source == "S3"} == {"Yes", "No"}
    assert "FABRICATED_EVIDENCE" not in issue_codes(g)


def test_ambiguity_is_flagged_not_invented(pdf):
    g = run(FixtureAdapter("purchase-honest.json"), pdf)
    amb = [i for i in g.issues if i.code == "AMBIGUITY"]
    assert len(amb) == 1 and amb[0].element_ids == ["S2"]
    assert amb[0].source_refs[0].grounding == "exact"
    assert not any("another review" in n.label.lower() for n in g.nodes)
    assert "CYCLE_PRESENT" not in issue_codes(g) and "INFERRED_LOOP" not in issue_codes(g)


def test_t08_fabricated_and_wrong_page_evidence_raise_issues(pdf):
    g = run(FixtureAdapter("purchase-fabricated.json"), pdf)
    fab = [i for i in g.issues if i.code == "FABRICATED_EVIDENCE"]
    assert fab and fab[0].severity.value == "error" and fab[0].element_ids == ["S2"]
    s2 = next(n for n in g.nodes if n.id == "S2")
    assert s2.source_refs[0].grounding == "not_found" and s2.review_state.value == "needs_review"
    wrong = [i for i in g.issues if i.code == "EVIDENCE_WRONG_PAGE"]
    assert wrong and wrong[0].element_ids == ["S4"]


def test_invented_loop_is_flagged(pdf):
    g = run(FixtureAdapter("purchase-invented-loop.json"), pdf)
    codes = issue_codes(g)
    assert "INFERRED_LOOP" in codes and "CYCLE_PRESENT" in codes


def test_d23_review_decisions_persist_and_are_separate_from_baseline(make_client):
    c = make_client(adapter=FixtureAdapter("purchase-honest.json"))
    rec = upload(c, "handoff/example-process-narrative.pdf").json()
    out = c.post(f"/api/v1/imports/{rec['id']}/infer", json={}).json()
    pid = out["process_ids"][0]
    assert out["status"] == "needs_review" and out["inference"]["state"] == "completed"
    before = c.get(f"/api/v1/processes/{pid}").json()
    r = c.post(f"/api/v1/processes/{pid}/reviews", json={"element_id": "S2", "state": "verified", "note": "matches p1"})
    assert r.status_code == 200
    r = c.post(f"/api/v1/processes/{pid}/reviews", json={"element_id": "nope", "state": "verified"})
    assert r.status_code == 404
    c2 = make_client()  # restart
    after = c2.get(f"/api/v1/processes/{pid}").json()
    assert after["baseline_sha256"] == before["baseline_sha256"]
    assert after["baseline"]["nodes"][1]["review_state"] == "needs_review"  # baseline untouched
    assert [(x["element_id"], x["state"]) for x in after["reviews"]] == [("S2", "verified")]


def test_inference_failure_keeps_import_usable(make_client):
    c = make_client(adapter=FixtureAdapter("invalid-shape.json"))
    rec = upload(c, "handoff/example-process-narrative.pdf").json()
    r = c.post(f"/api/v1/imports/{rec['id']}/infer", json={})
    assert r.status_code == 422 and r.json()["code"] == "MODEL_OUTPUT_INVALID"
    after = c.get(f"/api/v1/imports/{rec['id']}").json()
    assert after["status"] == "extracted" and after["inference"]["state"] == "failed"
    assert after["processes"] == []
