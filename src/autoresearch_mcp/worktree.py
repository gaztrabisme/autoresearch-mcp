"""Detached iteration worktrees; only the server moves the session branch."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Session


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.rstrip("\n")


def start(session: Session) -> None:
    git(session.config.target_repo, "branch", session.branch, "HEAD")


def assert_best_head(session: Session) -> str:
    head = git(session.config.target_repo, "rev-parse", session.branch)
    if session.best and head != session.best["commit"]:
        raise RuntimeError("session branch and best.json disagree; reconcile before resuming")
    return head


def create(session: Session, iteration: int) -> Path:
    if iteration < 0:
        raise ValueError("iteration must be nonnegative")
    path = session.workspace_dir / f"it-{iteration}"
    git(
        session.config.target_repo,
        "worktree",
        "add",
        "--detach",
        str(path),
        assert_best_head(session),
    )
    return path


def remove(session: Session, path: Path) -> None:
    path = path.resolve()
    if path.parent != session.workspace_dir or not re.fullmatch(r"it-\d+", path.name):
        raise ValueError("refusing to remove a path outside this session's iteration worktrees")
    git(session.config.target_repo, "worktree", "remove", "--force", str(path))


def in_scope(path: str, patterns: list[str]) -> bool:
    def match(parts: list[str], glob: list[str]) -> bool:
        if not glob:
            return not parts
        if glob[0] == "**":
            return match(parts, glob[1:]) or bool(parts and match(parts[1:], glob))
        return bool(parts and fnmatchcase(parts[0], glob[0]) and match(parts[1:], glob[1:]))

    return any(match(path.split("/"), pattern.split("/")) for pattern in patterns)


@dataclass(frozen=True)
class Diff:
    changed_files: list[str]
    hunks: int
    tree: str


def snapshot(path: Path, parent: str) -> Diff:
    if git(path, "rev-parse", "HEAD") != parent:
        raise ValueError("proposers must edit files, not commit or move HEAD")
    # Ignoring a file must not let a proposer bypass the edit-scope contract.
    git(path, "add", "--all", "--force")
    files = git(path, "diff", "--cached", "--no-renames", "--name-only", "-z", parent)
    changed = [file for file in files.split("\0") if file]
    patch = git(
        path,
        "diff",
        "--cached",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--unified=0",
        parent,
    )
    # Mode-only/binary changes count as a region as well.
    regions = sum(
        max(1, len(re.findall(r"^@@ ", part, re.MULTILINE)))
        for part in patch.split("diff --git ")[1:]
    )
    return Diff(changed, regions, git(path, "write-tree"))


def keep(session: Session, path: Path, idea: str, parent: str, tree: str) -> str:
    if snapshot(path, parent).tree != tree:
        raise RuntimeError("candidate changed during scoring")
    git(path, "commit", "--no-gpg-sign", "-m", idea)
    commit = git(path, "rev-parse", "HEAD")
    git(session.config.target_repo, "update-ref", f"refs/heads/{session.branch}", commit, parent)
    return commit
