"""Local model adapter via Ollama's native /api/chat with a JSON-schema ``format``.

Local-first: with the default base URL (127.0.0.1) no document text leaves the
machine. A non-local base URL is treated as remote and needs
PF_ALLOW_REMOTE_INFERENCE=true like any other remote provider.
"""

from __future__ import annotations

import httpx

from ..config import is_local_url
from .base import InferenceError, InferencePrompt, InferenceResult, ProcessInferenceAdapter


class OllamaAdapter(ProcessInferenceAdapter):
    name = "ollama"

    def __init__(self, *, base_url: str, model: str, allow_remote: bool, timeout_s: float = 300.0,
                 transport: httpx.BaseTransport | None = None):
        self._base = base_url.rstrip("/")
        self._model = model
        self._allow_remote = allow_remote
        self._timeout = timeout_s
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    @property
    def is_remote(self) -> bool:
        return not is_local_url(self._base)

    def readiness(self) -> tuple[bool, str]:
        if not self._model:
            return False, "Ollama is selected but PF_OLLAMA_MODEL is empty."
        if self.is_remote and not self._allow_remote:
            return False, (f"PF_OLLAMA_BASE_URL points to a non-local host; set PF_ALLOW_REMOTE_INFERENCE=true "
                           "to allow PDF text to be sent there.")
        where = "remote host" if self.is_remote else "this machine"
        return True, f"Configured: Ollama model '{self._model}' on {where} ({self._base}). Reachability is checked on use."

    def build_payload(self, prompt: InferencePrompt) -> dict:
        messages = [{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}]
        if prompt.retry_feedback and prompt.previous_output:
            messages += [{"role": "assistant", "content": prompt.previous_output},
                         {"role": "user", "content": prompt.retry_feedback}]
        return {"model": self._model, "messages": messages, "format": prompt.schema, "stream": False,
                "options": {"temperature": 0}}

    def infer(self, prompt: InferencePrompt) -> InferenceResult:
        ready, why = self.readiness()
        if not ready:
            raise InferenceError("PROVIDER_NOT_READY", why)
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport, trust_env=self.is_remote) as c:
                r = c.post(f"{self._base}/api/chat", json=self.build_payload(prompt))
        except httpx.ConnectError:
            raise InferenceError("PROVIDER_UNREACHABLE",
                                 f"Could not connect to Ollama at {self._base}. Is 'ollama serve' running?",
                                 retryable=True) from None
        except httpx.TimeoutException:
            raise InferenceError("PROVIDER_TIMEOUT", "The local model did not answer in time.", retryable=True) from None
        if r.status_code == 404:
            raise InferenceError("PROVIDER_MODEL_MISSING",
                                 f"Ollama has no model '{self._model}'. Run 'ollama pull {self._model}'.")
        if r.status_code >= 400:
            raise InferenceError("PROVIDER_HTTP_ERROR", f"Ollama returned HTTP {r.status_code}.")
        try:
            body = r.json()
            text = body["message"]["content"]
        except (ValueError, KeyError, TypeError):
            raise InferenceError("PROVIDER_BAD_RESPONSE", "Ollama returned an unexpected response shape.") from None
        return InferenceResult(raw_text=text, provider=self.name, model=body.get("model", self._model),
                               usage={"input_tokens": body.get("prompt_eval_count"),
                                      "output_tokens": body.get("eval_count")})
