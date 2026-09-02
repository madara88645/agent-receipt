import os
import time

import pytest

from agent_receipt.session import (find_project_dir, latest_session, resolve_session, session_files)


def _touch(path, age_seconds=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def test_find_project_dir_matches_claude_encoding_of_cwd(tmp_path):
    projects = tmp_path / "projects"
    target = projects / "-Users-me-Developer-my-app--claude-worktrees-x"
    target.mkdir(parents=True)
    (projects / "-Users-me-Developer-my-app").mkdir()
    cwd = "/Users/me/Developer/my.app/.claude/worktrees/x"   # '/' and '.' both become '-'
    assert find_project_dir(cwd, claude_home=tmp_path) == target


def test_find_project_dir_tolerates_underscore_ambiguity(tmp_path):
    projects = tmp_path / "projects"
    only = projects / "-Users-me-my-app"
    only.mkdir(parents=True)
    assert find_project_dir("/Users/me/my_app", claude_home=tmp_path) == only


def test_find_project_dir_returns_none_when_unknown(tmp_path):
    (tmp_path / "projects").mkdir()
    assert find_project_dir("/nowhere", claude_home=tmp_path) is None


def test_latest_session_picks_newest_top_level_jsonl_and_ignores_subagent_files(tmp_path):
    old = _touch(tmp_path / "aaaa.jsonl", age_seconds=300)
    new = _touch(tmp_path / "bbbb.jsonl", age_seconds=10)
    _touch(tmp_path / "bbbb" / "subagents" / "agent-zzz.jsonl", age_seconds=0)
    assert latest_session(tmp_path) == new
    assert latest_session(tmp_path / "empty-nonexistent") is None


def test_session_files_lists_main_and_subagent_transcripts(tmp_path):
    main = _touch(tmp_path / "s1.jsonl")
    a = _touch(tmp_path / "s1" / "subagents" / "agent-a.jsonl")
    b = _touch(tmp_path / "s1" / "subagents" / "agent-b.jsonl")
    files = session_files(main)
    assert files.session_id == "s1" and files.main == main and files.subagents == [a, b]
    assert session_files(_touch(tmp_path / "lonely.jsonl")).subagents == []


def test_resolve_session_accepts_path_id_prefix_or_nothing(tmp_path):
    projects = tmp_path / "projects" / "-Users-me"
    s1 = _touch(projects / "abcd1234-0000.jsonl", age_seconds=100)
    s2 = _touch(projects / "ffff9999-0000.jsonl", age_seconds=5)
    kw = dict(cwd="/Users/me", claude_home=tmp_path)
    assert resolve_session(str(s1), **kw) == s1                # explicit path
    assert resolve_session("abcd1234", **kw) == s1             # id prefix
    assert resolve_session(None, **kw) == s2                   # latest
    with pytest.raises(FileNotFoundError):
        resolve_session("nope", **kw)
    with pytest.raises(FileNotFoundError):
        resolve_session(None, cwd="/elsewhere", claude_home=tmp_path)


def test_resolve_session_with_a_nonexistent_path_raises_not_found_instead_of_globbing(tmp_path):
    (tmp_path / "projects" / "-Users-me").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="not found"):
        resolve_session("/no/such/dir/session.jsonl", cwd="/Users/me", claude_home=tmp_path)
