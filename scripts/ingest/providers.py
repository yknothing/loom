#!/usr/bin/env python3
"""
providers.py — Provider registry and API-key resolution for Loom.

Two classes of providers are supported:

  1. Direct HTTP providers (mimo / kimi / deepseek / any custom) speaking
     the OpenAI or Anthropic wire format. Configured via ``api: openai`` or
     ``api: anthropic``.
  2. The ``emergent`` provider, which routes through the emergentintegrations
     universal-key client and can address OpenAI / Anthropic / Gemini models
     with a single key (``EMERGENT_LLM_KEY``). Its ``model`` field uses the
     ``<llm_provider>/<model_name>`` notation, e.g. ``openai/gpt-5.4``.

config/loom.yml can override any builtin field and register custom providers:

    providers:
      default: emergent
      emergent:
        model: openai/gpt-5.4
      my-gateway:
        base_url: https://llm.example.com/v1
        model: my-model
        max_tokens: 8192
        api: openai
        api_key: sk-...

API-key resolution order (``resolve_api_key``):
  1. Environment variables:
       EMERGENT_LLM_KEY                    (emergent profile only)
       LOOM_API_KEY_<PROFILE>              (e.g. LOOM_API_KEY_KIMI)
       <PROFILE>_API_KEY                   (e.g. DEEPSEEK_API_KEY)
  2. config/loom.yml → providers.<name>.api_key
  3. Legacy openclaw auth-profiles.json (backwards compatibility)
"""

import json
import os
from pathlib import Path

from . import config as loom_config

# ⛔⛔⛔ MIMO CONSTRAINTS (violated 3 times, do NOT change without checking):
#   1. max_tokens MUST be ≤ 16384 (larger values cause SSL disconnect)
#   2. NEVER use ProxyHandler({}) or NO_PROXY to bypass system proxy
#      (Mimo SGP is only reachable via the system https_proxy)
# See MEMORY.md → "Mimo 硬性约束" for full context.

BUILTIN_PROVIDERS = {
    "mimo": {
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
        "max_tokens": 16384,
        "api": "openai",
        "auth_header": "Bearer",
        "key_profile": "xiaomimimo",
    },
    "kimi": {
        "base_url": "https://api.kimi.com/coding",
        "model": "kimi-for-coding",
        "max_tokens": 32768,
        "api": "anthropic",
        "auth_header": "x-api-key",
        "key_profile": "kimi",
        "supports_cache_control": False,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "max_tokens": 8192,
        "api": "openai",
        "auth_header": "Bearer",
        "key_profile": "deepseek",
    },
    "emergent": {
        "base_url": "",
        "model": "openai/gpt-5.1",
        "max_tokens": 8192,
        "api": "emergent",
        "auth_header": "Bearer",
        "key_profile": "emergent",
    },
}

_RESERVED_KEYS = {"default"}


def _config_providers() -> dict:
    """Provider override/custom blocks declared in config/loom.yml."""
    cfg = loom_config.get("providers", {}) or {}
    if not isinstance(cfg, dict):
        return {}
    return {k: v for k, v in cfg.items()
            if k not in _RESERVED_KEYS and isinstance(v, dict)}


def default_provider() -> str:
    """Name of the configured default provider (falls back to 'kimi')."""
    return loom_config.get("providers.default", "kimi") or "kimi"


def get_provider(name: str) -> dict:
    """Return the merged provider config for ``name``.

    Unknown names fall back to the kimi builtin (legacy behaviour),
    but still honour any config overrides registered under that name.
    """
    base = dict(BUILTIN_PROVIDERS.get(name) or BUILTIN_PROVIDERS["kimi"])
    overrides = _config_providers().get(name, {})
    base.update({k: v for k, v in overrides.items() if k != "api_key"})
    base.setdefault("key_profile", name)
    return base


def provider_names() -> list:
    """All known provider names (builtin first, then custom)."""
    names = list(BUILTIN_PROVIDERS)
    names += [n for n in _config_providers() if n not in BUILTIN_PROVIDERS]
    return names


def has_key(profile: str) -> bool:
    try:
        resolve_api_key(profile)
        return True
    except Exception:
        return False


def list_providers() -> list:
    """Inventory of providers with key status — used by the web console."""
    out = []
    default = default_provider()
    for n in provider_names():
        p = get_provider(n)
        out.append({
            "name": n,
            "model": p.get("model", ""),
            "api": p.get("api", ""),
            "max_tokens": p.get("max_tokens", 0),
            "has_key": has_key(p.get("key_profile", n)),
            "is_default": n == default,
        })
    return out


def resolve_api_key(profile: str) -> str:
    """Resolve the API key for a provider key-profile. Raises KeyError if absent."""
    profile = profile or ""
    profile_upper = profile.upper().replace("-", "_")

    env_candidates = []
    if profile.lower() in ("emergent", "universal"):
        env_candidates.append("EMERGENT_LLM_KEY")
    env_candidates += [f"LOOM_API_KEY_{profile_upper}", f"{profile_upper}_API_KEY"]
    for var in env_candidates:
        val = os.environ.get(var)
        if val:
            return val

    # config/loom.yml → providers.<name>.api_key (match by name or key_profile)
    for name, conf in _config_providers().items():
        if (name == profile or conf.get("key_profile") == profile) and conf.get("api_key"):
            return str(conf["api_key"])

    # Legacy: openclaw auth-profiles.json
    auth_path = Path.home() / ".openclaw/agents/leader/agent/auth-profiles.json"
    if auth_path.exists():
        try:
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            key = auth.get("profiles", {}).get(f"{profile}:default", {}).get("key")
            if key:
                return key
        except Exception:
            pass

    raise KeyError(
        f"No API key found for provider profile '{profile}'. "
        f"Set env LOOM_API_KEY_{profile_upper} (or EMERGENT_LLM_KEY for the "
        f"emergent provider), or providers.{profile}.api_key in config/loom.yml"
    )
