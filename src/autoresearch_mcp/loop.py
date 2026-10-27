"""Propose/score orchestration with reusable providers and one current best."""

from __future__ import annotations

import math
import time
from contextlib import ExitStack, contextmanager
from uuid import uuid4

from . import loop_core, worktree
from .brief import build_brief
from .proposer import ClaudeCliProposer, ProposalResult, Proposer, SubagentProposer
from .store import Session


@contextmanager
def _using_proposer(session, proposer, *, provider=None):
    if proposer is not None:
        yield proposer  # Injected instances belong to the caller, including protocol-only fakes.
    else:
        provider = session.config.provider if provider is None else provider
        if provider.startswith("claude-cli:"):
            yield ClaudeCliProposer(
                model=provider.removeprefix("claude-cli:"),
                timeout_s=session.config.proposer_timeout_s,
            )
            return
        # Startup belongs to propose(), inside iterate's failure/fallback handling.
        owned = SubagentProposer(session, provider=provider)
        try:
            yield owned
        finally:
            owned.close()


def iterate(session: Session, *, proposer: Proposer | None = None, cancelled=None) -> dict:
    with ExitStack() as stack:
        return _iterate(session, proposer, cancelled, stack, {})


def _iterate(session, proposer, cancelled, stack, cache):
    with session.queue.lock:
        if not session.best:
            raise ValueError("run baseline before iterating")
        iteration = session.next_iteration()
        while (session.workspace_dir / f"it-{iteration}").exists():
            path = session.workspace_dir / f"it-{iteration}"
            loop_core._record(
                session, iteration, "interrupted iteration", "candidate",
                {"status": "abandoned"}, session.best,
            )
            session.tried(iteration, "interrupted iteration", "abandoned")
            worktree.remove(session, path)
            iteration = session.next_iteration()
        if iteration > session.config.max_iter:
            raise ValueError("max_iter reached")
        free = session.queue.wait_for_memory(
            session.config.memory_free_min_pct, cancelled=cancelled
        )
        path = worktree.create(session, iteration)
        previous = session.log_context
        session.log_context = {
            "proposal_memory_free_pct": free,
            "fallback_used": False,
            "proposer_attempts": [],
        }
        try:
            brief = build_brief(session, path)
            providers = [session.config.provider]
            if session.config.fallback_provider:
                providers.append(session.config.fallback_provider)
            for attempt, provider in enumerate(providers):
                call_id = uuid4().hex
                try:
                    if attempt == 0 and proposer is not None:
                        active = proposer
                    else:
                        key = (attempt, provider)
                        if key not in cache:
                            cache[key] = stack.enter_context(
                                _using_proposer(session, None, provider=provider)
                            )
                        active = cache[key]
                    proposal = active.propose(session, path, brief)
                    error = None
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    proposal = ProposalResult("proposer failed", "failed", raw=error)
                failed = proposal.run_state in {"failed", "cancelled", "timeout"}
                session.log_context.update(
                    proposer=provider, fallback_used=bool(attempt), proposer_call_id=call_id
                )
                session.log_context["proposer_attempts"].append(
                    {
                        "provider": provider,
                        "proposer_call_id": call_id,
                        "run_state": proposal.run_state,
                        "idea": proposal.idea,
                        "error": (error or proposal.raw) if failed else None,
                        "denials": proposal.denials,
                        "agent_id": proposal.agent_id,
                        "trace_path": proposal.trace_path,
                    }
                )
                if not failed or attempt == len(providers) - 1:
                    break
                # Discard even untracked partial edits before retrying the same iteration.
                worktree.remove(session, path)
                path = worktree.create(session, iteration)
            session.log_context.update(
                proposer_state=proposal.run_state,
                denials=proposal.denials,
                agent_id=proposal.agent_id,
                trace_path=proposal.trace_path,
            )
            if proposal.run_state not in {"completed", "completed_unverified"}:
                status = (
                    "proposer_error"
                    if proposal.run_state in {"failed", "cancelled", "timeout"}
                    else "proposer_incomplete"
                )
                row = loop_core._record(
                    session,
                    iteration,
                    proposal.idea,
                    "candidate",
                    {"status": status, "error": error or proposal.raw},
                    session.best,
                )
                session.tried(iteration, proposal.idea, f"{status} (inconclusive)")
                return row
            return loop_core.score_candidate(session, path, proposal.idea)
        finally:
            session.log_context = previous
            if path.exists():
                worktree.remove(session, path)


def validate_limits(session, max_iter, time_cap_s, plateau_limit):
    if not session.best:
        raise ValueError("run baseline before running the loop")
    count = session.config.max_iter if max_iter is None else max_iter
    plateau = session.config.plateau_limit if plateau_limit is None else plateau_limit
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("max_iter must be a nonnegative integer")
    if isinstance(plateau, bool) or not isinstance(plateau, int) or plateau < 1:
        raise ValueError("plateau_limit must be a positive integer")
    if time_cap_s is not None and (not math.isfinite(time_cap_s) or time_cap_s <= 0):
        raise ValueError("time_cap_s must be positive and finite")
    return min(count, max(0, session.config.max_iter - session.next_iteration() + 1)), plateau


def outcomes(session) -> list[dict]:
    """The last row of each iteration is its decision; remeasurements aren't extra attempts."""
    by_iteration = {}
    for row in session.rows():
        if row["phase"] == "candidate":
            by_iteration[row["iteration"]] = row
    return list(by_iteration.values())


def run(
    session: Session,
    max_iter: int | None = None,
    time_cap_s: float | None = None,
    *,
    plateau_limit: int | None = None,
    proposer: Proposer | None = None,
    clock=time.monotonic,
) -> dict:
    count, plateau = validate_limits(session, max_iter, time_cap_s, plateau_limit)
    started = clock()
    streak = 0
    for row in reversed(outcomes(session)):
        if row["kept"]:
            break
        streak += 1

    def stop_reason():
        if (session.session_dir / "stop").exists():
            return "stop"
        if time_cap_s is not None and clock() - started >= time_cap_s:
            return "time_cap"
        if streak >= plateau:
            return "plateau"
        return None

    completed = keeps = 0
    reason = "max_iter"
    with ExitStack() as stack:
        cache = {}
        for _ in range(count):
            reason = stop_reason()
            if reason:
                break
            try:
                result = _iterate(session, proposer, stop_reason, stack, cache)
            except InterruptedError:
                reason = stop_reason() or "stop"
                break
            completed += 1
            keeps += bool(result["kept"])
            streak = 0 if result["kept"] else streak + 1
        else:
            reason = stop_reason() or "max_iter"
    return {"reason": reason, "iterations": completed, "keeps": keeps, "non_keeps": streak}


def finalize(session: Session, *, print_path: bool = True):
    from .report import write_report

    with session.queue.lock:
        heldout = loop_core.finalize(session)
        path = write_report(session, heldout)
    if print_path:
        print(path)
    return path
