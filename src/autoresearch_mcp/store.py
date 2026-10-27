"""Private session records and separately located, sandbox-readable workspaces."""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import tomllib
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from uuid import uuid4

from .queue import RunQueue


@dataclass(frozen=True)
class Config:
    target_repo: Path
    scope: list[str]
    train_cmd: list[str] | str
    eval_cmd: list[str] | str
    metric: str = "score"
    direction: str = "min"
    steps: int = 100
    noise_k: float = 2.0
    min_gain: float = 0.0
    baseline_runs: int = 5
    remeasure_runs: int = 3
    timeout_factor: float = 2.0
    initial_timeout_s: float = 60.0
    max_iter: int = 100
    memory_free_min_pct: float = 20.0
    provider: str = ""
    holdout_cmd: list[str] | str = field(default_factory=list)
    goal: str = ""
    plateau_limit: int = 10
    fallback_provider: str = ""
    proposer_timeout_s: float = 600.0

    def __post_init__(self):
        object.__setattr__(self, "target_repo", Path(self.target_repo).expanduser().resolve())
        if self.direction not in {"min", "max"}:
            raise ValueError("direction must be min or max")
        if not re.fullmatch(r"[A-Za-z_][\w./-]*", self.metric):
            raise ValueError("invalid metric name")
        for name in ("steps", "baseline_runs", "remeasure_runs", "max_iter", "plateau_limit"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in (
            "noise_k",
            "min_gain",
            "timeout_factor",
            "initial_timeout_s",
            "proposer_timeout_s",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
                or ("timeout" in name and value == 0)
            ):
                raise ValueError(f"invalid {name}")
        for name in ("provider", "fallback_provider"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or "\0" in value
                or (
                    value.startswith("claude-cli:")
                    and not value.removeprefix("claude-cli:").strip()
                )
            ):
                raise ValueError(f"invalid {name}")
        if not 0 <= self.memory_free_min_pct <= 100:
            raise ValueError("memory_free_min_pct must be in [0, 100]")
        for pattern in self.scope:
            if not pattern or Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                raise ValueError("scope globs must be relative and cannot contain ..")
        for name in ("train_cmd", "eval_cmd", "holdout_cmd"):
            value = getattr(self, name)
            argv = shlex.split(value) if isinstance(value, str) else list(value)
            if (name != "holdout_cmd" and not argv) or any(
                not isinstance(arg, str) or "\0" in arg for arg in argv
            ):
                raise ValueError(f"invalid {name}")
            object.__setattr__(self, name, argv)


def _paths(name: str, home=None, wt_root=None, snap_root=None) -> tuple[Path, Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ValueError("session names may contain letters, digits, _ and -")
    private = Path(home or os.environ.get("AUTORESEARCH_HOME", "~/.autoresearch"))
    public = Path(wt_root or os.environ.get("AUTORESEARCH_WT_ROOT", "~/.cache/autoresearch-wt"))
    snapshots = Path(
        snap_root or os.environ.get("AUTORESEARCH_SNAP_ROOT", "~/.cache/autoresearch-snap")
    )
    private, public, snapshots = (
        path.expanduser().resolve() for path in (private, public, snapshots)
    )
    if private.is_relative_to(public) or public.is_relative_to(private):
        raise ValueError("session and workspace roots must be separate")
    if public.is_relative_to((Path.home() / ".autoresearch").resolve()):
        raise ValueError("workspaces cannot be under ~/.autoresearch")
    for root in (private, public):
        if snapshots.is_relative_to(root) or root.is_relative_to(snapshots):
            raise ValueError("snapshot, session and workspace roots must be separate")
    return private / name, public / name, snapshots / name


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class Session:
    name: str
    config: Config
    session_dir: Path
    workspace_dir: Path
    snapshot_dir: Path
    queue: RunQueue = field(default_factory=RunQueue, repr=False)
    log_context: dict = field(default_factory=dict, repr=False)

    @classmethod
    def create(
        cls,
        name: str,
        config: Config,
        *,
        goal: str | None = None,
        home=None,
        wt_root=None,
        snap_root=None,
        queue: RunQueue | None = None,
    ) -> Session:
        from .worktree import start

        private, public, snapshots = _paths(name, home, wt_root, snap_root)
        if config.target_repo.is_relative_to(private):
            raise ValueError("target repository cannot be inside the session")
        if goal is not None:
            config = replace(config, goal=goal)
        private.mkdir(parents=True, mode=0o700)
        public.mkdir(parents=True)
        snapshots.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        snapshots.mkdir(mode=0o700, exist_ok=True)
        session = cls(name, config, private, public, snapshots, queue or RunQueue())
        data = asdict(config)
        data["target_repo"] = str(config.target_repo)
        # These fields use TOML's JSON-compatible scalar and array subset.
        (private / "config.toml").write_text(
            "".join(
                f"{key} = {json.dumps(value, ensure_ascii=False)}\n" for key, value in data.items()
            )
        )
        (private / "log.jsonl").touch()
        (private / "tried.md").write_text(
            "# Tried ideas\n\nIteration | Idea | Outcome\n--- | --- | ---\n"
        )
        (public / "data").mkdir()
        (public / "artifacts").mkdir()
        start(session)
        session.regenerate_state()
        return session

    @classmethod
    def load(cls, name: str, *, home=None, wt_root=None, snap_root=None, queue=None) -> Session:
        private, public, snapshots = _paths(name, home, wt_root, snap_root)
        config = Config(**tomllib.loads((private / "config.toml").read_text()))
        return cls(name, config, private, public, snapshots, queue or RunQueue())

    @property
    def proposer_deny_read(self) -> list[str]:
        return [str(self.session_dir), str(self.snapshot_dir.parent)]

    @property
    def branch(self) -> str:
        return f"autoresearch/{self.name}"

    @property
    def best(self) -> dict | None:
        path = self.session_dir / "best.json"
        return json.loads(path.read_text()) if path.exists() else None

    @property
    def timeout_s(self) -> float:
        best = self.best
        if not best:
            return self.config.initial_timeout_s
        return self.config.timeout_factor * median(best["wall_times"])

    def rows(self) -> list[dict]:
        content = (self.session_dir / "log.jsonl").read_text()
        lines = content.splitlines()
        # Status can read concurrently with an append; only newline-terminated rows are complete.
        if content and not content.endswith("\n"):
            lines = lines[:-1]
        return [json.loads(line) for line in lines]

    def next_seed(self) -> int:
        return max((row.get("seed") or 0 for row in self.rows()), default=0) + 1

    def next_iteration(self) -> int:
        return max((row["iteration"] for row in self.rows()), default=0) + 1

    def write_best(self, best: dict) -> None:
        _atomic_write(
            self.session_dir / "best.json", json.dumps(best, allow_nan=False, indent=2) + "\n"
        )

    def append(self, row: dict) -> None:
        row = {"ts": datetime.now(UTC).isoformat(), **row}
        encoded = (json.dumps(row, allow_nan=False) + "\n").encode("utf-8")
        with self.queue.lock:
            with (self.session_dir / "log.jsonl").open("r+b") as output:
                content = output.read()
                if content and not content.endswith(b"\n"):
                    boundary = content.rfind(b"\n") + 1
                    with (self.session_dir / "log.torn").open("ab") as torn:
                        torn.write(content[boundary:])
                        torn.flush()
                        os.fsync(torn.fileno())
                    output.seek(boundary)
                    output.truncate()
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            self.regenerate_state()

    def tried(self, iteration: int, idea: str, outcome: str) -> None:
        idea = " ".join(idea.split()).replace("|", "\\|")
        with (self.session_dir / "tried.md").open("a", encoding="utf-8") as output:
            output.write(f"{iteration} | {idea} | {outcome}\n")

    def regenerate_state(self) -> None:
        best = self.best
        current = f"{best['metric']} @ `{best['commit']}`" if best else "Awaiting baseline"
        noise = f"MAD {best['noise_mad']}; threshold {best['threshold']}" if best else "Unmeasured"
        recent = "\n".join(
            f"- {row['iteration']}: {row['status']} metric={row['metric']} — "
            f"{' '.join(row['idea'].split())}"
            for row in self.rows()[-5:]
        )
        _atomic_write(
            self.session_dir / "state.md",
            (
                f"# {self.name}\n\nGoal: {self.config.goal}\n\n"
                f"Metric: {self.config.metric} ({self.config.direction})\n\n"
                f"Current best: {current}\n\nNoise floor: {noise}\n\n"
                f"## Last 5 results\n\n{recent}\n\n[Already tried](tried.md)\n"
            ),
        )
