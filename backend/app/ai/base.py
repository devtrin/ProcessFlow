"""Provider-agnostic inference boundary for PDF narrative -> candidate process.

The model never produces the canonical graph directly. It returns a
``CandidateProcess`` (validated here), which ``app.ingest.pdf_inference`` then
grounds against the extracted PDF text and converts into a ProcessGraph whose
every element is ``needs_review``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InferenceError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


# ---------------------------------------------------------------- candidate contract


class _C(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_C):
    page: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=600)


class CandidateItem(_C):
    name: str = Field(min_length=1, max_length=200)
    evidence: list[Evidence]


class CandidateStep(_C):
    id: str = Field(min_length=1, max_length=40)
    kind: Literal["start_event", "end_event", "intermediate_event", "task", "decision", "subprocess"]
    label: str = Field(min_length=1, max_length=200)
    actor: str = Field(max_length=120)  # "not_stated" when the text does not say
    evidence: list[Evidence]
    inputs: list[CandidateItem]
    outputs: list[CandidateItem]


class CandidateFlow(_C):
    id: str = Field(min_length=1, max_length=40)
    source: str
    target: str
    condition: str = Field(max_length=300)  # "none" (unconditional) or "not_stated" or the stated condition
    evidence: list[Evidence]


class CandidateUncertainty(_C):
    description: str = Field(min_length=1, max_length=600)
    related_step_ids: list[str]
    evidence: list[Evidence]


class CandidateProcess(_C):
    process_title: str = Field(max_length=200)
    steps: list[CandidateStep]
    flows: list[CandidateFlow]
    uncertainties: list[CandidateUncertainty]


def _strictify(schema: dict) -> dict:
    """Inline $defs and make every object closed with all properties required,
    which is what JSON-schema constrained decoders accept."""
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            out = {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
            if out.get("type") == "object":
                out["additionalProperties"] = False
                out["required"] = list(out.get("properties", {}).keys())
            # Length/range constraints are enforced by Pydantic after decoding.
            for k in ("minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum"):
                out.pop(k, None)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


CANDIDATE_JSON_SCHEMA: dict = _strictify(CandidateProcess.model_json_schema())


# ---------------------------------------------------------------- adapter interface


@dataclass
class InferencePrompt:
    system: str
    user: str
    schema: dict = field(default_factory=lambda: CANDIDATE_JSON_SCHEMA)
    # Earlier attempt + validation feedback for one bounded re-prompt.
    retry_feedback: str | None = None
    previous_output: str | None = None


@dataclass
class InferenceResult:
    raw_text: str
    provider: str
    model: str
    usage: dict = field(default_factory=dict)


class ProcessInferenceAdapter(ABC):
    name: str = "abstract"

    @property
    @abstractmethod
    def model(self) -> str: ...

    @property
    @abstractmethod
    def is_remote(self) -> bool:
        """True when document text leaves this machine."""

    @abstractmethod
    def readiness(self) -> tuple[bool, str]:
        """(ready, human-readable reason). Must not make network calls or reveal secrets."""

    @abstractmethod
    def infer(self, prompt: InferencePrompt) -> InferenceResult: ...
