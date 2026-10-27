"""Snapshots remain readable by candidate evaluation children, denied to proposers."""

import os
import stat
import sys
from pathlib import Path

import pytest

from autoresearch_mcp.loop_core import baseline
from autoresearch_mcp.proposer import SubagentProposer
from autoresearch_mcp.store import Session, _paths


def test_snapshot_root_default(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTORESEARCH_SNAP_ROOT", raising=False)
    private, public, snapshots = _paths(
        "example", home=tmp_path / "private", wt_root=tmp_path / "public"
    )
    assert snapshots == (Path.home() / ".cache/autoresearch-snap/example").resolve()
    assert not snapshots.is_relative_to(private.parent)
    assert not snapshots.is_relative_to(public.parent)


def test_snapshot_root_roundtrips_and_has_private_permissions(session_factory, tmp_path):
    session = session_factory()
    expected = tmp_path / "snapshots" / session.name
    assert session.snapshot_dir == expected
    assert Session.load(session.name).snapshot_dir == expected
    assert stat.S_IMODE(expected.stat().st_mode) == 0o700
    assert stat.S_IMODE(expected.parent.stat().st_mode) == 0o700
    # Library overrides use the same resolution and validation on create/load.
    custom_root = tmp_path / "custom-snapshots"
    custom = Session.create("custom", session.config, snap_root=custom_root)
    assert custom.snapshot_dir == custom_root / "custom"
    assert Session.load("custom", snap_root=custom_root).snapshot_dir == custom.snapshot_dir


@pytest.mark.parametrize("root_name", ["private", "public"])
@pytest.mark.parametrize("relation", ["equal", "inside", "ancestor"])
def test_snapshot_root_overlap_rejected_on_create_and_load(
    session_factory, tmp_path, monkeypatch, root_name, relation
):
    session = session_factory()
    other_root = tmp_path / root_name
    root = {
        "equal": other_root,
        "inside": other_root / "another-session" / "snapshots",
        "ancestor": tmp_path,
    }[relation]
    monkeypatch.setenv("AUTORESEARCH_SNAP_ROOT", str(root))
    with pytest.raises(ValueError, match="roots must be separate"):
        Session.create("overlap", session.config)
    with pytest.raises(ValueError, match="roots must be separate"):
        Session.load(session.name)
    assert not (session.session_dir.parent / "overlap").exists()


@pytest.mark.parametrize("root_name", ["private", "public"])
def test_snapshot_root_symlink_overlap_rejected(session_factory, tmp_path, root_name):
    session = session_factory()
    alias = tmp_path / "snapshot-alias"
    alias.symlink_to(tmp_path / root_name, target_is_directory=True)
    with pytest.raises(ValueError, match="roots must be separate"):
        Session.create("aliased", session.config, snap_root=alias)
    with pytest.raises(ValueError, match="roots must be separate"):
        Session.load(session.name, snap_root=alias)


def _install_child_evaluator(session, *, sandboxed):
    secret = session.session_dir / "secret"
    secret.write_text("private reference")
    child = (
        "import sys\nfrom pathlib import Path\n"
        + (
            "try:\n"
            "    Path(sys.argv[2]).read_text()\n"
            "except PermissionError:\n"
            "    pass\n"
            "else:\n"
            "    raise RuntimeError('candidate could read the session')\n"
            if sandboxed else ""
        )
        + "print(Path(sys.argv[1], 'value.json').read_text())\n"
    )
    evaluator = (
        "import os, subprocess, sys\nfrom pathlib import Path\n"
        "session = Path.cwd()\n"
        "artifact = Path(os.environ['AUTORESEARCH_ARTIFACT'])\n"
        "assert not artifact.is_relative_to(session)\n"
        f"argv = [sys.executable, '-I', '-B', '-c', {child!r}, str(artifact), "
        "str(session / 'secret')]\n"
        + (
            "from subagent.sandbox import wrap_argv\n"
            "argv = wrap_argv(argv, [str(session)])\n"
            if sandboxed else ""
        )
        + "result = subprocess.run(argv, cwd=artifact, capture_output=True,\n"
        "                        text=True, check=True)\n"
        "print(f'METRIC score={float(result.stdout)}')\n"
    )
    (session.session_dir / "eval.py").write_text(evaluator)


def test_evaluator_child_reads_external_snapshot_and_logs_path(session_factory, offline_sandbox):
    session = session_factory(baseline_runs=1)
    _install_child_evaluator(session, sandboxed=False)
    result = baseline(session)
    assert result["status"] == "baseline"
    assert result["metric"] == 10
    snapshot = session.snapshot_dir / result["run_id"]
    assert snapshot.parent.parent == Path(os.environ["AUTORESEARCH_SNAP_ROOT"])
    assert not snapshot.is_relative_to(session.session_dir.parent)
    assert not snapshot.is_relative_to(session.workspace_dir.parent)
    assert snapshot.is_dir()
    assert result["artifact"] == result["snapshot"] == str(snapshot)
    row = session.rows()[0]
    assert row["artifact"] == row["snapshot"] == str(snapshot)
    assert session.best["artifacts"] == [str(snapshot)]


@pytest.mark.macos_sandbox
def test_native_evaluator_child_reads_snapshot_but_cannot_read_session(session_factory):
    session = session_factory(baseline_runs=1)
    _install_child_evaluator(session, sandboxed=True)
    result = baseline(session)
    assert result["status"] == "baseline"
    assert result["metric"] == 10


def test_proposer_refuses_runtime_inside_snapshot_root(session_factory, monkeypatch, tmp_path):
    from subagent.config import Settings

    session = session_factory()
    settings = Settings(workspace=session.config.target_repo, session_root=tmp_path)
    monkeypatch.setattr("subagent.config.load", lambda project_root: settings)
    with (
        pytest.raises(ValueError, match="denied snapshot root"),
        SubagentProposer(session, session_root=session.snapshot_dir.parent / "runtime"),
    ):
        pass


@pytest.mark.macos_sandbox
def test_native_proposer_snapshot_deny_blocks_reads_and_writes(session_factory):
    import subprocess

    from subagent.sandbox import wrap_argv

    session = session_factory()
    # Protect snapshots of all sessions under the root, including siblings.
    snapshot = session.snapshot_dir.parent / "another-session" / "run"
    snapshot.mkdir(parents=True)
    value = snapshot / "value.json"
    value.write_text("10")
    source = (
        "import sys\nfrom pathlib import Path\n"
        "value = Path(sys.argv[1])\n"
        "for operation in (value.read_text, lambda: value.write_text('0')):\n"
        "    try:\n"
        "        operation()\n"
        "    except PermissionError:\n"
        "        continue\n"
        "    raise RuntimeError('snapshot access allowed')\n"
    )
    result = subprocess.run(
        wrap_argv([sys.executable, "-I", "-B", "-c", source, str(value)],
                  session.proposer_deny_read),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert value.read_text() == "10"
