import re

import pytest

from autoresearch_mcp import loop
from autoresearch_mcp.loop_core import baseline
from autoresearch_mcp.proposer import FakeProposer, ProposalResult
from autoresearch_mcp.queue import RunQueue
from autoresearch_mcp.worktree import git

from .conftest import log_rows
from .test_core import edit_value


def fake_values(*values):
    def edit(path, value):
        script = path / "train.py"
        script.write_text(
            re.sub(r"^value = .*$", f"value = {value}", script.read_text(), flags=re.MULTILINE)
        )

    return FakeProposer([(f"try {value}", lambda path, v=value: edit(path, v)) for value in values])


def test_run_keeps_and_discards_with_fake_proposer(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    proposer = fake_values(8, 12)
    result = loop.run(session, max_iter=2, time_cap_s=60, proposer=proposer)
    assert result["iterations"] == 2
    assert result["keeps"] == 1
    assert result["reason"] == "max_iter"
    rows = [row for row in log_rows(session) if row["phase"] == "candidate"]
    assert {"kept", "discarded"} <= {row["status"] for row in rows}
    assert all(row["proposer_state"] == "completed" and row["denials"] == 0 for row in rows)
    assert len({row["proposer_call_id"] for row in rows}) == 2
    assert proposer.calls == 2
    assert session.best["metric"] == 8
    assert int(git(session.config.target_repo, "rev-list", "--count", session.branch)) == 2
    assert not list(session.workspace_dir.glob("it-*"))
    assert (session.session_dir / "tried.md").read_text().count("try 8") == 1


def test_plateau_stop_is_configurable(session_factory, offline_sandbox):
    session = session_factory(plateau_limit=2)
    baseline(session)
    proposer = fake_values(11, 12, 13)
    result = loop.run(session, max_iter=5, time_cap_s=60, proposer=proposer)
    assert result["reason"] == "plateau"
    assert result["iterations"] == proposer.calls == 2


def test_stop_flag_prevents_a_proposer_call(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    (session.session_dir / "stop").touch()
    proposer = fake_values(8)
    assert loop.run(session, 5, 60, proposer=proposer)["reason"] == "stop"
    assert proposer.calls == 0


def test_stop_flag_is_observed_between_iterations(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)

    def edit_and_stop(path):
        edit_value(path, 8)
        (session.session_dir / "stop").touch()

    proposer = FakeProposer([("improve and stop", edit_and_stop)])
    result = loop.run(session, 5, 60, proposer=proposer)
    assert result["reason"] == "stop"
    assert result["iterations"] == 1


def test_time_cap_prevents_another_iteration(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    ticks = iter([0, 0, 0, 0, 20, 20, 20])
    proposer = fake_values(11, 12)
    result = loop.run(session, 5, 10, proposer=proposer, clock=lambda: next(ticks, 20))
    assert result["reason"] == "time_cap"
    assert proposer.calls == 1


def test_memory_gate_precedes_worktree_and_proposer(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    readings = iter([10, 90, 90])
    waits = []

    class Clock:
        def sleep(self, seconds):
            assert not list(session.workspace_dir.glob("it-*"))
            waits.append(seconds)

    session.queue = RunQueue(lambda: next(readings), clock=Clock())
    assert loop.iterate(session, proposer=fake_values(11))["status"] == "discarded"
    assert waits == [1]


def test_finalize_writes_report_with_holdout_and_proposer_totals(
    session_factory,
    offline_sandbox,
    capsys,
):
    session = session_factory()
    baseline(session)
    loop.run(session, 2, 60, proposer=fake_values(8, 12))
    before = session.best
    path = loop.finalize(session)
    report = path.read_text()
    for text in [
        "Baseline: 10",
        "Final best: 8",
        "try 8",
        "Gain",
        "discarded: 1",
        "Held-out score: 42",
        "Noise history",
        "Proposer calls: 2",
        "Denials: 0",
    ]:
        assert text in report
    assert session.best == before
    assert str(path) in capsys.readouterr().out
    assert not list(session.workspace_dir.glob("it-*"))


def test_proposer_failure_is_recorded_without_training(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    artifacts_before = set((session.workspace_dir / "artifacts").iterdir())

    class Failed:
        def propose(self, session, worktree, brief):
            return ProposalResult("blocked idea", "failed", 3, "guard refused")

    result = loop.iterate(session, proposer=Failed())
    assert result["status"] == "proposer_error"
    assert result["denials"] == 3
    assert set((session.workspace_dir / "artifacts").iterdir()) == artifacts_before
    assert not list(session.workspace_dir.glob("it-*"))
    assert "blocked idea" in (session.session_dir / "tried.md").read_text()


def test_proposer_exception_cleans_up_and_logs(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)

    class Broken:
        def propose(self, session, worktree, brief):
            raise RuntimeError("provider unavailable")

    result = loop.iterate(session, proposer=Broken())
    assert result["status"] == "proposer_error"
    assert "provider unavailable" in result["error"]
    assert not list(session.workspace_dir.glob("it-*"))
    assert session.log_context == {}


def test_no_duplicate_denials_across_remeasurements(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)

    class DeniedThenSucceeded:
        def propose(self, session, worktree, brief):
            edit_value(worktree, 8)
            return ProposalResult("improve after denials", "completed", 3, "IDEA: improve")

    loop.iterate(session, proposer=DeniedThenSucceeded())
    path = loop.finalize(session, print_path=False)
    assert "Proposer calls: 1" in path.read_text()
    assert "Denials: 3" in path.read_text()


@pytest.mark.parametrize("value", [0, -1])
def test_invalid_plateau_limit_is_rejected(session_factory, value):
    with pytest.raises(ValueError, match="plateau"):
        session_factory(plateau_limit=value)


def test_run_starts_and_stops_one_proposer_runtime(session_factory, offline_sandbox, monkeypatch):
    from contextlib import contextmanager

    session = session_factory()
    baseline(session)
    events = []

    @contextmanager
    def runtime(session):
        events.append("start")
        try:
            yield fake_values(8, 12)
        finally:
            events.append("stop")

    monkeypatch.setattr(loop, "SubagentProposer", runtime)
    assert loop.run(session, 2, 60)["iterations"] == 2
    assert events == ["start", "stop"]


@pytest.mark.parametrize("state", ["failed", "cancelled", "timeout"])
def test_failed_proposals_are_errors_without_training(session_factory, offline_sandbox, state):
    session = session_factory()
    baseline(session)
    before = len(list((session.workspace_dir / "artifacts").iterdir()))

    class Pending:
        def propose(self, session, worktree, brief):
            edit_value(worktree, 8)
            return ProposalResult("unverified change", state)

    assert loop.iterate(session, proposer=Pending())["status"] == "proposer_error"
    assert len(list((session.workspace_dir / "artifacts").iterdir())) == before
    assert session.best["metric"] == 10


def test_unfinished_proposals_are_not_scored(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    before = set((session.workspace_dir / "artifacts").iterdir())

    class Pending:
        def propose(self, session, worktree, brief):
            edit_value(worktree, 8)
            return ProposalResult("waiting for input", "working")

    assert loop.iterate(session, proposer=Pending())["status"] == "proposer_incomplete"
    assert set((session.workspace_dir / "artifacts").iterdir()) == before
    assert session.best["metric"] == 10


@pytest.mark.parametrize("state", ["completed", "completed_unverified"])
def test_finished_proposals_with_empty_diff_are_no_change(session_factory, offline_sandbox, state):
    session = session_factory()
    baseline(session)
    before = session.best
    artifacts = set((session.workspace_dir / "artifacts").iterdir())

    class NoEdit:
        def propose(self, session, worktree, brief):
            return ProposalResult("leave the current best", state, 2)

    result = loop.iterate(session, proposer=NoEdit())
    assert result["status"] == "no_change"
    assert result["metric"] is result["seed"] is None
    assert result["changed_files"] == []
    assert result["proposer_state"] == state
    assert result["denials"] == 2
    assert set((session.workspace_dir / "artifacts").iterdir()) == artifacts
    assert session.best == before
    assert log_rows(session)[-1]["status"] == "no_change"
    assert len(log_rows(session)) == session.config.baseline_runs + 1
    assert "no_change" in (session.session_dir / "tried.md").read_text()
    assert "no_change" in (session.session_dir / "state.md").read_text()
    assert not list(session.workspace_dir.glob("it-*"))


@pytest.mark.parametrize("state", ["completed", "completed_unverified"])
def test_finished_proposals_are_scored(session_factory, offline_sandbox, state):
    session = session_factory()
    baseline(session)

    class Finished:
        def propose(self, session, worktree, brief):
            edit_value(worktree, 8)
            return ProposalResult("improve the value", state)

    result = loop.iterate(session, proposer=Finished())
    assert result["status"] == "kept"
    assert result["proposer_state"] == state
    assert session.best["metric"] == 8
    assert (
        len(log_rows(session)) == session.config.baseline_runs + 1 + session.config.remeasure_runs
    )


def test_keep_resets_plateau_streak(session_factory, offline_sandbox):
    session = session_factory(plateau_limit=2)
    baseline(session)
    proposer = fake_values(11, 8, 9, 10, 11)
    result = loop.run(session, 5, 60, proposer=proposer)
    assert result["iterations"] == 4
    assert result["reason"] == "plateau"
    assert result["keeps"] == 1


def test_memory_wait_can_stop_without_creating_worktree(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)

    class Clock:
        def sleep(self, seconds):
            (session.session_dir / "stop").touch()

    session.queue = RunQueue(lambda: 0, clock=Clock())
    proposer = fake_values(8)
    result = loop.run(session, 1, 60, proposer=proposer)
    assert result["reason"] == "stop"
    assert result["iterations"] == proposer.calls == 0
    assert not list(session.workspace_dir.glob("it-*"))


def test_default_plateau_stops_after_ten_non_keeps(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    proposer = fake_values(*range(11, 23))
    result = loop.run(session, 20, 60, proposer=proposer)
    assert result["reason"] == "plateau"
    assert result["iterations"] == proposer.calls == 10
