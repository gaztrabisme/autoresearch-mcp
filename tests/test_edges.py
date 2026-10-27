import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from autoresearch_mcp.loop_core import baseline, score_candidate
from autoresearch_mcp.queue import RunQueue
from autoresearch_mcp.runner import run
from autoresearch_mcp.store import Config, Session
from autoresearch_mcp.worktree import create, git, remove

from .conftest import log_rows
from .test_core import edit_value


def test_evaluator_stderr_cannot_supply_metric(session_factory, offline_sandbox):
    session = session_factory(
        eval_cmd=[
            sys.executable,
            "-c",
            "import sys; print('METRIC score=1', file=sys.stderr)",
        ]
    )
    worktree = create(session, 0)
    assert run(session, worktree, 1)["status"] == "crash"
    remove(session, worktree)


def test_sandbox_wrapper_receives_session_path(session_factory, monkeypatch):
    session = session_factory()
    calls = []

    def wrapper(argv, deny_paths, **kwargs):
        calls.append((argv, deny_paths))
        return argv

    monkeypatch.setattr("subagent.sandbox.wrap_argv", wrapper)
    worktree = create(session, 0)
    assert run(session, worktree, 1)["status"] == "ok"
    assert calls == [(session.config.train_cmd, [str(session.session_dir)])]
    remove(session, worktree)


def test_sandbox_failure_never_falls_back(session_factory, monkeypatch):
    session = session_factory()

    def unavailable(argv, deny_paths, **kwargs):
        raise RuntimeError("sandbox unavailable")

    monkeypatch.setattr("subagent.sandbox.wrap_argv", unavailable)
    worktree = create(session, 0)
    result = run(session, worktree, 1)
    assert result["status"] == "crash"
    assert not (session.workspace_dir / "artifacts" / result["run_id"] / "value.json").exists()
    remove(session, worktree)


@pytest.mark.parametrize("phase", ["train", "eval"])
def test_timeout_kills_descendants(session_factory, offline_sandbox, phase):
    session = session_factory(initial_timeout_s=0.3)
    worktree = create(session, 0)
    marker = session.workspace_dir / "late-descendant"
    child = f"import time; from pathlib import Path; time.sleep(0.7); Path({str(marker)!r}).touch()"
    script = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "time.sleep(10)\n"
    )
    path = worktree / "train.py" if phase == "train" else session.session_dir / "eval.py"
    path.write_text(script)
    assert run(session, worktree, 1)["status"] == "timeout"
    time.sleep(0.8)
    assert not marker.exists()
    remove(session, worktree)


def test_memory_gate_serializes_across_queue_instances():
    entered = threading.Event()
    release = threading.Event()
    second_read = threading.Event()

    def first():
        entered.set()
        assert release.wait(2)
        return {"status": "ok"}

    def read_second():
        second_read.set()
        return 90

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(RunQueue(lambda: 90).run, first, 20)
        assert entered.wait(2)
        two = pool.submit(RunQueue(read_second).run, lambda: {"status": "ok"}, 20)
        try:
            assert not second_read.wait(0.05)
        finally:
            release.set()
        assert one.result()["status"] == "ok"
        assert two.result()["status"] == "ok"
    assert second_read.is_set()


def test_ignored_out_of_scope_edit_is_rejected(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    worktree = create(session, 1)
    (worktree / "__pycache__").mkdir()
    (worktree / "__pycache__" / "bypass.py").write_text("# ignored\n")
    assert score_candidate(session, worktree, "ignored bypass")["status"] == "rejected_scope"


def test_training_cannot_change_the_candidate_being_committed(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    before = session.best
    worktree = create(session, 1)
    edit_value(worktree, 8)
    with (worktree / "train.py").open("a") as output:
        output.write("\np = Path('train.py'); p.write_text(p.read_text() + '\\n# mutated')\n")
    result = score_candidate(session, worktree, "self modifying trainer")
    assert result["status"] == "crash"
    assert "candidate_changed" in result["flags"]
    assert session.best == before
    assert log_rows(session)[-1]["status"] == "crash"


def test_no_change_is_recorded_without_training(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    worktree = create(session, 1)
    result = score_candidate(session, worktree, "no edit")
    assert result["status"] == "no_change"
    assert result["seed"] is None
    assert len(log_rows(session)) == 4


def test_max_direction_keeps_a_higher_median(session_factory, offline_sandbox):
    session = session_factory(direction="max")
    baseline(session)
    worktree = create(session, 1)
    edit_value(worktree, 12)
    assert score_candidate(session, worktree, "maximize")["status"] == "kept"
    assert session.best["metric"] == 12


def test_overlapping_storage_roots_are_rejected(session_factory, tmp_path):
    session = session_factory()
    with pytest.raises(ValueError, match="separate"):
        Session.create("overlap", session.config, home=tmp_path, wt_root=tmp_path / "public")
    with pytest.raises(ValueError, match="autoresearch"):
        Session.create(
            "sensitive",
            session.config,
            home=tmp_path / "alternate",
            wt_root=os.path.expanduser("~/.autoresearch/workspaces"),
        )


def test_branch_best_mismatch_fails_visibly(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    session.write_best({**session.best, "commit": "0" * 40})
    with pytest.raises(RuntimeError, match="disagree"):
        create(session, 1)


def test_max_iter_prevents_another_candidate(session_factory, offline_sandbox):
    session = session_factory(max_iter=1)
    baseline(session)
    worktree = create(session, 1)
    score_candidate(session, worktree, "no edit")
    worktree = create(session, 2)
    with pytest.raises(ValueError, match="max_iter"):
        score_candidate(session, worktree, "one too many")
    remove(session, worktree)


def test_reload_preserves_best_and_append_only_logs(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    existing = (session.session_dir / "log.jsonl").read_bytes()
    loaded = Session.load(session.name, queue=RunQueue(lambda: 90))
    assert loaded.best == session.best
    worktree = create(loaded, 1)
    edit_value(worktree, 11)
    score_candidate(loaded, worktree, "worse after reload")
    assert (loaded.session_dir / "log.jsonl").read_bytes().startswith(existing)
    assert len(log_rows(loaded)) == 4


def test_commit_failure_is_recorded(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    before = session.best
    # An empty local identity overrides any owner's global Git identity.
    git(session.config.target_repo, "config", "user.name", "")
    git(session.config.target_repo, "config", "user.email", "")
    worktree = create(session, 1)
    edit_value(worktree, 8)
    result = score_candidate(session, worktree, "cannot commit")
    assert result["status"] == "crash"
    assert "keep failed" in result["error"]
    assert len(log_rows(session)) == 7
    assert session.best == before


def test_config_defaults_and_validation(session_factory):
    config = session_factory().config
    defaults = Config(config.target_repo, config.scope, config.train_cmd, config.eval_cmd)
    assert defaults.baseline_runs == 5
    assert defaults.remeasure_runs == 3
    assert defaults.noise_k == 2
    assert defaults.timeout_factor == 2
    assert defaults.memory_free_min_pct == 20
    with pytest.raises(ValueError, match="direction"):
        replace(config, direction="sideways")
    with pytest.raises(ValueError, match="scope"):
        replace(config, scope=["../outside.py"])
    with pytest.raises(ValueError, match="initial_timeout_s"):
        replace(config, initial_timeout_s=0)


def test_baseline_uses_distinct_seeds_and_median(session_factory, offline_sandbox):
    script = (
        "import os; from pathlib import Path; "
        "Path(os.environ['AUTORESEARCH_OUT'], 'value.json').write_text("
        "os.environ['AUTORESEARCH_SEED'])"
    )
    session = session_factory(baseline_runs=5, train_cmd=[sys.executable, "-c", script])
    assert baseline(session)["metric"] == 3
    assert session.best["noise_mad"] == 1
    assert session.best["threshold"] == 2
    assert [row["seed"] for row in log_rows(session)] == [1, 2, 3, 4, 5]
    assert git(session.config.target_repo, "rev-parse", session.branch) == session.best["commit"]
