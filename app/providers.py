"""LLM provider definitions for the Cricket Statistician engine.

Every supported provider speaks the OpenAI chat-completions protocol, so a
single ``openai.OpenAI`` client serves all of them — only the base URL, API
key, and model chain differ. Centralising the definitions here keeps provider
churn (adding, retiring, or repricing a vendor) out of both the query engine
and the FastAPI layer.

API keys are resolved from the environment at access time and are never
hard-coded — see ``.env`` (git-ignored) for local development values.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Provider API keys live in .env (git-ignored). Load them here so this module
# is self-sufficient and does not depend on another module importing first.
load_dotenv()


@dataclass(frozen=True)
class ProviderConfig:
    """Immutable, environment-resolved configuration for one LLM provider."""

    name: str
    display_name: str
    base_url: str
    api_key_env: str
    default_model: str
    default_fallbacks: tuple[str, ...]
    model_env: str

    @property
    def api_key(self) -> str:
        """API key for this provider, resolved from the environment."""
        return os.getenv(self.api_key_env, "")

    @property
    def model(self) -> str:
        """Primary model id, overridable via the provider's model env var."""
        return os.getenv(self.model_env, self.default_model)

    @property
    def fallback_models(self) -> list[str]:
        """Fallback chain; entry N is overridable via ``{MODEL_ENV}_{N+1}``."""
        return [
            os.getenv(f"{self.model_env}_{index}", default)
            for index, default in enumerate(self.default_fallbacks, start=2)
        ]

    @property
    def is_configured(self) -> bool:
        """True when an API key is present for this provider."""
        return bool(self.api_key)


# Insertion order matters: the first entry is the default provider.
PROVIDERS: dict[str, ProviderConfig] = {
    "gemini": ProviderConfig(
        name="gemini",
        display_name="Gemini",
        # google-genai client does not use a base_url in the same way as OpenAI.
        # It connects directly to Google's native Gemini endpoints.
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/", 
        api_key_env="GEMINI_API_KEY",
        default_model="gemini-2.5-pro",
        default_fallbacks=("gemini-2.5-flash",),
        model_env="GEMINI_MODEL",
    ),
    "claude": ProviderConfig(
        name="claude",
        display_name="Claude Haiku 4.5",
        base_url="https://api.anthropic.com/v1/",
        api_key_env="ANTHROPIC_API_KEY",
        default_model="claude-haiku-4-5-20251001",
        default_fallbacks=(),
        model_env="CLAUDE_MODEL",
    ),
    "sarvam": ProviderConfig(
        name="sarvam",
        display_name="Sarvam",
        base_url="https://api.sarvam.ai/v1",
        api_key_env="SARVAM_API_KEY",
        default_model="sarvam-105b",
        default_fallbacks=("sarvam-30b",),
        model_env="SARVAM_MODEL",
    ),
}

DEFAULT_PROVIDER: str = next(iter(PROVIDERS))


def get_provider(name: str | None) -> ProviderConfig:
    """Return the config for ``name``, falling back to the default provider."""
    key = (name or "").strip().lower()
    return PROVIDERS.get(key, PROVIDERS[DEFAULT_PROVIDER])


# Per-model USD-per-million-token prices live in a JSON file outside source so
# they can be updated without a code edit when a vendor reprices. Default path
# is data/model_pricing.json; the MODEL_PRICING_FILE env var overrides it.
_DEFAULT_PRICING_PATH = Path(__file__).resolve().parent.parent / "data" / "model_pricing.json"
_PRICING_PATH = Path(os.getenv("MODEL_PRICING_FILE") or _DEFAULT_PRICING_PATH)
_pricing_cache: dict[str, tuple[float, float]] | None = None


def _load_pricing() -> dict[str, tuple[float, float]]:
    """Read the model pricing file once and cache the result for the process."""
    global _pricing_cache
    if _pricing_cache is not None:
        return _pricing_cache
    table: dict[str, tuple[float, float]] = {}
    try:
        with open(_PRICING_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, OSError, ValueError):
        _pricing_cache = table
        return _pricing_cache
    for model, prices in (raw or {}).items():
        if isinstance(prices, dict) and "input" in prices and "output" in prices:
            try:
                table[model] = (float(prices["input"]), float(prices["output"]))
            except (TypeError, ValueError):
                continue
    _pricing_cache = table
    return _pricing_cache


def model_pricing(model: str) -> tuple[float, float] | None:
    """Return (input, output) USD-per-million-token prices, or None if unknown."""
    return _load_pricing().get((model or "").strip())
