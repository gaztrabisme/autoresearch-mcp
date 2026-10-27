import json
import threading
from types import SimpleNamespace

import pytest
from subagent.guard.classify import classify_verification

from autoresearch_mcp.brief import build_brief
from autoresearch_mcp.loop_core import baseline
from autoresearch_mcp.proposer import SubagentProposer, count_denials, extract_idea
from autoresearch_mcp.worktree import create, remove


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Summary\nIDEA: Reduce the constant\n", "Reduce the constant"),
        ("IDEA: first\nIDEA: second", "second"),
        ("ordinary response", "ordinary response"),
        ("x" * 200, "x" * 120),
        ("IDEA: earlier\ntrailing explanation", "IDEA: earlier trailing explanation"),
    ],
)
def test_idea_extraction(raw, expected):
    assert extract_idea(raw) == expected


def test_brief_contains_tried_list_scope_and_rules(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    session.tried(1, "failed previous idea", "discarded")
    worktree = create(session, 1)
    brief = build_brief(session, worktree)
    for text in [
        "Reduce score",
        "score",
        "min",
        "0.5",
        "failed previous idea",
        "do not repeat these",
        "train.py",
        "ONE change",
        "cat/head/rg",
        "no git",
        "no python -c",
        "no network",
        "no package installs",
        "do not try to run training",
        "IDEA: <one line>",
    ]:
        assert text in brief
    assert "other.txt" not in brief
    assert str(session.session_dir) not in brief
    remove(session, worktree)


def test_denials_are_counted_for_only_that_agent(tmp_path):
    trace = tmp_path / "trace.jsonl"
    rows = [
        {"kind": "verdict", "agent_id": "a", "action": "deny"},
        {"kind": "verdict", "agent_id": "a", "action": "allow"},
        {"kind": "verdict", "agent_id": "b", "action": "deny"},
        {"kind": "run", "agent_id": "a", "action": "deny"},
        {"kind": "verdict", "agent_id": "a", "action": "deny"},
    ]
    trace.write_text("\n".join(json.dumps(row) for row in rows) + "\n{partial")
    assert count_denials(trace, "a") == 2
    assert count_denials(tmp_path / "missing", "a") == 0


class Backend:
    def __init__(self, trace):
        self.trace_path = trace
        self.started = self.stopped = 0
        self.closed = []
        self.requests = []

    def start(self):
        self.started += 1
        return self

    def delegate(self, **kwargs):
        self.requests.append(kwargs)
        agent_id = f"agent-{len(self.requests)}"
        with self.trace_path.open("a") as trace:
            trace.write(json.dumps({"kind": "verdict", "agent_id": agent_id, "action": "deny"}))
            trace.write("\n")
        ready = threading.Event()
        ready.set()
        return SimpleNamespace(
            agent_id=agent_id,
            await_ready=ready,
            state="completed",
            final_response="Changed it.\nIDEA: reduce value",
            result_text="distilled summary",
        )

    def close_agent(self, agent_id):
        self.closed.append(agent_id)

    def stop(self):
        self.stopped += 1


def test_subagent_api_and_server_lifecycle(session_factory, tmp_path):
    session = session_factory(provider="codex")
    backend = Backend(tmp_path / "trace.jsonl")
    with SubagentProposer(session, server=backend) as proposer:
        for _ in range(2):
            result = proposer.propose(session, tmp_path, "brief")
            assert result.idea == "reduce value"
            assert result.denials == 1
            assert result.run_state == "completed"
    assert backend.started == backend.stopped == 1
    assert backend.closed == ["agent-1", "agent-2"]
    assert (
        backend.requests
        == [
            {
                "provider": "codex",
                "task": "brief",
                "verification": "true",
                "workspace": tmp_path,
                "deny_read": [str(session.session_dir)],
            }
        ]
        * 2
    )
    assert classify_verification(backend.requests[0]["verification"], tmp_path).action == "allow"


def test_agent_closed_even_when_wait_fails(session_factory, tmp_path):
    session = session_factory()
    backend = Backend(tmp_path / "trace.jsonl")

    class BrokenEvent:
        def wait(self):
            raise RuntimeError("wait failed")

    def delegate(**kwargs):
        return SimpleNamespace(agent_id="broken", await_ready=BrokenEvent())

    backend.delegate = delegate
    with (
        pytest.raises(RuntimeError, match="wait failed"),
        SubagentProposer(
            session,
            server=backend,
        ) as proposer,
    ):
        proposer.propose(session, tmp_path, "brief")
    assert backend.closed == ["broken"]
    assert backend.stopped == 1


def test_proposer_runtime_is_outside_denied_session(session_factory, tmp_path, monkeypatch):
    from subagent.config import Settings

    session = session_factory()
    settings = Settings(
        workspace=session.config.target_repo, session_root=tmp_path / "runtime", trace="off"
    )
    captured = {}

    def factory(*, session_root, settings):
        captured.update(root=session_root, settings=settings)
        session_root.mkdir(parents=True)
        return Backend(session_root / "trace.jsonl")

    monkeypatch.setattr("subagent.config.load", lambda project_root: settings)
    monkeypatch.setattr("subagent.core.InProcessServer", factory)
    with SubagentProposer(session) as proposer:
        assert proposer.trace_path == captured["root"] / "trace.jsonl"
    assert captured["root"].is_relative_to(settings.session_root)
    assert not captured["root"].is_relative_to(session.session_dir)
    assert captured["settings"].trace is None


def test_proposer_refuses_runtime_inside_private_session(session_factory, monkeypatch, tmp_path):
    from subagent.config import Settings

    session = session_factory()
    settings = Settings(workspace=session.config.target_repo, session_root=tmp_path)
    monkeypatch.setattr("subagent.config.load", lambda project_root: settings)
    with (
        pytest.raises(ValueError, match="denied private session"),
        SubagentProposer(
            session,
            session_root=session.session_dir / "runtime",
        ),
    ):
        pass


def test_server_is_stopped_after_start_failure(session_factory, tmp_path):
    backend = Backend(tmp_path / "trace.jsonl")

    def fail():
        raise RuntimeError("socket unavailable")

    backend.start = fail
    with (
        pytest.raises(RuntimeError, match="socket unavailable"),
        SubagentProposer(
            session_factory(),
            server=backend,
        ),
    ):
        pass
    assert backend.stopped == 1
