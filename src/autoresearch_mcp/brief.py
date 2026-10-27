"""The proposer sees progress and editable files, never private evaluator paths."""

from pathlib import Path

from .store import Session
from .worktree import in_scope


def build_brief(session: Session, worktree: Path) -> str:
    best = session.best
    if not best:
        raise ValueError("run baseline before proposing")
    files = sorted(
        path.relative_to(worktree).as_posix()
        for path in worktree.rglob("*")
        if path.is_file()
        and path.name != ".git"
        and in_scope(path.relative_to(worktree).as_posix(), session.config.scope)
    )
    return (
        "Improve the measured target with ONE change.\n\n"
        f"Goal: {session.config.goal}\n"
        f"Metric: {session.config.metric}; direction: {session.config.direction}\n"
        f"Current best: {best['metric']} @ {best['commit']}\n"
        f"Keep threshold: strictly more than {best['threshold']} improvement.\n\n"
        "Session state (includes last 5 results):\n"
        f"{(session.session_dir / 'state.md').read_text()}\n"
        "Full tried list — do not repeat these:\n"
        f"{(session.session_dir / 'tried.md').read_text()}\n"
        f"Scope globs: {', '.join(session.config.scope)}\n"
        "Matching files:\n" + "\n".join(f"- {file}" for file in files) + "\n\n"
        "Rules:\n"
        "- edit only in-scope files; make ONE change.\n"
        "- read with cat/head/rg only.\n"
        "- no git, no python -c, no network, no package installs.\n"
        "- do not try to run training (the server does).\n"
        "- Never call autoresearch MCP tools or inspect evaluator/validation/holdout data.\n"
        "- End your response with IDEA: <one line>\n"
    )
