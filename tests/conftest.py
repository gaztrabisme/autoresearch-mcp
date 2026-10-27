import json
import subprocess
import sys

import pytest

from autoresearch_mcp.queue import RunQueue
from autoresearch_mcp.store import Config, Session
from autoresearch_mcp.worktree import git

sys.dont_write_bytecode = True


def pytest_collection_modifyitems(items):
    try:
        probe = subprocess.run(
            ["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
            capture_output=True,
            timeout=5,
        )
        reason = (
            (probe.stderr.decode(errors="replace").strip() or f"exit {probe.returncode}")
            if probe.returncode
            else ""
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        reason = str(exc)
    if reason:
        skip = pytest.mark.skip(reason=f"sandbox-exec unavailable: {reason}")
        for item in items:
            if "macos_sandbox" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_HOME", str(tmp_path / "private"))
    monkeypatch.setenv("AUTORESEARCH_WT_ROOT", str(tmp_path / "public"))
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "tests@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "train.py").write_text(
        "import json, os\nfrom pathlib import Path\n"
        "value = 10\n"
        "Path(os.environ['AUTORESEARCH_OUT'], 'value.json').write_text(json.dumps(value))\n"
        "print('METRIC score=0')\n"
    )
    (repo / "other.txt").write_text("unchanged\n")
    (repo / ".gitignore").write_text("__pycache__/\n")
    git(repo, "add", ".")
    git(repo, "commit", "--no-gpg-sign", "-qm", "fixture")

    def create(name="test", **overrides):
        options = dict(
            target_repo=repo,
            scope=["train.py"],
            metric="score",
            direction="min",
            steps=10,
            noise_k=2,
            min_gain=0.5,
            baseline_runs=3,
            remeasure_runs=3,
            initial_timeout_s=10,
            timeout_factor=100,
            train_cmd=[sys.executable, "train.py"],
            eval_cmd=[sys.executable, "eval.py"],
            holdout_cmd=[sys.executable, "holdout.py"],
        )
        options.update(overrides)
        session = Session.create(
            name,
            Config(**options),
            goal="Reduce score",
            queue=RunQueue(lambda: 90),
        )
        evaluator = (
            "import json, os\nfrom pathlib import Path\n"
            "value = json.loads(Path(os.environ['AUTORESEARCH_ARTIFACT'], "
            "'value.json').read_text())\n"
            "print(f'METRIC score={value}')\nprint('METRIC tokens_per_s=123.5')\n"
        )
        (session.session_dir / "eval.py").write_text(evaluator)
        (session.session_dir / "holdout.py").write_text("print('METRIC score=42')\n")
        return session

    return create


@pytest.fixture
def offline_sandbox(monkeypatch):
    # Only the OS boundary is substituted. Marked tests exercise the real wrapper.
    monkeypatch.setattr("subagent.sandbox.wrap_argv", lambda argv, deny_paths: argv)


def log_rows(session):
    lines = (session.session_dir / "log.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]
