"""Read one Claude Code transcript (.jsonl) into API calls and subagent spawn records.

A transcript is either the main session file (``<session>.jsonl``) or one subagent file
(``<session>/subagents/agent-<id>.jsonl``). Lines are independent JSON objects; a single
API response is stored as several lines that share ``message.id`` (one per content block),
so calls are de-duplicated here and the largest usage value seen per field is kept.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

_AGENT_ID_IN_TEXT = re.compile(r"agentId:\s*([A-Za-z0-9_-]+)")
_ERROR_PREFIX = re.compile(r"^\s*error:\s*", re.IGNORECASE)


@dataclass(frozen=True)
class Usage:
    """Token counts for one API call (or a sum of calls)."""

    input: int = 0
    cache_create: int = 0
    cache_read: int = 0
    output: int = 0

    @classmethod
    def from_api(cls, raw: dict[str, Any] | None) -> "Usage":
        raw = raw or {}
        return cls(
            input=int(raw.get("input_tokens") or 0),
            cache_create=int(raw.get("cache_creation_input_tokens") or 0),
            cache_read=int(raw.get("cache_read_input_tokens") or 0),
            output=int(raw.get("output_tokens") or 0),
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input + other.input,
            self.cache_create + other.cache_create,
            self.cache_read + other.cache_read,
            self.output + other.output,
        )

    def merge_max(self, other: "Usage") -> "Usage":
        """Combine two partial reports of the same call: streaming blocks only grow."""
        return Usage(
            max(self.input, other.input),
            max(self.cache_create, other.cache_create),
            max(self.cache_read, other.cache_read),
            max(self.output, other.output),
        )

    @property
    def total(self) -> int:
        return self.input + self.cache_create + self.cache_read + self.output


@dataclass
class Call:
    """One de-duplicated API call made by an agent."""

    agent_id: str | None
    message_id: str
    model: str
    timestamp: str
    usage: Usage


@dataclass
class Spawn:
    """One use of the Agent tool, paired with its result when the result exists.

    ``error`` is set when the tool itself failed (concurrency limit, fork not allowed, ...):
    nothing was launched, so there is no child and no transcript to look for.
    """

    parent_agent_id: str | None
    tool_use_id: str
    description: str
    subagent_type: str
    requested_model: str | None
    timestamp: str
    child_agent_id: str | None = None
    resolved_model: str | None = None
    error: str | None = None


@dataclass
class Transcript:
    path: Path
    agent_id: str | None
    calls: list[Call] = field(default_factory=list)
    spawns: list[Spawn] = field(default_factory=list)

    def total_usage(self) -> Usage:
        total = Usage()
        for call in self.calls:
            total = total + call.usage
        return total


def iter_json_lines(path: Path) -> Iterator[dict[str, Any]]:
    """Yield each well-formed JSON object in the file; skip anything else silently."""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _content_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, list):
        return [c for c in content if isinstance(c, dict)]
    return []


def _text_of(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return ""


def _error_reason(text: str) -> str:
    reason = " ".join(_ERROR_PREFIX.sub("", text.strip()).split())
    return reason or "unknown error"


def parse_transcript(path: Path | str) -> Transcript:
    path = Path(path)
    transcript = Transcript(path=path, agent_id=None)
    calls_by_id: dict[str, Call] = {}
    pending: dict[str, Spawn] = {}

    for line in iter_json_lines(path):
        if transcript.agent_id is None and isinstance(line.get("agentId"), str):
            transcript.agent_id = line["agentId"]
        message = line.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        timestamp = str(line.get("timestamp") or "")

        if role == "assistant":
            message_id = str(message.get("id") or line.get("uuid") or "")
            usage = Usage.from_api(message.get("usage"))
            existing = calls_by_id.get(message_id)
            if existing is None:
                call = Call(
                    agent_id=transcript.agent_id,
                    message_id=message_id,
                    model=str(message.get("model") or "unknown"),
                    timestamp=timestamp,
                    usage=usage,
                )
                calls_by_id[message_id] = call
                transcript.calls.append(call)
            else:
                existing.usage = existing.usage.merge_max(usage)
            for block in _content_blocks(message):
                if block.get("type") == "tool_use" and block.get("name") == "Agent":
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    spawn = Spawn(
                        parent_agent_id=transcript.agent_id,
                        tool_use_id=str(block.get("id") or ""),
                        description=str(inp.get("description") or ""),
                        subagent_type=str(inp.get("subagent_type") or "general-purpose"),
                        requested_model=inp.get("model"),
                        timestamp=timestamp,
                    )
                    pending[spawn.tool_use_id] = spawn
                    transcript.spawns.append(spawn)

        elif role == "user":
            for block in _content_blocks(message):
                if block.get("type") != "tool_result":
                    continue
                spawn = pending.get(str(block.get("tool_use_id") or ""))
                if spawn is None:
                    continue
                if block.get("is_error") is True:
                    raw = line.get("toolUseResult")
                    spawn.error = _error_reason(_text_of(block) or (raw if isinstance(raw, str) else ""))
                    continue
                structured = line.get("toolUseResult")
                if isinstance(structured, dict):
                    if isinstance(structured.get("agentId"), str):
                        spawn.child_agent_id = structured["agentId"]
                    if isinstance(structured.get("resolvedModel"), str):
                        spawn.resolved_model = structured["resolvedModel"]
                if spawn.child_agent_id is None:
                    match = _AGENT_ID_IN_TEXT.search(_text_of(block))
                    if match:
                        spawn.child_agent_id = match.group(1)

    # calls made before the first line that names the agent id still belong to this agent
    for call in transcript.calls:
        call.agent_id = transcript.agent_id
    for spawn in transcript.spawns:
        spawn.parent_agent_id = transcript.agent_id
    return transcript
