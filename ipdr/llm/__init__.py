from __future__ import annotations

from . import anthropic_llm, openai_llm
from .base import LLMConfig, LLMError, Session, Tool, ToolCall, Turn

PRESETS: dict[str, dict] = {
    "anthropic": {
        "label": "Anthropic (Claude)", "kind": "anthropic", "base_url": "", "needs_key": True,
        "default_model": "claude-opus-5-5",
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"],
        "key_hint": "sk-ant-…", "effort": True,
    },
    "openai": {
        "label": "OpenAI", "kind": "openai", "base_url": "https://api.openai.com/v1", "needs_key": True,
        "default_model": "", "models": [], "key_hint": "sk-…",
    },
    "gemini": {
        "label": "Google Gemini", "kind": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "needs_key": True,
        "default_model": "", "models": [], "key_hint": "AIza…",
    },
    "openrouter": {
        "label": "OpenRouter", "kind": "openai", "base_url": "https://openrouter.ai/api/v1", "needs_key": True,
        "default_model": "", "models": [], "key_hint": "sk-or-…",
    },
    "groq": {
        "label": "Groq", "kind": "openai", "base_url": "https://api.groq.com/openai/v1", "needs_key": True,
        "default_model": "", "models": [], "key_hint": "gsk_…",
    },
    "deepseek": {
        "label": "DeepSeek", "kind": "openai", "base_url": "https://api.deepseek.com/v1", "needs_key": True,
        "default_model": "", "models": [], "key_hint": "sk-…",
    },
    "mistral": {
        "label": "Mistral", "kind": "openai", "base_url": "https://api.mistral.ai/v1", "needs_key": True,
        "default_model": "", "models": [], "key_hint": "",
    },
    "together": {
        "label": "Together AI", "kind": "openai", "base_url": "https://api.together.xyz/v1", "needs_key": True,
        "default_model": "", "models": [], "key_hint": "",
    },
    "ollama": {
        "label": "Ollama", "kind": "openai", "base_url": "http://localhost:11434/v1", "needs_key": False,
        "default_model": "", "models": [], "key_hint": "No key needed",
    },
    "custom": {
        "label": "Other (OpenAI-compatible)", "kind": "openai", "base_url": "", "needs_key": False,
        "default_model": "", "models": [], "key_hint": "If the endpoint needs one",
    },
}


def preset(cfg: LLMConfig) -> dict:
    p = PRESETS.get(cfg.provider)
    if p is None:
        raise LLMError(f"Unknown AI provider '{cfg.provider}'.")
    return p


def check_config(cfg: LLMConfig) -> None:
    p = preset(cfg)
    if not cfg.model:
        raise LLMError("Choose a model in Settings.")
    if p["needs_key"] and not cfg.api_key:
        raise LLMError(f"Add your {p['label']} API key in Settings.")
    if p["kind"] == "openai" and not (cfg.base_url or p["base_url"]):
        raise LLMError("Set the provider's base URL in Settings.")


def _with_base(cfg: LLMConfig) -> LLMConfig:
    if not cfg.base_url:
        cfg.base_url = preset(cfg)["base_url"]
    return cfg


def open_session(cfg: LLMConfig, system: str, tools: list[Tool], user: str) -> Session:
    check_config(cfg)
    p = preset(cfg)
    if p["kind"] == "anthropic":
        return anthropic_llm.AnthropicSession(cfg, system, tools, user)
    return openai_llm.OpenAISession(_with_base(cfg), system, tools, user, label=p["label"])


def list_models(cfg: LLMConfig) -> list[str]:
    p = preset(cfg)
    if p["kind"] == "anthropic":
        return anthropic_llm.list_models(cfg)
    return openai_llm.list_models(_with_base(cfg), p["label"])


def test(cfg: LLMConfig) -> str:
    check_config(cfg)
    p = preset(cfg)
    if p["kind"] == "anthropic":
        return anthropic_llm.test(cfg)
    return openai_llm.test(_with_base(cfg), p["label"])


__all__ = ["LLMConfig", "LLMError", "PRESETS", "Session", "Tool", "ToolCall", "Turn",
           "check_config", "list_models", "open_session", "test"]
