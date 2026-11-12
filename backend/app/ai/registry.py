from __future__ import annotations

from ..config import Settings
from .anthropic_adapter import AnthropicAdapter
from .base import ProcessInferenceAdapter
from .ollama_adapter import OllamaAdapter


def build_adapter(settings: Settings) -> ProcessInferenceAdapter | None:
    p = settings.inference_provider
    if p in ("", "none", "off", "disabled"):
        return None
    if p == "anthropic":
        return AnthropicAdapter(model=settings.anthropic_model, allow_remote=settings.allow_remote_inference,
                                fallbacks=settings.anthropic_fallbacks, effort=settings.anthropic_effort,
                                timeout_s=settings.inference_timeout_s)
    if p == "ollama":
        return OllamaAdapter(base_url=settings.ollama_base_url, model=settings.ollama_model,
                             allow_remote=settings.allow_remote_inference, timeout_s=settings.inference_timeout_s)
    raise ValueError(f"Unknown PF_INFERENCE_PROVIDER '{p}'. Use none, ollama or anthropic.")


def provider_status(adapter: ProcessInferenceAdapter | None, settings: Settings) -> dict:
    if adapter is None:
        return {"provider": "none", "configured": False, "ready": False, "remote": False, "model": None,
                "message": "No inference provider is configured. PDF text extraction works, but automatic "
                           "process drafting is unavailable. See README 'Enabling PDF inference'."}
    ready, why = adapter.readiness()
    return {"provider": adapter.name, "configured": True, "ready": ready, "remote": adapter.is_remote,
            "model": adapter.model, "message": why}
