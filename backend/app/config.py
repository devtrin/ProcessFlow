"""Runtime configuration from environment variables (see .env.example).

No secret has a default. Credentials are read from the environment only and
never returned by any API endpoint or written to logs.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


def _default_data_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ProcessFlow"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ProcessFlow"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "processflow"


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw and raw.strip() else default


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing env vars win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int = 20 * 1024 * 1024
    max_xml_bytes: int = 5 * 1024 * 1024
    max_xml_depth: int = 64
    max_xml_elements: int = 50_000
    max_pdf_pages: int = 100
    min_text_chars_per_page: int = 20
    # Inference
    inference_provider: str = "none"  # none | ollama | anthropic
    allow_remote_inference: bool = False
    anthropic_model: str = "claude-opus-5-5"
    anthropic_fallbacks: str = "default"  # "default" | "off"
    anthropic_effort: str = "high"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    inference_timeout_s: float = 300.0
    max_inference_chars: int = 200_000
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])

    @property
    def db_path(self) -> Path:
        return self.data_dir / "processflow.sqlite3"

    @property
    def sources_dir(self) -> Path:
        return self.data_dir / "sources"


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("PF_DATA_DIR") or _default_data_dir()).expanduser().resolve(),
        max_upload_bytes=_int("PF_MAX_UPLOAD_MB", 20) * 1024 * 1024,
        max_xml_bytes=_int("PF_MAX_XML_MB", 5) * 1024 * 1024,
        max_xml_depth=_int("PF_MAX_XML_DEPTH", 64),
        max_xml_elements=_int("PF_MAX_XML_ELEMENTS", 50_000),
        max_pdf_pages=_int("PF_MAX_PDF_PAGES", 100),
        inference_provider=(os.environ.get("PF_INFERENCE_PROVIDER") or "none").strip().lower(),
        allow_remote_inference=_bool("PF_ALLOW_REMOTE_INFERENCE"),
        anthropic_model=os.environ.get("PF_ANTHROPIC_MODEL") or "claude-opus-5-5",
        anthropic_fallbacks=(os.environ.get("PF_ANTHROPIC_FALLBACKS") or "default").strip().lower(),
        anthropic_effort=os.environ.get("PF_ANTHROPIC_EFFORT") or "high",
        ollama_base_url=os.environ.get("PF_OLLAMA_BASE_URL") or "http://127.0.0.1:11434",
        ollama_model=os.environ.get("PF_OLLAMA_MODEL") or "",
        inference_timeout_s=float(os.environ.get("PF_INFERENCE_TIMEOUT_S") or 300),
    )
