"""Subagent dispatch and a deterministic scripted proposer for offline callers."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .store import Session


@dataclass(frozen=True)
class ProposalResult:
    idea: str
    run_state: str
    denials: int = 0
    raw: str = ""
    agent_id: str | None = None
    trace_path: str | None = None


class Proposer(Protocol):
    def propose(self, session: Session, worktree: Path, brief: str) -> ProposalResult: ...


def extract_idea(raw: str) -> str:
    lines = raw.strip().splitlines()
    match = re.fullmatch(r"IDEA:\s*(\S.*)", lines[-1].strip()) if lines else None
    return match[1].strip() if match else " ".join(raw.split())[:120] or "Unspecified idea"


def count_denials(trace_path: Path, agent_id: str) -> int:
    if not trace_path.exists():
        return 0
    count = 0
    with trace_path.open() as trace:
        for line in trace:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # A trace writer may have left a partial final line.
            if (
                isinstance(row, dict)
                and row.get("kind") == "verdict"
                and (row.get("agent_id") == agent_id and row.get("action") == "deny")
            ):
                count += 1
    return count


class SubagentProposer:
    def __init__(self, session: Session, *, server=None, session_root: Path | None = None):
        self.session = session
        self.server = server
        self.session_root = session_root
        self._started = False
        self._closed = False

    @property
    def trace_path(self) -> Path | None:
        return Path(self.server.trace_path) if self.server is not None else None

    def start(self):
        if self._closed:
            raise RuntimeError("proposer has already stopped")
        if not self._started:
            if self.server is None:
                # Imports are lazy: an MCP handshake must not start the subagent runtime.
                from subagent.config import load
                from subagent.core import InProcessServer

                settings = replace(load(self.session.config.target_repo), trace=None)
                base = self.session_root or os.environ.get("AUTORESEARCH_SUBAGENT_ROOT")
                base = Path(base) if base else settings.session_root / "autoresearch"
                root = (base.expanduser() / self.session.name / uuid4().hex).resolve()
                if root.is_relative_to(self.session.session_dir):
                    raise ValueError("subagent runtime cannot be inside the denied private session")
                self.server = InProcessServer(session_root=root, settings=settings)
            self._started = True
            try:
                self.server.start()
            except BaseException:
                self.close()
                raise
        return self

    def close(self):
        if self._started and not self._closed:
            self._closed = True
            self.server.stop()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    def propose(self, session: Session, worktree: Path, brief: str) -> ProposalResult:
        if session.session_dir != self.session.session_dir:
            raise ValueError("a proposer belongs to one session")
        self.start()
        run = self.server.delegate(
            provider=session.config.provider,
            task=brief,
            verification="true",
            workspace=worktree,
            deny_read=[str(session.session_dir)],
        )
        try:
            run.await_ready.wait()
            # A human gate also wakes await_ready. Such a run is closed, never scored.
            raw = run.final_response or run.result_text
            state = run.state
        finally:
            self.server.close_agent(run.agent_id)
        return ProposalResult(
            extract_idea(raw),
            state,
            count_denials(self.trace_path, run.agent_id),
            raw,
            run.agent_id,
            str(self.trace_path),
        )


class FakeProposer:
    def __init__(self, edits: Iterable[tuple[str, Callable[[Path], None]]]):
        self.edits = iter(edits)
        self.calls = 0
        self.briefs: list[str] = []

    def propose(self, session: Session, worktree: Path, brief: str) -> ProposalResult:
        self.calls += 1
        self.briefs.append(brief)
        idea, edit = next(self.edits)
        edit(worktree)
        return ProposalResult(idea, "completed", raw=f"IDEA: {idea}")
