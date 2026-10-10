"""Sponsor settings, read from environment variables (put them in a local .env, never commit it)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    # Nicrron (OpenAI-compatible gateway)
    nicrron_api_key: str = field(default_factory=lambda: os.getenv("NICRRON_API_KEY", ""))
    nicrron_base_url: str = field(default_factory=lambda: os.getenv("NICRRON_BASE_URL", "https://api.nicrron.ai/v1"))
    reader_model: str = field(default_factory=lambda: os.getenv("NICRRON_READER_MODEL", "anthropic/claude-sonnet-5"))
    # Optional second, independent reader from a different vendor (empty = disabled)
    second_reader_model: str = field(default_factory=lambda: os.getenv("NICRRON_SECOND_READER_MODEL", ""))

    # Block Convey PRISM
    prism_api_key: str = field(default_factory=lambda: os.getenv("PRISMTRACE_API_KEY", ""))
    prism_project_id: str = field(default_factory=lambda: os.getenv("PRISMTRACE_PROJECT_ID", ""))
    prism_host: str = field(default_factory=lambda: os.getenv("PRISMTRACE_HOST", "https://api.prism.blockconvey.com"))
    # Stable agent identities: PRISM guardrail/evaluator rules are scoped to these, so never rename them.
    agent_extractor: str = field(default_factory=lambda: os.getenv("PRISM_AGENT_EXTRACTOR", "sigcheck-extractor"))
    agent_guardrail: str = field(default_factory=lambda: os.getenv("PRISM_AGENT_GUARDRAIL", "sigcheck-input-guardrail"))
    agent_reviewer: str = field(default_factory=lambda: os.getenv("PRISM_AGENT_REVIEWER", "sigcheck-review-pipeline"))

    @property
    def nicrron_enabled(self) -> bool:
        return bool(self.nicrron_api_key)

    @property
    def prism_enabled(self) -> bool:
        return bool(self.prism_api_key and self.prism_project_id)


settings = Settings()
