import sys
from pathlib import Path

import pytest
from subagent.guard.classify import SENSITIVE_HOME, is_sensitive

from autoresearch_mcp.loop_core import baseline, finalize, score_candidate
from autoresearch_mcp.noise import improves, keep_threshold, mad
from autoresearch_mcp.queue import RunQueue, parse_memory_free_pct
from autoresearch_mcp.runner import run
from autoresearch_mcp.store import Session
from autoresearch_mcp.worktree import create, git, remove

from .conftest import log_rows


def edit_value(worktree, value):
    path = worktree / "train.py"
    path.write_text(path.read_text().replace("value = 10", f"value = {value}"))


def test_fake_metric_from_trainer_is_ignored(session_factory, offline_sandbox):
    session = session_factory()
    worktree = create(session, 0)
    result = run(session, worktree, 1)
    assert result["status"] == "ok"
    assert result["metric"] == 10
    assert result["extra_metrics"] == {"tokens_per_s": 123.5}
    stdout = next((session.session_dir / "runs").glob("*/train.stdout")).read_text()
    assert "METRIC score=0" in stdout
    remove(session, worktree)


@pytest.mark.macos_sandbox
def test_trainer_cannot_read_session_secret(session_factory):
    session = session_factory()
    secret = "private-eval-canary-5937"
    secret_path = session.session_dir / "secret.txt"
    secret_path.write_text(secret)
    worktree = create(session, 0)
    (worktree / "train.py").write_text(
        f"from pathlib import Path\nprint(Path({str(secret_path)!r}).read_text())\n"
    )
    result = run(session, worktree, 1)
    assert result["status"] == "crash"
    for path in session.session_dir.rglob("*"):
        if path.is_file() and path != secret_path:
            assert secret not in path.read_text()
    remove(session, worktree)


def test_zero_mad_uses_min_gain():
    assert mad([10, 10, 10]) == 0
    assert keep_threshold([10, 10, 10], 2, 0.5) == 0.5
    assert not improves(9.5, 10, 0.5, "min")
    assert improves(9.4, 10, 0.5, "min")
    assert not improves(10.5, 10, 0.5, "max")
    assert improves(10.6, 10, 0.5, "max")


def test_timeout_is_inconclusive(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    before = session.best
    worktree = create(session, 1)
    (worktree / "train.py").write_text("import time\ntime.sleep(20)\n")
    session.write_best({**before, "wall_times": [0.001]})
    result = score_candidate(session, worktree, "slow run")
    assert result["status"] == "timeout"
    assert not result["kept"]
    assert session.best["commit"] == before["commit"]
    assert log_rows(session)[-1]["status"] == "timeout"
    assert "inconclusive" in (session.session_dir / "tried.md").read_text()


def test_out_of_scope_edit_rejected(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    worktree = create(session, 1)
    (worktree / "other.txt").write_text("tampered\n")
    result = score_candidate(session, worktree, "edit evaluator input")
    assert result["status"] == "rejected_scope"
    assert result["changed_files"] == ["other.txt"]
    assert len(log_rows(session)) == 4
    assert not worktree.exists()


def test_workspace_not_under_protected_path(session_factory):
    session = session_factory()
    worktree = create(session, 1)
    assert not worktree.is_relative_to(session.session_dir)
    assert not worktree.is_relative_to(Path.home() / ".autoresearch")
    assert is_sensitive(worktree) is None
    remove(session, worktree)


def test_keep_requires_remeasure_median(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    worktree = create(session, 1)
    # Fresh seeds are 4, 5, 6, 7: one lucky run, then three worse runs.
    edit_value(worktree, "1 if int(os.environ['AUTORESEARCH_SEED']) == 4 else 11")
    before = session.best
    result = score_candidate(session, worktree, "lucky first measurement")
    assert result["status"] == "discarded"
    assert result["metric"] == 11
    assert len(log_rows(session)) == 7
    assert session.best == before
    assert len({row["seed"] for row in log_rows(session)}) == 7


def test_discard_leaves_branch_unchanged(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    before = git(session.config.target_repo, "rev-parse", session.branch)
    worktree = create(session, 1)
    edit_value(worktree, 12)
    assert score_candidate(session, worktree, "worse value")["status"] == "discarded"
    assert git(session.config.target_repo, "rev-parse", session.branch) == before
    assert not worktree.exists()


def test_log_rows_have_required_fields(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    worktree = create(session, 1)
    edit_value(worktree, 8)
    result = score_candidate(session, worktree, "better value")
    assert result["status"] == "kept"
    required = {
        "ts",
        "iteration",
        "idea",
        "status",
        "metric",
        "threshold",
        "noise_mad",
        "best_before",
        "kept",
        "changed_files",
        "hunks",
        "flags",
        "wall_s",
        "extra_metrics",
        "memory_free_pct",
        "seed",
    }
    rows = log_rows(session)
    assert len(rows) == 7
    assert all(required <= row.keys() for row in rows)
    assert all(row["memory_free_pct"] == 90 for row in rows)
    assert all(row["threshold"] == 0.5 for row in rows[3:])
    assert session.best["metric"] == 8
    assert session.best["noise_mad"] == 0
    assert len(session.best["wall_times"]) == session.config.remeasure_runs
    assert git(session.config.target_repo, "log", "-1", "--format=%s", session.branch) == (
        "better value"
    )
    assert "value = 8" in git(session.config.target_repo, "show", f"{session.branch}:train.py")
    assert "value = 10" in (session.config.target_repo / "train.py").read_text()
    assert "better value" in (session.session_dir / "state.md").read_text()


def test_memory_gate_waits_then_runs():
    readings = iter([10, 19, 50])
    events = []

    class Clock:
        def sleep(self, seconds):
            events.append(("wait", seconds))

    queue = RunQueue(lambda: next(readings), clock=Clock(), poll_interval_s=0.1)
    result = queue.run(lambda: events.append("run") or {"status": "ok"}, 20)
    assert events == [("wait", 0.1), ("wait", 0.1), "run"]
    assert result["memory_free_pct"] == 50
    assert parse_memory_free_pct("System-wide memory free percentage: 37%\n") == 37


def test_sensitive_home_contains_autoresearch():
    assert ".autoresearch" in SENSITIVE_HOME


def test_config_and_best_reload(session_factory):
    session = session_factory()
    loaded = Session.load(session.name)
    assert loaded.config == session.config
    assert loaded.workspace_dir == session.workspace_dir
    assert loaded.config.goal == "Reduce score"


@pytest.mark.parametrize("value", ["nan", "inf", "bad", ""])
def test_invalid_eval_metric_crashes(session_factory, offline_sandbox, value):
    session = session_factory()
    (session.session_dir / "eval.py").write_text(f"print('METRIC score={value}')\n")
    worktree = create(session, 0)
    assert run(session, worktree, 1)["status"] == "crash"
    remove(session, worktree)


def test_failed_remeasure_is_inconclusive(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    before = session.best
    worktree = create(session, 1)
    edit_value(worktree, 8)
    with (worktree / "train.py").open("a") as file:
        file.write("\nif int(os.environ['AUTORESEARCH_SEED']) == 5: raise RuntimeError('fail')\n")
    assert score_candidate(session, worktree, "unstable")["status"] == "crash"
    assert session.best == before
    assert len(log_rows(session)) == 5


def test_holdout_only_at_finalize(session_factory, offline_sandbox):
    session = session_factory()
    (session.session_dir / "holdout.py").write_text(
        "from pathlib import Path\nPath('holdout-used').touch()\nprint('METRIC score=42')\n"
    )
    baseline(session)
    before = session.best
    assert not (session.session_dir / "holdout-used").exists()
    assert finalize(session)["metric"] == 42
    assert (session.session_dir / "holdout-used").exists()
    assert session.best == before
    assert log_rows(session)[-1]["phase"] == "holdout"


def test_untracked_out_of_scope_file_rejected(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    worktree = create(session, 1)
    (worktree / "injected.py").write_text("print('oops')\n")
    assert score_candidate(session, worktree, "new file")["status"] == "rejected_scope"


def test_multi_change_and_repeated_idea_are_recorded(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    for iteration in (1, 2):
        worktree = create(session, iteration)
        edit_value(worktree, 11)
        with (worktree / "train.py").open("a") as file:
            file.write("\n# independent region\n")
        result = score_candidate(session, worktree, "same idea")
        assert "multi_change" in result["flags"]
    assert "repeated_idea" in result["flags"]
    assert (session.session_dir / "tried.md").read_text().count("same idea") == 2


def test_baseline_failure_does_not_create_best(session_factory, offline_sandbox):
    session = session_factory(eval_cmd=[sys.executable, "-c", "raise RuntimeError('bad')"])
    assert baseline(session)["status"] == "crash"
    assert session.best is None
    assert len(log_rows(session)) == 1


def test_scope_globs_are_rooted_and_slash_aware():
    from autoresearch_mcp.worktree import in_scope

    assert in_scope("src/a.py", ["src/**/*.py"])
    assert in_scope("src/deep/a.py", ["src/**/*.py"])
    assert not in_scope("else/src/a.py", ["src/**/*.py"])
    assert not in_scope("src/deep/a.py", ["src/*.py"])


def test_best_noise_and_timeout_follow_current_best(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    worktree = create(session, 1)
    edit_value(worktree, "int(os.environ['AUTORESEARCH_SEED'])")
    assert score_candidate(session, worktree, "variable gain")["status"] == "kept"
    assert session.best["metric"] == 6
    assert session.best["noise_mad"] == 1
    assert session.best["threshold"] == 2
    import statistics

    assert session.timeout_s == 100 * statistics.median(session.best["wall_times"])


def test_eval_nonzero_exit_crashes_even_with_metric(session_factory, offline_sandbox):
    session = session_factory(eval_cmd=[sys.executable, "-c", "print('METRIC score=1'); exit(2)"])
    worktree = create(session, 0)
    assert run(session, worktree, 1)["status"] == "crash"
    remove(session, worktree)


def test_runner_passes_training_contract(session_factory, offline_sandbox):
    session = session_factory(steps=123)
    (session.workspace_dir / "data" / "train.txt").write_text("training")
    worktree = create(session, 0)
    (worktree / "train.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "assert os.environ['AUTORESEARCH_STEPS'] == '123'\n"
        "assert os.environ['AUTORESEARCH_SEED'] == '7'\n"
        "assert Path(os.environ['AUTORESEARCH_DATA'], 'train.txt').read_text() == 'training'\n"
        "Path(os.environ['AUTORESEARCH_OUT'], 'value.json').write_text('9')\n"
    )
    assert run(session, worktree, 7)["metric"] == 9
    remove(session, worktree)
