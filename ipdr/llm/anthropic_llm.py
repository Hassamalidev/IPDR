from __future__ import annotations

import re

import anthropic

from .base import LLMConfig, LLMError, Tool, ToolCall, Turn

FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_NO_EFFORT = re.compile(r"claude-(3|3-5|3-7|sonnet-4-5|haiku-4-5|opus-4-1|opus-4-0|sonnet-4-0)")


def _client(cfg: LLMConfig) -> anthropic.Anthropic:
    kwargs = {"max_retries": 3, "timeout": 300.0}
    if cfg.api_key:
        kwargs["api_key"] = cfg.api_key
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return anthropic.Anthropic(**kwargs)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except anthropic.AuthenticationError as e:
        raise LLMError("The Anthropic API key was rejected. Check it in Settings.") from e
    except anthropic.PermissionDeniedError as e:
        raise LLMError("This Anthropic key does not have access to that model.") from e
    except anthropic.NotFoundError as e:
        raise LLMError("Anthropic does not know that model name. Check it in Settings.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Anthropic is rate limiting requests right now. Wait a minute and try again.") from e
    except anthropic.BadRequestError as e:
        raise LLMError(f"Anthropic rejected the request: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic returned an error ({e.status_code}). Try again shortly.") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Cannot reach Anthropic. Check the network connection.") from e


class AnthropicSession:
    def __init__(self, cfg: LLMConfig, system: str, tools: list[Tool], user: str):
        self.cfg = cfg
        self.client = _client(cfg)
        self.system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self.tools = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters, "strict": True}
            for t in tools
        ]
        self.messages: list[dict] = [{"role": "user", "content": user}]

    def send(self) -> Turn:
        kwargs = {
            "model": self.cfg.model,
            "max_tokens": 16000,
            "system": self.system,
            "tools": self.tools,
            "messages": self.messages,
            "cache_control": {"type": "ephemeral"},
        }
        if not _NO_EFFORT.search(self.cfg.model) and self.cfg.effort:
            kwargs["output_config"] = {"effort": self.cfg.effort}
        if self.cfg.model in _FALLBACK_MODELS:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        resp = _call(self.client.beta.messages.create, **kwargs)
        self.messages.append({"role": "assistant", "content": resp.content})

        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, args=dict(b.input or {})) for b in resp.content if b.type == "tool_use"]
        stop = {"tool_use": "tool_use", "max_tokens": "max_tokens", "refusal": "refusal"}.get(resp.stop_reason, "end")
        if calls and stop == "end":
            stop = "tool_use"
        u = resp.usage
        usage = {
            "input": getattr(u, "input_tokens", 0) or 0,
            "output": getattr(u, "output_tokens", 0) or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
        }
        return Turn(text=text, tool_calls=calls, stop=stop, usage=usage)

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> None:
        self.messages.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": call.id, "content": text, "is_error": is_error}
                for call, text, is_error in results
            ],
        })


def list_models(cfg: LLMConfig) -> list[str]:
    client = _client(cfg)
    page = _call(client.models.list, limit=100)
    return sorted({m.id for m in page.data})


def test(cfg: LLMConfig) -> str:
    client = _client(cfg)
    m = _call(client.models.retrieve, cfg.model)
    return f"Connected. {getattr(m, 'display_name', None) or m.id} is available."
