"""Detached iteration worktrees; only the server moves the session branch."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Session


class GitMetadataChanged(ValueError):
    def __init__(self):
        super().__init__("git metadata changed")


def _verify_metadata(repo: Path, target_repo: Path) -> None:
    common = (target_repo / ".git").resolve()
    expected = common / "worktrees" / repo.name
    try:
        pointer = repo / ".git"
        if not stat.S_ISREG(pointer.lstat().st_mode):
            raise GitMetadataChanged()
        fd = os.open(pointer, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise GitMetadataChanged()
            if stream.read().strip() != f"gitdir: {expected}":
                raise GitMetadataChanged()
        if git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir") != str(common):
            raise GitMetadataChanged()
    except (OSError, ValueError, subprocess.CalledProcessError):
        # Invalidate it now: cleanup must never run another command in this worktree.
        shutil.rmtree(repo)
        raise GitMetadataChanged() from None


def git(repo: Path, *args: str, target_repo: Path | None = None) -> str:
    if target_repo is not None:
        _verify_metadata(repo, target_repo)
    options = (
        "core.fsmonitor=", "core.hooksPath=/dev/null", "core.sshCommand=",
        "protocol.allow=never", "diff.external=", "core.pager=cat",
    )
    return subprocess.run(
        ["git", *(arg for option in options for arg in ("-c", option)), "-C", str(repo), *args],
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
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
    try:
        git(session.config.target_repo, "worktree", "remove", "--force", str(path))
    except subprocess.CalledProcessError:
        if path.exists():
            shutil.rmtree(path)


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


def snapshot(path: Path, parent: str, session: Session) -> Diff:
    def checked_git(*args):
        return git(path, *args, target_repo=session.config.target_repo)

    if checked_git("rev-parse", "HEAD") != parent:
        raise ValueError("proposers must edit files, not commit or move HEAD")
    # Ignoring a file must not let a proposer bypass the edit-scope contract.
    checked_git("add", "--all", "--force")
    files = checked_git("diff", "--cached", "--no-renames", "--name-only", "-z", parent)
    changed = [file for file in files.split("\0") if file]
    patch = checked_git(
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
    return Diff(changed, regions, checked_git("write-tree"))


def keep(session: Session, path: Path, idea: str, parent: str, tree: str) -> str:
    if snapshot(path, parent, session).tree != tree:
        raise RuntimeError("candidate changed during scoring")
    git(path, "commit", "--no-gpg-sign", "-m", idea, target_repo=session.config.target_repo)
    commit = git(path, "rev-parse", "HEAD", target_repo=session.config.target_repo)
    git(session.config.target_repo, "update-ref", f"refs/heads/{session.branch}", commit, parent)
    return commit
