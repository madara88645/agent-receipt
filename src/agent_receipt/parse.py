"""Read one Claude Code transcript (.jsonl) into API calls and subagent spawn records.

A transcript is either the main session file (``<session>.jsonl``) or one subagent file
(``<session>/subagents/agent-<id>.jsonl``). Lines are independent JSON objects; a single
API response is stored as several lines that share ``message.id`` (one per content block),
so calls are de-duplicated here and the largest usage value seen per field is kept.
"""
from __future__ import annotations

import json
import re
from collections import Counter
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
    session_id: str | None = None        # the line's own sessionId; differs from the file when history was carried over


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
    status: str | None = None            # e.g. completed / async_launched
    duration_ms: int | None = None       # from the finished result, wall-clock of the child run
    tool_calls: int | None = None        # totalToolUseCount from the finished result
    tool_stats: dict[str, int] = field(default_factory=dict)
    session_id: str | None = None


@dataclass
class WorkflowRun:
    """One use of the Workflow tool: its agents live under subagents/workflows/<run_id>/."""

    parent_agent_id: str | None
    tool_use_id: str
    name: str
    description: str
    timestamp: str
    run_id: str | None = None
    transcript_dir: str | None = None
    status: str | None = None
    session_id: str | None = None


@dataclass
class Transcript:
    path: Path
    agent_id: str | None
    calls: list[Call] = field(default_factory=list)
    spawns: list[Spawn] = field(default_factory=list)
    workflows: list[WorkflowRun] = field(default_factory=list)
    tool_calls: Counter = field(default_factory=Counter)   # tool name -> count, this agent only
    title: str | None = None
    first_prompt: str | None = None                        # head of the first human/user text, for labels
    cwd: str | None = None
    git_branch: str | None = None
    version: str | None = None
    reported_cost_usd: float | None = None                 # Claude Code's own cost-state line, when present
    reported_model_usage: dict[str, dict] = field(default_factory=dict)
    session_id: str | None = None                          # from the file name
    continued_from: list[str] = field(default_factory=list)  # other session ids whose lines this file carries

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
    transcript = Transcript(path=path, agent_id=None, session_id=path.stem if path.suffix == ".jsonl" else None)
    foreign: list[str] = []
    calls_by_id: dict[str, Call] = {}
    pending: dict[str, Spawn] = {}
    pending_workflows: dict[str, WorkflowRun] = {}

    for line in iter_json_lines(path):
        if transcript.agent_id is None and isinstance(line.get("agentId"), str):
            transcript.agent_id = line["agentId"]
        line_session = line.get("sessionId") if isinstance(line.get("sessionId"), str) else None
        if line_session and transcript.session_id and line_session != transcript.session_id and line_session not in foreign \
                and not path.name.startswith("agent-"):
            foreign.append(line_session)
        kind = line.get("type")
        if kind == "custom-title" and isinstance(line.get("customTitle"), str):
            transcript.title = line["customTitle"]
        elif kind == "ai-title" and isinstance(line.get("aiTitle"), str) and transcript.title is None:
            transcript.title = line["aiTitle"]
        elif kind == "cost-state":
            if isinstance(line.get("totalCostUSD"), (int, float)):
                transcript.reported_cost_usd = float(line["totalCostUSD"])
            if isinstance(line.get("modelUsage"), dict):
                transcript.reported_model_usage = line["modelUsage"]
        for key, attr in (("cwd", "cwd"), ("gitBranch", "git_branch"), ("version", "version")):
            if getattr(transcript, attr) is None and isinstance(line.get(key), str):
                setattr(transcript, attr, line[key])
        message = line.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        timestamp = str(line.get("timestamp") or "")

        if role == "assistant":
            if message.get("model") == "<synthetic>":
                continue      # Claude Code placeholder, not an API call
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
                    session_id=line_session,
                )
                calls_by_id[message_id] = call
                transcript.calls.append(call)
            else:
                existing.usage = existing.usage.merge_max(usage)
            for block in _content_blocks(message):
                if block.get("type") == "tool_use":
                    transcript.tool_calls[str(block.get("name") or "?")] += 1
                if block.get("type") == "tool_use" and block.get("name") == "Workflow":
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    run = WorkflowRun(parent_agent_id=transcript.agent_id, tool_use_id=str(block.get("id") or ""),
                                      name="", description=str(inp.get("description") or ""), timestamp=timestamp,
                                      session_id=line_session)
                    pending_workflows[run.tool_use_id] = run
                    transcript.workflows.append(run)
                if block.get("type") == "tool_use" and block.get("name") == "Agent":
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    spawn = Spawn(
                        parent_agent_id=transcript.agent_id,
                        tool_use_id=str(block.get("id") or ""),
                        description=str(inp.get("description") or ""),
                        subagent_type=str(inp.get("subagent_type") or "general-purpose"),
                        requested_model=inp.get("model"),
                        timestamp=timestamp,
                        session_id=line_session,
                    )
                    pending[spawn.tool_use_id] = spawn
                    transcript.spawns.append(spawn)

        elif role == "user":
            if transcript.first_prompt is None and not line.get("isMeta"):
                text = message.get("content") if isinstance(message.get("content"), str) else \
                    " ".join(str(b.get("text", "")) for b in _content_blocks(message) if b.get("type") == "text")
                text = " ".join(text.split())
                if text and not text.startswith(("<", "/")):
                    transcript.first_prompt = text[:120]
            for block in _content_blocks(message):
                if block.get("type") != "tool_result":
                    continue
                tool_use_id = str(block.get("tool_use_id") or "")
                run = pending_workflows.get(tool_use_id)
                if run is not None:
                    structured = line.get("toolUseResult")
                    if isinstance(structured, dict):
                        run.run_id = structured.get("runId") if isinstance(structured.get("runId"), str) else None
                        run.transcript_dir = structured.get("transcriptDir") if isinstance(structured.get("transcriptDir"), str) else None
                        run.name = str(structured.get("workflowName") or "")
                        run.status = str(structured.get("status") or "") or None
                        if not run.description:
                            run.description = str(structured.get("summary") or "")
                    if run.run_id is None:
                        match = re.search(r"wf_[A-Za-z0-9-]+", _text_of(block))
                        run.run_id = match.group(0) if match else None
                    continue
                spawn = pending.get(tool_use_id)
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
                    if isinstance(structured.get("status"), str):
                        spawn.status = structured["status"]
                    if isinstance(structured.get("totalDurationMs"), (int, float)):
                        spawn.duration_ms = int(structured["totalDurationMs"])
                    if isinstance(structured.get("totalToolUseCount"), (int, float)):
                        spawn.tool_calls = int(structured["totalToolUseCount"])
                    if isinstance(structured.get("toolStats"), dict):
                        spawn.tool_stats = {k: int(v) for k, v in structured["toolStats"].items() if isinstance(v, (int, float))}
                if spawn.child_agent_id is None:
                    match = _AGENT_ID_IN_TEXT.search(_text_of(block))
                    if match:
                        spawn.child_agent_id = match.group(1)

    # calls made before the first line that names the agent id still belong to this agent
    for call in transcript.calls:
        call.agent_id = transcript.agent_id
    for spawn in transcript.spawns:
        spawn.parent_agent_id = transcript.agent_id
    for run in transcript.workflows:
        run.parent_agent_id = transcript.agent_id
    transcript.continued_from = foreign
    return transcript
