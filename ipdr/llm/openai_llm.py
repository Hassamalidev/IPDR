from __future__ import annotations

import json
import re

import requests

from .. import net

from .base import LLMConfig, LLMError, Tool, ToolCall, Turn


def _headers(cfg: LLMConfig) -> dict:
    h = {"Content-Type": "application/json"}
    if cfg.api_key:
        h["Authorization"] = f"Bearer {cfg.api_key}"
    return h


def _base(cfg: LLMConfig) -> str:
    if not cfg.base_url:
        raise LLMError("Set the provider's base URL in Settings.")
    return cfg.base_url.rstrip("/")


def _error(resp: requests.Response, provider: str) -> LLMError:
    detail = ""
    try:
        body = resp.json()
        err = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(err, list) and err:
            err = err[0].get("error", err[0]) if isinstance(err[0], dict) else err[0]
        detail = (err.get("message") if isinstance(err, dict) else str(err)) or ""
    except ValueError:
        detail = resp.text[:300]
    detail = " ".join(str(detail).split())[:400]
    code = resp.status_code
    if "<html" in detail.lower() or "<!doctype" in detail.lower():
        detail = ""
    if code in (502, 503, 504) or 520 <= code <= 530:
        host = requests.utils.urlparse(resp.url or "").netloc or "the AI server"
        tunnel = " It looks like a tunnel (trycloudflare): if the model runs on Kaggle or Colab, check the notebook " \
                 "is still running, and update the URL in Settings if the tunnel was restarted." \
            if "trycloudflare" in host or "ngrok" in host else ""
        return LLMError(f"The AI server at {host} is not reachable right now (error {code}).{tunnel}")
    if code in (401, 403):
        return LLMError(f"The {provider} API key was rejected. Check it in Settings. {detail}".strip())
    if code == 404:
        return LLMError(f"{provider} does not know that model or URL. {detail}".strip())
    if code == 429:
        return LLMError(f"{provider} is rate limiting requests or the quota is used up. {detail}".strip())
    return LLMError(f"{provider} returned an error ({code}). {detail}".strip())


def _post(cfg: LLMConfig, path: str, body: dict, label: str, timeout: float = 300) -> dict:
    try:
        resp = net.post(_base(cfg) + path, headers=_headers(cfg), json=body, timeout=timeout)
    except requests.RequestException as e:
        raise LLMError(f"Cannot reach {label} at {cfg.base_url}: {e.__class__.__name__}") from e
    if resp.status_code != 200:
        raise _error(resp, label)
    try:
        return resp.json()
    except ValueError as e:
        raise LLMError(f"{label} returned a response that is not JSON.") from e


_TOOL_TAG = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def _json_objects(text: str) -> list:
    dec = json.JSONDecoder()
    out, i = [], 0
    while i < len(text):
        j = min([k for k in (text.find("{", i), text.find("[", i)) if k != -1], default=-1)
        if j == -1:
            break
        try:
            obj, end = dec.raw_decode(text, j)
        except ValueError:
            i = j + 1
            continue
        out.append(obj)
        i = end
    return out


def text_tool_calls(text: str, known: set[str]) -> list[tuple[str, dict]]:
    if not text or not known:
        return []
    chunks = _TOOL_TAG.findall(text) or _FENCE.findall(text) or [text]
    found = []
    for chunk in chunks:
        for obj in _json_objects(chunk):
            for item in obj if isinstance(obj, list) else [obj]:
                if not isinstance(item, dict):
                    continue
                fn = item.get("function") if isinstance(item.get("function"), dict) else item
                name = fn.get("name") or item.get("tool") or item.get("tool_name")
                if name not in known:
                    continue
                args = fn.get("arguments", fn.get("parameters", fn.get("args", {})))
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                found.append((name, args if isinstance(args, dict) else {}))
    return found


class OpenAISession:
    def __init__(self, cfg: LLMConfig, system: str, tools: list[Tool], user: str, label: str = "The provider"):
        self.cfg = cfg
        self.label = label
        strict = cfg.provider == "openai"
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    **({"strict": True} if strict else {}),
                },
            }
            for t in tools
        ]
        self.messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _request(self) -> tuple[dict, str, dict]:
        body = {"model": self.cfg.model, "messages": self.messages, "tools": self.tools, "tool_choice": "auto",
                "stream": True}
        if self.cfg.provider in ("openai", "ollama"):
            body["stream_options"] = {"include_usage": True}
        try:
            resp = net.post(_base(self.cfg) + "/chat/completions", headers=_headers(self.cfg), json=body,
                                 timeout=(20, 300), stream=True)
        except requests.RequestException as e:
            raise LLMError(f"Cannot reach {self.label} at {self.cfg.base_url}: {e.__class__.__name__}") from e
        if resp.status_code != 200:
            raise _error(resp, self.label)

        ctype = (getattr(resp, "headers", None) or {}).get("content-type", "")
        if "event-stream" not in ctype:
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise LLMError(f"{self.label} returned no answer.")
            return choices[0].get("message") or {}, choices[0].get("finish_reason") or "", data.get("usage") or {}

        parts: list[str] = []
        calls: dict[int, dict] = {}
        finish, usage = "", {}
        resp.encoding = "utf-8"
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                if chunk.get("error"):
                    err = chunk["error"]
                    raise LLMError(f"{self.label}: {err.get('message', err) if isinstance(err, dict) else err}")
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("content"):
                        parts.append(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        c = calls.setdefault(tc.get("index", len(calls)),
                                             {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            c["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            c["function"]["name"] += fn["name"]
                        args = fn.get("arguments")
                        if isinstance(args, dict):
                            c["function"]["arguments"] = json.dumps(args)
                        elif args:
                            c["function"]["arguments"] += args
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
        except requests.RequestException as e:
            raise LLMError(f"The connection to {self.label} dropped while it was answering. Try again.") from e
        except ValueError as e:
            resp.close()
            raise LLMError(f"{self.label} sent a response that could not be read.") from e
        except BaseException:
            resp.close()
            raise
        try:
            for _ in getattr(resp, "iter_content", lambda **k: ())(chunk_size=None):
                pass
        except requests.RequestException:
            resp.close()
        msg = {"content": "".join(parts)}
        if calls:
            msg["tool_calls"] = [calls[i] for i in sorted(calls)]
        return msg, finish, usage

    def send(self) -> Turn:
        msg, finish, u = self._request()
        raw_calls = msg.get("tool_calls") or []
        if not raw_calls:
            recovered = text_tool_calls(msg.get("content") or "", {t["function"]["name"] for t in self.tools})
            if recovered:
                raw_calls = [{"id": f"call_t{len(self.messages)}_{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(recovered)]
                msg = {**msg, "content": None}

        stored = {"role": "assistant", "content": msg.get("content") or (None if raw_calls else "")}
        if raw_calls:
            stored["tool_calls"] = raw_calls
        self.messages.append(stored)

        calls = []
        for i, c in enumerate(raw_calls):
            fn = c.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            bad = ""
            if isinstance(args_raw, dict):
                args = args_raw
            else:
                try:
                    args = json.loads(args_raw) if args_raw.strip() else {}
                except (ValueError, AttributeError):
                    args, bad = {}, str(args_raw)[:200]
            if not c.get("id"):
                c["id"] = f"call_{len(self.messages)}_{i}"
            calls.append(ToolCall(id=c["id"], name=fn.get("name", ""), args=args if isinstance(args, dict) else {}, bad_args=bad))

        if calls:
            stop = "tool_use"
        elif finish == "length":
            stop = "max_tokens"
        elif finish == "content_filter":
            stop = "refusal"
        else:
            stop = "end"
        usage = {
            "input": u.get("prompt_tokens", 0) or 0,
            "output": u.get("completion_tokens", 0) or 0,
            "cache_read": ((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)) or 0,
        }
        return Turn(text=msg.get("content") or "", tool_calls=calls, stop=stop, usage=usage)

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> None:
        for call, text, is_error in results:
            self.messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": ("Error: " + text) if is_error and not text.startswith("Error") else text,
            })


def list_models(cfg: LLMConfig, label: str) -> list[str]:
    try:
        resp = net.get(_base(cfg) + "/models", headers=_headers(cfg), timeout=30)
    except requests.RequestException as e:
        raise LLMError(f"Cannot reach {label} at {cfg.base_url}: {e.__class__.__name__}") from e
    if resp.status_code != 200:
        raise _error(resp, label)
    data = resp.json()
    items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
    ids = []
    for m in items or []:
        mid = m.get("id") or m.get("name") if isinstance(m, dict) else str(m)
        if mid:
            ids.append(mid.removeprefix("models/"))
    return sorted(set(ids))


def test(cfg: LLMConfig, label: str) -> str:
    if not cfg.model:
        raise LLMError("Enter a model name first.")
    data = _post(cfg, "/chat/completions",
                 {"model": cfg.model, "messages": [{"role": "user", "content": "Reply with the word OK."}]},
                 label, timeout=60)
    if not data.get("choices"):
        raise LLMError(f"{label} answered, but with no content.")
    return f"Connected. {cfg.model} answered."
