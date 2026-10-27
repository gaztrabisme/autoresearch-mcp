import json
import subprocess
from unittest.mock import Mock

import pytest

from autoresearch_mcp import loop
from autoresearch_mcp import proposer as proposers
from autoresearch_mcp.loop_core import baseline
from autoresearch_mcp.proposer import FakeProposer, ProposalResult
from autoresearch_mcp.store import Session

from .test_core import edit_value


def cli_process_setup(monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_CLAUDE_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/local/bin/claude")
    wrapper = Mock(side_effect=lambda argv, paths: ["sandbox-wrapper", *argv])
    process = Mock(
        return_value=subprocess.CompletedProcess(
            [], 0, json.dumps({"result": "Changed it.\nIDEA: improve value", "is_error": False}), ""
        )
    )
    monkeypatch.setattr("subagent.sandbox.wrap_argv", wrapper)
    monkeypatch.setattr("autoresearch_mcp.proposer.subprocess.run", process)
    return wrapper, process


def test_claude_cli_argv_is_wrapped_and_uses_worktree(session_factory, tmp_path, monkeypatch):
    session = session_factory()
    # Patch after fixture setup so Git subprocesses remain real.
    wrapper, process = cli_process_setup(monkeypatch)
    result = proposers.ClaudeCliProposer().propose(session, tmp_path, "brief with 'quotes'\nline")
    argv, paths = wrapper.call_args.args
    assert paths == [str(session.session_dir)]
    assert argv[:7] == [
        "/local/bin/claude",
        "-p",
        "brief with 'quotes'\nline",
        "--model",
        "haiku",
        "--tools",
        "Read,Edit",
    ]
    for flag, value in [
        ("--permission-mode", "acceptEdits"),
        ("--output-format", "json"),
        ("--setting-sources", "project"),
    ]:
        assert argv[argv.index(flag) + 1] == value
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    assert "--append-system-prompt" in argv
    assert process.call_args.args == (["sandbox-wrapper", *argv],)
    assert process.call_args.kwargs == {
        "cwd": str(tmp_path),
        "capture_output": True,
        "text": True,
        "timeout": 600,
    }
    assert result == ProposalResult(
        "improve value", "completed", raw="Changed it.\nIDEA: improve value"
    )


def test_claude_cli_bin_override_model_and_timeout(session_factory, tmp_path, monkeypatch):
    session = session_factory()
    wrapper, process = cli_process_setup(monkeypatch)
    monkeypatch.setenv("AUTORESEARCH_CLAUDE_BIN", "/custom/claude")
    proposers.ClaudeCliProposer(model="sonnet", timeout_s=17).propose(session, tmp_path, "brief")
    argv = wrapper.call_args.args[0]
    assert argv[0] == "/custom/claude"
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert process.call_args.kwargs["timeout"] == 17


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (1, '{"result": "auth unavailable"}', "auth unavailable"),
        (0, '{"result": "limit reached", "is_error": true}', "limit reached"),
        (0, "not JSON", "not JSON"),
        (0, "[]", "[]"),
        (0, "{}", "{}"),
    ],
)
def test_claude_cli_failures(session_factory, tmp_path, monkeypatch, returncode, stdout, expected):
    session = session_factory()
    _, process = cli_process_setup(monkeypatch)
    process.return_value = subprocess.CompletedProcess([], returncode, stdout, "")
    result = proposers.ClaudeCliProposer().propose(session, tmp_path, "brief")
    assert result.run_state == "failed"
    assert result.denials == 0
    assert expected in result.raw


def test_claude_cli_timeout(session_factory, tmp_path, monkeypatch):
    session = session_factory()
    _, process = cli_process_setup(monkeypatch)
    process.side_effect = subprocess.TimeoutExpired("claude", 12)
    result = proposers.ClaudeCliProposer(timeout_s=12).propose(session, tmp_path, "brief")
    assert result.run_state == "timeout"
    assert result.denials == 0
    assert "12" in result.raw


def test_claude_cli_idea_can_precede_trailing_text(session_factory, tmp_path, monkeypatch):
    session = session_factory()
    _, process = cli_process_setup(monkeypatch)
    process.return_value.stdout = json.dumps(
        {"result": "IDEA: first\nIDEA: final idea\nExtra explanation."}
    )
    result = proposers.ClaudeCliProposer().propose(session, tmp_path, "brief")
    assert result.idea == "final idea"


def test_claude_cli_sandbox_failure_never_launches(session_factory, tmp_path, monkeypatch):
    session = session_factory()
    wrapper, process = cli_process_setup(monkeypatch)
    wrapper.side_effect = RuntimeError("sandbox unavailable")
    with pytest.raises(RuntimeError, match="sandbox unavailable"):
        proposers.ClaudeCliProposer().propose(session, tmp_path, "brief")
    process.assert_not_called()


def test_claude_cli_missing_binary_is_failed(session_factory, tmp_path, monkeypatch):
    session = session_factory()
    wrapper, process = cli_process_setup(monkeypatch)
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = proposers.ClaudeCliProposer().propose(session, tmp_path, "brief")
    assert result.run_state == "failed"
    assert "claude" in result.raw
    wrapper.assert_not_called()
    process.assert_not_called()


def test_claude_cli_provider_selection_and_config_roundtrip(session_factory):
    session = session_factory(
        provider="claude-cli:haiku", fallback_provider="codex", proposer_timeout_s=23
    )
    loaded = Session.load(session.name)
    assert loaded.config == session.config
    with loop._using_proposer(loaded, None) as active:
        assert isinstance(active, proposers.ClaudeCliProposer)
        assert active.model == "haiku"
        assert active.timeout_s == 23


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_claude_cli_timeout_config_is_validated(session_factory, timeout):
    with pytest.raises(ValueError, match="proposer_timeout_s"):
        session_factory(proposer_timeout_s=timeout)
    with pytest.raises(ValueError, match="timeout_s"):
        proposers.ClaudeCliProposer(timeout_s=timeout)


@pytest.mark.parametrize("field", ["provider", "fallback_provider"])
def test_empty_claude_cli_model_is_rejected(session_factory, field):
    with pytest.raises(ValueError, match=field):
        session_factory(**{field: "claude-cli:"})


@pytest.mark.parametrize("state", ["failed", "cancelled", "timeout", "exception"])
def test_fallback_after_primary_failure_logs_both_attempts(
    session_factory, offline_sandbox, monkeypatch, state
):
    session = session_factory(provider="codex", fallback_provider="claude-cli:haiku")
    baseline(session)
    paths = []
    briefs = []

    class Primary:
        def propose(self, session, path, brief):
            paths.append(path)
            briefs.append(brief)
            edit_value(path, 99)
            (path / "partial.txt").write_text("failed edit")
            if state == "exception":
                raise RuntimeError("provider unavailable")
            return ProposalResult("primary idea", state, 3, "provider unavailable")

    class Fallback:
        def __init__(self, model, *, timeout_s):
            assert model == "haiku"
            assert timeout_s == 600

        def propose(self, session, path, brief):
            assert path == paths[0]
            assert brief == briefs[0]
            assert "value = 10" in (path / "train.py").read_text()
            assert not (path / "partial.txt").exists()
            edit_value(path, 8)
            return ProposalResult("fallback idea", "completed")

    monkeypatch.setattr(loop, "ClaudeCliProposer", Fallback)
    row = loop.iterate(session, proposer=Primary())
    assert row["status"] == "kept"
    assert row["iteration"] == 1
    assert row["proposer"] == "claude-cli:haiku"
    assert row["fallback_used"] is True
    attempts = row["proposer_attempts"]
    assert [a["provider"] for a in attempts] == ["codex", "claude-cli:haiku"]
    assert [a["run_state"] for a in attempts] == [
        "failed" if state == "exception" else state,
        "completed",
    ]
    assert "provider unavailable" in attempts[0]["error"]
    assert attempts[0]["proposer_call_id"] != attempts[1]["proposer_call_id"]
    assert row["proposer_call_id"] == attempts[1]["proposer_call_id"]
    assert all(r["proposer_attempts"] == attempts for r in session.rows() if r["iteration"] == 1)
    assert session.next_iteration() == 2
    assert not list(session.workspace_dir.glob("it-*"))
    assert session.log_context == {}
    report = loop.finalize(session, print_path=False).read_text()
    assert "Proposer calls: 2" in report
    assert f"Denials: {0 if state == 'exception' else 3}" in report


@pytest.mark.parametrize("outcome", ["no_change", "discarded", "proposer_incomplete"])
def test_fallback_not_used_after_finished_or_incomplete_proposal(
    session_factory, offline_sandbox, monkeypatch, outcome
):
    session = session_factory(provider="codex", fallback_provider="claude-cli:haiku")
    baseline(session)
    fallback = Mock(side_effect=AssertionError("fallback must not be selected"))
    monkeypatch.setattr(loop, "ClaudeCliProposer", fallback)

    class Primary:
        def propose(self, session, path, brief):
            if outcome == "discarded":
                edit_value(path, 12)
            return ProposalResult(
                "primary idea", "working" if outcome == "proposer_incomplete" else "completed"
            )

    row = loop.iterate(session, proposer=Primary())
    assert row["status"] == outcome
    assert row["proposer"] == "codex"
    assert row["fallback_used"] is False
    assert len(row["proposer_attempts"]) == 1
    fallback.assert_not_called()


def test_fallback_failure_is_not_retried(session_factory, offline_sandbox, monkeypatch):
    session = session_factory(provider="claude-cli:haiku", fallback_provider="codex")
    baseline(session)
    requests = []

    class Fallback:
        def __init__(self, session, *, provider):
            assert provider == "codex"

        def propose(self, session, path, brief):
            requests.append(brief)
            return ProposalResult("fallback failed", "failed", raw="no balance")

        def close(self):
            pass

    monkeypatch.setattr(loop, "SubagentProposer", Fallback)

    class Primary:
        def propose(self, *args):
            raise RuntimeError("primary unavailable")

    row = loop.iterate(session, proposer=Primary())
    assert row["status"] == "proposer_error"
    assert row["fallback_used"] is True
    assert len(row["proposer_attempts"]) == 2
    assert len(requests) == 1
    assert "no balance" in row["error"]


def test_run_uses_claude_cli_provider(session_factory, offline_sandbox, monkeypatch):
    session = session_factory(provider="claude-cli:haiku")
    baseline(session)
    fake = FakeProposer([("no change", lambda path: None)] * 2)
    factory = Mock(return_value=fake)
    monkeypatch.setattr(loop, "ClaudeCliProposer", factory)
    result = loop.run(session, 2)
    assert result["iterations"] == fake.calls == 2
    factory.assert_called_once_with(model="haiku", timeout_s=600)


def test_run_falls_back_after_primary_startup_exception(
    session_factory, offline_sandbox, monkeypatch
):
    session = session_factory(provider="codex", fallback_provider="claude-cli:haiku")
    baseline(session)
    closed = []

    class Primary:
        def __init__(self, session, *, provider):
            pass

        def propose(self, *args):
            raise RuntimeError("startup failed")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(loop, "SubagentProposer", Primary)
    fake = FakeProposer([("no change", lambda path: None)])
    monkeypatch.setattr(loop, "ClaudeCliProposer", lambda **kwargs: fake)
    assert loop.run(session, 1)["iterations"] == 1
    assert fake.calls == 1
    assert session.rows()[-1]["fallback_used"] is True
    assert closed == [True]


def test_run_falls_back_after_primary_constructor_exception(
    session_factory, offline_sandbox, monkeypatch
):
    session = session_factory(provider="codex", fallback_provider="claude-cli:haiku")
    baseline(session)
    primary = Mock(side_effect=RuntimeError("constructor failed"))
    monkeypatch.setattr(loop, "SubagentProposer", primary)
    fake = FakeProposer([("no change", lambda path: None)])
    monkeypatch.setattr(loop, "ClaudeCliProposer", lambda **kwargs: fake)
    assert loop.run(session, 1)["iterations"] == 1
    assert fake.calls == 1
    row = session.rows()[-1]
    assert row["fallback_used"] is True
    assert "constructor failed" in row["proposer_attempts"][0]["error"]
