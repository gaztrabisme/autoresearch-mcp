"""Train behind the session deny rule, then score in a separate trusted process."""

from __future__ import annotations

import math
import os
import re
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import subagent.sandbox

if TYPE_CHECKING:
    from .store import Session


def parse_metrics(output: str, primary: str) -> tuple[float, dict[str, float]]:
    metrics = {}
    for line in output.splitlines():
        match = re.fullmatch(r"METRIC ([A-Za-z_][\w./-]*)=(\S+)", line.strip())
        if match:
            name, raw = match.groups()
            value = float(raw)
            if not math.isfinite(value) or name in metrics:
                raise ValueError("nonfinite or duplicate evaluator metric")
            metrics[name] = value
    if primary not in metrics:
        raise ValueError("evaluator did not emit the configured metric")
    score = metrics.pop(primary)
    return score, metrics


def _process(argv: list[str], cwd: Path, env: dict, deadline: float, output: Path) -> int:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(argv, 0)
    with output.open("wb") as stream, output.with_suffix(".stderr").open("wb") as errors:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=errors,
        )
        try:
            return process.wait(timeout=remaining)
        finally:
            # Terminate descendants left behind by a successfully exited leader, too.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def run(session: Session, worktree: Path, seed: int, *, holdout: bool = False) -> dict:
    """Run one gated train+eval pair; wall_s excludes waiting for memory/the lock."""
    command = session.config.holdout_cmd if holdout else session.config.eval_cmd
    if not command:
        raise ValueError("holdout_cmd is required for finalize")

    def execute():
        started = time.monotonic()
        timeout = session.timeout_s
        run_id = f"{seed}-{uuid4().hex}"
        artifact = session.workspace_dir / "artifacts" / run_id
        logs = session.session_dir / "runs" / run_id
        artifact.mkdir()
        logs.mkdir(parents=True)
        record = dict(
            status="crash",
            metric=None,
            extra_metrics={},
            seed=seed,
            run_id=run_id,
            artifact=str(artifact),
            timeout_s=timeout,
        )
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "AUTORESEARCH_SEED": str(seed)}
        train_env = {
            **env,
            "AUTORESEARCH_STEPS": str(session.config.steps),
            "AUTORESEARCH_DATA": str(session.workspace_dir / "data"),
            "AUTORESEARCH_OUT": str(artifact),
        }
        try:
            argv = subagent.sandbox.wrap_argv(
                list(session.config.train_cmd),
                [str(session.session_dir)],
            )
            train_rc = _process(argv, worktree, train_env, started + timeout, logs / "train.stdout")
            if train_rc:
                record["error"] = f"train exited with code {train_rc}"
            else:
                eval_rc = _process(
                    list(command),
                    session.session_dir,
                    {**env, "AUTORESEARCH_ARTIFACT": str(artifact)},
                    started + timeout,
                    logs / "eval.stdout",
                )
                if eval_rc:
                    record["error"] = f"eval exited with code {eval_rc}"
                else:
                    score, extra = parse_metrics(
                        (logs / "eval.stdout").read_text(errors="replace"),
                        session.config.metric,
                    )
                    record.update(status="ok", metric=score, extra_metrics=extra)
        except subprocess.TimeoutExpired:
            record["status"] = "timeout"
        except (OSError, RuntimeError, ValueError) as exc:
            record["error"] = str(exc)
        record["wall_s"] = time.monotonic() - started
        return record

    return session.queue.run(execute, session.config.memory_free_min_pct)
