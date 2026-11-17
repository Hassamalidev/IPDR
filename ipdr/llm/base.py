from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class LLMError(Exception):
    pass


@dataclass
class LLMConfig:
    provider: str
    model: str
    api_key: str = ""
    base_url: str = ""
    effort: str = "medium"


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
    bad_args: str = ""


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop: str = "end"
    usage: dict = field(default_factory=dict)


class Session(Protocol):
    def send(self) -> Turn: ...
    def add_user(self, text: str) -> None: ...
    def add_tool_results(self, results: list[tuple[ToolCall, str, bool]]) -> None: ...
