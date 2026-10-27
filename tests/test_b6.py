"""Regression checks for the B6 trust boundaries and interrupted operations."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from subagent.sandbox import build_profile

from autoresearch_mcp import loop, worktree
from autoresearch_mcp.loop_core import baseline, score_candidate
from autoresearch_mcp.proposer import ClaudeCliProposer, FakeProposer, SubagentProposer
from autoresearch_mcp.runner import run
from autoresearch_mcp.server import create_server
from autoresearch_mcp.service import RunManager
from autoresearch_mcp.store import Session

from .test_b3_proposer import Backend
from .test_core import edit_value


def test_training_write_allow_and_fresh_tmpdir(session_factory, monkeypatch):
    session = session_factory()
    calls = []

    def wrap(argv, deny_paths, *, write_allow):
        assert deny_paths == [str(session.session_dir)]
        assert Path(write_allow[1]).is_dir()
        assert write_allow[2] == "/dev"
        assert len(write_allow) == 3
        assert all(
            not session.snapshot_dir.is_relative_to(Path(allowed)) for allowed in write_allow
        )
        calls.append(write_allow)
        return argv

    monkeypatch.setattr("subagent.sandbox.wrap_argv", wrap)
    path = worktree.create(session, 0)
    (path / "train.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['AUTORESEARCH_OUT'], 'tmp').write_text(os.environ['TMPDIR'])\n"
        "Path(os.environ['TMPDIR'], 'scratch').write_text('temporary')\n"
        "Path(os.environ['AUTORESEARCH_OUT'], 'value.json').write_text('10')\n"
    )
    try:
        for seed in (1, 2):
            result = run(session, path, seed)
            assert result["status"] == "ok"
            assert calls[-1][0] == str(session.workspace_dir / "artifacts" / result["run_id"])
            assert (Path(result["artifact"]) / "tmp").read_text() == calls[-1][1]
            assert not Path(calls[-1][1]).exists()
        assert calls[0][1] != calls[1][1]
    finally:
        worktree.remove(session, path)


def test_training_profile_denies_writes_before_allowing_outputs(tmp_path):
    private, artifact, temporary = (str(tmp_path / name) for name in ("private", "out", "tmp"))
    profile = build_profile([private], write_allow=[artifact, temporary, "/dev"])
    assert '(deny file-write* (subpath "/"))' in profile
    allow = f'(allow file-write* (subpath "{artifact}") (subpath "{temporary}") (subpath "/dev"))'
    assert allow in profile
    assert profile.index('(deny file-write* (subpath "/"))') < profile.index(allow)
    assert profile.index(allow) < profile.index(f'(deny file-write* (subpath "{private}"))')


@pytest.mark.macos_sandbox
def test_native_training_cannot_write_repo_or_git(session_factory):
    session = session_factory()
    path = worktree.create(session, 0)
    git_config = session.config.target_repo / ".git" / "config"
    original = git_config.read_bytes()
    (path / "train.py").write_text(
        "import os\nfrom pathlib import Path\n"
        f"targets = [{str(git_config)!r}, 'other.txt']\n"
        "for target in targets:\n"
        "    try:\n"
        "        Path(target).write_text('corrupt')\n"
        "    except PermissionError:\n"
        "        continue\n"
        "    raise RuntimeError('unauthorized write succeeded')\n"
        "Path(os.environ['AUTORESEARCH_OUT'], 'value.json').write_text('10')\n"
        "Path(os.environ['TMPDIR'], 'scratch').touch()\n"
    )
    try:
        assert run(session, path, 1)["status"] == "ok"
        assert git_config.read_bytes() == original
        assert (path / "other.txt").read_text() == "unchanged\n"
    finally:
        worktree.remove(session, path)


@pytest.mark.parametrize(
    "kind", ["symlink", "directory_symlink", "hardlink", "fifo", "root_symlink"]
)
def test_artifact_links_and_special_files_crash(session_factory, offline_sandbox, kind):
    session = session_factory()
    private = session.session_dir / "validation.json"
    private.write_text("0")
    path = worktree.create(session, 0)
    attack = {
        "symlink": f"(out / 'value.json').symlink_to({str(private)!r})",
        "directory_symlink": f"(out / 'nested').symlink_to({str(session.session_dir)!r})",
        "hardlink": f"os.link({str(private)!r}, out / 'value.json')",
        "fifo": "os.mkfifo(out / 'value.json')",
        "root_symlink": f"out.rmdir(); out.symlink_to({str(session.session_dir)!r})",
    }[kind]
    (path / "train.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "out = Path(os.environ['AUTORESEARCH_OUT'])\n" + attack + "\n"
    )
    try:
        result = run(session, path, 1)
        assert result["status"] == "crash"
        assert result["error"] == "artifact contains a link or special file"
        assert not (session.session_dir / "runs" / result["run_id"] / "eval.stdout").exists()
    finally:
        worktree.remove(session, path)


def test_detached_writer_cannot_change_private_artifact_snapshot(
    session_factory, offline_sandbox
):
    session = session_factory(initial_timeout_s=10)
    path = worktree.create(session, 0)
    # The escaped descendant waits until the evaluator has started on the snapshot.
    ready = session.workspace_dir / "eval-ready"
    done = session.workspace_dir / "writer-done"
    pid_file = session.workspace_dir / "writer-pid"
    child = (
        "import sys, time\nfrom pathlib import Path\n"
        f"while not Path({str(ready)!r}).exists(): time.sleep(0.01)\n"
        "Path(sys.argv[1]).write_text('0')\n"
        f"Path({str(done)!r}).touch()\n"
    )
    (path / "train.py").write_text(
        "import os, subprocess, sys\nfrom pathlib import Path\n"
        "out = Path(os.environ['AUTORESEARCH_OUT'], 'value.json')\nout.write_text('10')\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child!r}, str(out)], "
        "start_new_session=True)\n"
        f"Path({str(pid_file)!r}).write_text(str(child.pid))\n"
    )
    (session.session_dir / "eval.py").write_text(
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(ready)!r}).touch()\n"
        f"while not Path({str(done)!r}).exists(): time.sleep(0.01)\n"
        "value = Path(os.environ['AUTORESEARCH_ARTIFACT'], 'value.json').read_text()\n"
        "print(f'METRIC score={value}')\n"
    )
    try:
        result = run(session, path, 1)
        assert result["status"] == "ok"
        assert result["metric"] == 10
        private = Path(result["artifact"])
        public = session.workspace_dir / "artifacts" / result["run_id"]
        assert private == session.snapshot_dir / result["run_id"]
        assert not private.is_relative_to(session.session_dir)
        assert not private.is_relative_to(session.workspace_dir)
        assert (public / "value.json").read_text() == "0"
        assert (private / "value.json").read_text() == "10"
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), 9)
            except ProcessLookupError:
                pass
        worktree.remove(session, path)


def test_artifact_snapshot_copies_nested_regular_files(session_factory, offline_sandbox):
    session = session_factory()
    path = worktree.create(session, 0)
    with (path / "train.py").open("a") as stream:
        stream.write(
            "nested = Path(os.environ['AUTORESEARCH_OUT'], 'nested')\n"
            "nested.mkdir()\n(nested / 'weights').write_bytes(b'weights')\n"
        )
    try:
        result = run(session, path, 1)
        assert result["status"] == "ok"
        public = session.workspace_dir / "artifacts" / result["run_id"] / "nested" / "weights"
        private = Path(result["artifact"]) / "nested" / "weights"
        assert private.read_bytes() == public.read_bytes() == b"weights"
        assert private.stat().st_ino != public.stat().st_ino
    finally:
        worktree.remove(session, path)


def test_git_helper_disables_execution_config(monkeypatch, tmp_path):
    execute = Mock(return_value=SimpleNamespace(stdout="ok\n"))
    monkeypatch.setattr(worktree.subprocess, "run", execute)
    assert worktree.git(tmp_path, "status") == "ok"
    argv = execute.call_args.args[0]
    assert argv == [
        "git", "-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null",
        "-c", "core.sshCommand=", "-c", "protocol.allow=never", "-c", "diff.external=",
        "-c", "core.pager=cat", "-C", str(tmp_path), "status",
    ]
    assert execute.call_args.kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert execute.call_args.kwargs["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"


@pytest.mark.parametrize("kind", ["rewrite", "symlink", "directory", "commondir"])
def test_changed_git_metadata_crashes_before_scope_check(
    session_factory, offline_sandbox, monkeypatch, kind
):
    session = session_factory()
    baseline(session)
    calls = []
    real_execute = subprocess.run

    def execute(argv, **kwargs):
        if argv[0] == "git":
            position = argv.index("-C")
            calls.append((Path(argv[position + 1]), tuple(argv[position + 2:])))
        return real_execute(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", execute)

    def attack(path):
        pointer = path / ".git"
        if kind == "commondir":
            admin = session.config.target_repo / ".git" / "worktrees" / path.name
            (admin / "commondir").write_text(str(session.config.target_repo))
        else:
            pointer.unlink()
            if kind == "rewrite":
                pointer.write_text(f"gitdir: {session.config.target_repo / '.git'}\n")
            elif kind == "symlink":
                pointer.symlink_to(session.config.target_repo / ".git" / "config")
            else:
                pointer.mkdir()
        calls.clear()

    result = loop.iterate(session, proposer=FakeProposer([("metadata attack", attack)]))
    assert result["status"] == "crash"
    assert result["error"] == "git metadata changed"
    assert result["seed"] is None
    path = session.workspace_dir / "it-1"
    assert not path.exists()
    worktree_calls = [args for repo, args in calls if repo == path]
    # The common-dir check is allowed only after the pointer passes its static check.
    allowed = ("rev-parse", "--path-format=absolute", "--git-common-dir")
    assert all(args == allowed for args in worktree_calls)
    if kind != "commondir":
        assert not worktree_calls


@pytest.mark.macos_sandbox
def test_native_git_ignores_fsmonitor_and_hooks(session_factory, offline_sandbox):
    session = session_factory()
    marker = session.workspace_dir / "executed"
    hook = session.workspace_dir / "hook"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o700)
    hooks = session.workspace_dir / "hooks"
    hooks.mkdir()
    shutil.copy(hook, hooks / "pre-commit")
    worktree.git(session.config.target_repo, "config", "core.fsmonitor", str(hook))
    worktree.git(session.config.target_repo, "config", "core.hooksPath", str(hooks))
    assert baseline(session)["status"] == "baseline"
    path = worktree.create(session, 1)
    edit_value(path, 8)
    assert score_candidate(session, path, "safe keep")["status"] == "kept"
    assert not marker.exists()


def test_subagent_deadline_closes_timed_out_agent(session_factory, tmp_path):
    session = session_factory(proposer_timeout_s=0.01)
    backend = Backend(tmp_path / "trace.jsonl")
    ready = Mock()
    ready.wait.return_value = False
    backend.delegate = Mock(return_value=SimpleNamespace(agent_id="slow", await_ready=ready))
    with SubagentProposer(session, server=backend) as proposer:
        result = proposer.propose(session, tmp_path, "brief")
    ready.wait.assert_called_once_with(timeout=0.01)
    assert result.run_state == "timeout"
    assert backend.closed == ["slow"]
    assert backend.stopped == 1


@pytest.mark.parametrize("timeout", [False, True])
def test_claude_cli_kills_descendants_on_exit_and_timeout(
    session_factory, offline_sandbox, monkeypatch, timeout
):
    session = session_factory()
    marker = session.workspace_dir / "late-write"
    executable = session.workspace_dir / "fake-claude"
    child = f"import time; from pathlib import Path; time.sleep(0.8); Path({str(marker)!r}).touch()"
    executable.write_text(
        f"#!{sys.executable}\nimport subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        + ("time.sleep(10)\n" if timeout else "print('{\"result\": \"IDEA: done\"}')\n")
    )
    executable.chmod(0o700)
    monkeypatch.setenv("AUTORESEARCH_CLAUDE_BIN", str(executable))
    result = ClaudeCliProposer(timeout_s=0.3 if timeout else 3).propose(
        session, session.config.target_repo, "brief"
    )
    assert result.run_state == ("timeout" if timeout else "completed")
    time.sleep(0.9)
    assert not marker.exists()


def test_confirmation_median_excludes_screening_run(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    before = session.best
    path = worktree.create(session, 1)
    edit_value(path, "{4: 0, 5: 1, 6: 11, 7: 11}[int(os.environ['AUTORESEARCH_SEED'])]")
    result = score_candidate(session, path, "lucky screening")
    assert result["status"] == "discarded"
    assert result["metric"] == 11
    assert session.best == before
    assert [row["metric"] for row in session.rows() if row["iteration"] == 1] == [0, 1, 11, 11]


def test_kept_best_statistics_use_confirmations_only(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    path = worktree.create(session, 1)
    edit_value(path, "{4: 0, 5: 6, 6: 8, 7: 9}[int(os.environ['AUTORESEARCH_SEED'])]")
    result = score_candidate(session, path, "confirmed gain")
    assert result["status"] == "kept"
    assert result["metric"] == 8
    assert session.best["metrics"] == [6, 8, 9]
    assert session.best["seeds"] == [5, 6, 7]
    assert session.best["noise_mad"] == 1
    assert len(session.best["wall_times"]) == 3


@pytest.mark.parametrize("fallback", [False, True])
def test_resume_abandons_unlogged_worktrees(
    session_factory, offline_sandbox, monkeypatch, fallback
):
    session = session_factory()
    baseline(session)
    real_git = worktree.git

    def git(repo, *args, **kwargs):
        if fallback and args[:2] == ("worktree", "remove"):
            raise subprocess.CalledProcessError(1, "git")
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(worktree, "git", git)
    # Repeat the crash/reload/recovery exercise, preserving numbering and the best.
    before = session.best
    for number in (1, 3):
        abandoned = worktree.create(session, number)
        (abandoned / "partial").write_text("interrupted")
        loaded = Session.load(session.name, queue=session.queue)
        result = loop.iterate(loaded, proposer=FakeProposer([("no edit", lambda path: None)]))
        assert result["iteration"] == number + 1
        assert result["status"] == "no_change"
        assert not abandoned.exists()
        row = next(row for row in loaded.rows() if row["iteration"] == number)
        assert row["status"] == "abandoned"
        assert row["seed"] is None
        assert loaded.best == before


def test_append_repairs_torn_tail_repeatedly_under_lock(session_factory):
    session = session_factory()
    log = session.session_dir / "log.jsonl"
    dropped = b""
    for number, tail in enumerate((b'{"iter', b'{"idea":"\xe2\x82', b'not-json'), 1):
        with log.open("ab") as stream:
            stream.write(tail)
        dropped += tail
        loaded = Session.load(session.name, queue=session.queue)
        loaded.append({"iteration": number, "status": "abandoned", "metric": None, "idea": "x"})
        assert [row["iteration"] for row in loaded.rows()] == list(range(1, number + 1))
        assert (session.session_dir / "log.torn").read_bytes() == dropped
        assert log.read_bytes().endswith(b"\n")
        assert len([json.loads(line) for line in log.read_bytes().splitlines()]) == number


def test_append_waits_for_mutation_lock(session_factory):
    session = session_factory()
    started, finished = threading.Event(), threading.Event()

    def append():
        started.set()
        session.append({"iteration": 1, "status": "abandoned", "metric": None, "idea": "x"})
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with session.queue.lock:
            future = executor.submit(append)
            assert started.wait(2)
            assert not finished.wait(0.1)
            assert session.rows() == []
        future.result(timeout=2)
    assert finished.is_set()
    assert session.rows()[0]["status"] == "abandoned"


@pytest.mark.parametrize("tool", ["baseline", "iterate", "finalize"])
def test_cancelled_foreground_call_holds_admission_until_worker_exits(
    session_factory, monkeypatch, tool
):
    session = session_factory()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    manager = RunManager()

    def blocking(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(5)
            if tool == "finalize":
                report = session.session_dir / "report.md"
                report.write_text("done")
                return report
            return {"status": "done"}
        finally:
            finished.set()

    module = "loop_core" if tool == "baseline" else "loop"
    monkeypatch.setattr(f"autoresearch_mcp.server.{module}.{tool}", blocking)
    monkeypatch.setattr("autoresearch_mcp.server.Session.load", lambda name: session)
    server = create_server(manager)

    async def exercise():
        task = asyncio.create_task(server.call_tool(tool, {"name": session.name}))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(2):
            with pytest.raises(RuntimeError, match="already running"), manager.exclusive():
                pass
        with pytest.raises(RuntimeError, match="already running"):
            manager.start(session)
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        # Wait for worker's finally block to release admission, without timing guesses.
        for _ in range(100):
            with manager._lock:
                busy = manager._busy
            if not busy:
                break
            await asyncio.sleep(0.01)
        with manager.exclusive():
            pass

    try:
        asyncio.run(exercise())
    finally:
        release.set()
