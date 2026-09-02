"""Builders for tiny synthetic Claude Code transcripts. No real session data is used."""
from __future__ import annotations

import json
from pathlib import Path


def usage(inp=0, create=0, read=0, out=0):
    return {
        "input_tokens": inp,
        "cache_creation_input_tokens": create,
        "cache_read_input_tokens": read,
        "output_tokens": out,
    }


def assistant_line(message_id, model, usage_, *, agent_id=None, ts="2026-09-01T18:00:00.000Z",
                   content=None, block_index=0):
    """One JSONL line of an assistant API response block. Several lines may share a message id."""
    line = {
        "type": "assistant",
        "uuid": f"{message_id}-{block_index}",
        "timestamp": ts,
        "apiBlockIndex": block_index,
        "message": {
            "role": "assistant",
            "id": message_id,
            "model": model,
            "usage": usage_,
            "content": content if content is not None else [{"type": "text", "text": "ok"}],
        },
    }
    if agent_id is not None:
        line["agentId"] = agent_id
    return line


def agent_tool_use(tool_use_id, description, subagent_type="general-purpose", model=None, prompt="do x"):
    inp = {"description": description, "subagent_type": subagent_type, "prompt": prompt}
    if model is not None:
        inp["model"] = model
    return {"type": "tool_use", "id": tool_use_id, "name": "Agent", "input": inp}


def agent_result_line(tool_use_id, child_agent_id, *, resolved_model=None, agent_id=None,
                      ts="2026-09-01T18:00:01.000Z", with_structured=True, finished=None):
    """``finished`` = dict(duration_ms=, tool_calls=, tool_stats=) mimics the completed-agent result shape."""
    """The user-role line that carries the Agent tool's result and the spawned agent's id."""
    text = f"Async agent launched successfully. agentId: {child_agent_id} (internal ID)"
    line = {
        "type": "user",
        "uuid": f"res-{tool_use_id}",
        "timestamp": ts,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id,
                         "content": [{"type": "text", "text": text}]}],
        },
    }
    if with_structured:
        line["toolUseResult"] = {"isAsync": True, "status": "async_launched",
                                 "agentId": child_agent_id, "resolvedModel": resolved_model}
        if finished:
            line["toolUseResult"].update({"status": "completed", "totalDurationMs": finished.get("duration_ms", 0),
                                          "totalToolUseCount": finished.get("tool_calls", 0),
                                          "toolStats": finished.get("tool_stats", {}), "totalTokens": 0})
    if agent_id is not None:
        line["agentId"] = agent_id
    return line


def workflow_tool_use(tool_use_id, description="run things"):
    return {"type": "tool_use", "id": tool_use_id, "name": "Workflow",
            "input": {"script": "export const meta = {}", "description": description}}


def workflow_result_line(tool_use_id, run_id, name="my-flow", transcript_dir=None, agent_id=None,
                         ts="2026-09-01T18:00:01.000Z"):
    line = {"type": "user", "uuid": f"res-{tool_use_id}", "timestamp": ts,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id,
                        "content": f"Workflow launched in background. Task ID: t1 run {run_id}"}]},
            "toolUseResult": {"status": "async_launched", "taskId": "t1", "taskType": "local_workflow",
                              "workflowName": name, "runId": run_id, "summary": "x",
                              "transcriptDir": transcript_dir or "", "scriptPath": ""}}
    if agent_id is not None:
        line["agentId"] = agent_id
    return line


def meta_json(path: Path, **meta) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta))
    return path


def write_jsonl(path: Path, lines) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")
    return path
