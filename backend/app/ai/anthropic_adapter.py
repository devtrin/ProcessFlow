"""Claude adapter (remote). Disabled unless PF_INFERENCE_PROVIDER=anthropic AND
PF_ALLOW_REMOTE_INFERENCE=true AND credentials exist in the environment.

Uses structured outputs (``output_config.format`` with a JSON schema) so the
response is schema-shaped JSON, which is then validated again with Pydantic.
"""

from __future__ import annotations

import os

from .base import InferenceError, InferencePrompt, InferenceResult, ProcessInferenceAdapter


class AnthropicAdapter(ProcessInferenceAdapter):
    name = "anthropic"

    def __init__(self, *, model: str, allow_remote: bool, fallbacks: str = "default", effort: str = "high",
                 timeout_s: float = 300.0, client=None):
        self._model = model
        self._allow_remote = allow_remote
        self._fallbacks = fallbacks
        self._effort = effort
        self._timeout = timeout_s
        self._client = client

    @property
    def model(self) -> str:
        return self._model

    @property
    def is_remote(self) -> bool:
        return True

    def readiness(self) -> tuple[bool, str]:
        if not self._allow_remote:
            return False, ("Anthropic is selected but remote inference is not enabled. Set "
                           "PF_ALLOW_REMOTE_INFERENCE=true to allow PDF text to be sent to the Anthropic API.")
        if self._client is None and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return False, "No Anthropic credentials found (set ANTHROPIC_API_KEY in the backend environment)."
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "The 'anthropic' Python package is not installed."
        return True, f"Ready: Anthropic API, model {self._model}. PDF text will leave this machine."

    def build_request(self, prompt: InferencePrompt) -> dict:
        messages = [{"role": "user", "content": prompt.user}]
        if prompt.retry_feedback and prompt.previous_output:
            messages += [
                {"role": "assistant", "content": prompt.previous_output},
                {"role": "user", "content": prompt.retry_feedback},
            ]
        req: dict = {
            "model": self._model,
            "max_tokens": 16000,
            "system": prompt.system,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._effort,
                              "format": {"type": "json_schema", "schema": prompt.schema}},
        }
        if self._fallbacks == "default":
            req["betas"] = ["server-side-fallback-2026-07-01"]
            req["fallbacks"] = "default"
        return req

    def infer(self, prompt: InferencePrompt) -> InferenceResult:
        ready, why = self.readiness()
        if not ready:
            raise InferenceError("PROVIDER_NOT_READY", why)
        import anthropic

        client = self._client or anthropic.Anthropic(timeout=self._timeout, max_retries=2)
        req = self.build_request(prompt)
        try:
            if "betas" in req:
                resp = client.beta.messages.create(**req)
            else:
                resp = client.messages.create(**req)
        except anthropic.AuthenticationError:
            raise InferenceError("PROVIDER_AUTH", "The Anthropic API rejected the credentials.") from None
        except anthropic.RateLimitError:
            raise InferenceError("PROVIDER_RATE_LIMIT", "The Anthropic API rate limit was reached; try again later.",
                                 retryable=True) from None
        except anthropic.APIStatusError as exc:
            raise InferenceError("PROVIDER_HTTP_ERROR", f"The Anthropic API returned HTTP {exc.status_code}.") from None
        except anthropic.APIConnectionError:
            raise InferenceError("PROVIDER_UNREACHABLE", "Could not connect to the Anthropic API.",
                                 retryable=True) from None
        if resp.stop_reason == "refusal":
            raise InferenceError("PROVIDER_REFUSED", "The model declined this request; no draft was produced.")
        if resp.stop_reason == "max_tokens":
            raise InferenceError("PROVIDER_TRUNCATED", "The model output hit the token limit; no draft was produced.")
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        usage = getattr(resp, "usage", None)
        return InferenceResult(
            raw_text=text, provider=self.name, model=getattr(resp, "model", self._model),
            usage={"input_tokens": getattr(usage, "input_tokens", None),
                   "output_tokens": getattr(usage, "output_tokens", None)} if usage else {},
        )
