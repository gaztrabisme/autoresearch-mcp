"""Single-current-best decisions, using a frozen gate and fresh-seed confirmation."""

from __future__ import annotations

from statistics import median
from subprocess import CalledProcessError

from . import worktree as wt
from .noise import improves, keep_threshold, mad
from .runner import run
from .store import Session


def _record(session, iteration, idea, phase, result, before, diff=None, flags=()):
    row = {
        "iteration": iteration,
        "idea": idea,
        "phase": phase,
        "status": "crash",
        "metric": None,
        "seed": None,
        "wall_s": 0.0,
        "extra_metrics": {},
        "memory_free_pct": None,
        "kept": False,
        "threshold": before["threshold"] if before else session.config.min_gain,
        "noise_mad": before["noise_mad"] if before else 0.0,
        "best_before": before["metric"] if before else None,
        "changed_files": diff.changed_files if diff else [],
        "hunks": diff.hunks if diff else 0,
        "flags": list(flags),
        "proposer_state": None,
        "denials": 0,
        "proposer": None,
        "fallback_used": False,
        "proposer_attempts": [],
        **session.log_context,
        **result,
    }
    session.append(row)
    return row


def _best(session, records, commit, iteration):
    values = [record["metric"] for record in records]
    best = {
        "metric": median(values),
        "commit": commit,
        "iteration": iteration,
        "noise_mad": mad(values),
        "threshold": keep_threshold(values, session.config.noise_k, session.config.min_gain),
        "wall_times": [record["wall_s"] for record in records],
        "seeds": [record["seed"] for record in records],
        "metrics": values,
        "artifacts": [record["artifact"] for record in records],
    }
    session.write_best(best)
    return best


def _tried(session, iteration, idea, status):
    outcome = f"{status} (inconclusive)" if status in {"timeout", "crash"} else status
    session.tried(iteration, idea, outcome)


def baseline(session: Session) -> dict:
    """Measure the committed target, without using dirty files in its checkout."""
    with session.queue.lock:
        if session.best:
            raise ValueError("baseline already exists")
        path = wt.create(session, 0)
        parent = wt.assert_best_head(session)
        records = []
        try:
            original = wt.snapshot(path, parent, session)
            for index in range(session.config.baseline_runs):
                result = run(session, path, session.next_seed())
                if result["status"] == "ok":
                    try:
                        if wt.snapshot(path, parent, session).tree != original.tree:
                            result.update(
                                status="crash", error="baseline code changed during training"
                            )
                    except (ValueError, CalledProcessError) as exc:
                        result.update(status="crash", error=str(exc))
                records.append(result)
                if result["status"] == "ok" and index == session.config.baseline_runs - 1:
                    _best(session, records, parent, 0)
                    result = {**result, "status": "baseline", "kept": True}
                _record(session, 0, "unchanged baseline", "baseline", result, None)
                if result["status"] not in {"ok", "baseline"}:
                    _tried(session, 0, "unchanged baseline", result["status"])
                    return result
            _tried(session, 0, "unchanged baseline", "baseline")
            return {
                **records[-1],
                "status": "baseline",
                "kept": True,
                "metric": session.best["metric"],
            }
        finally:
            wt.remove(session, path)


def score_candidate(session: Session, worktree, idea: str) -> dict:
    """Score and remove an iteration created with worktree.create(session, n)."""
    with session.queue.lock:
        before = session.best
        if not before:
            raise ValueError("run baseline before scoring a candidate")
        iteration = session.next_iteration()
        if iteration > session.config.max_iter:
            raise ValueError("max_iter reached")
        path = worktree.resolve()
        if path != session.workspace_dir / f"it-{iteration}":
            raise ValueError("candidate must be the next iteration worktree")
        parent = wt.assert_best_head(session)
        flags = []
        if any(
            " ".join(row["idea"].casefold().split()) == " ".join(idea.casefold().split())
            for row in session.rows()
            if row["phase"] == "candidate"
        ):
            flags.append("repeated_idea")
        try:
            try:
                diff = wt.snapshot(path, parent, session)
            except (ValueError, CalledProcessError) as exc:
                row = _record(
                    session,
                    iteration,
                    idea,
                    "candidate",
                    {"status": "crash", "error": str(exc)},
                    before,
                    flags=[*flags, "invalid_candidate"],
                )
                _tried(session, iteration, idea, row["status"])
                return row
            if diff.hunks > 1:
                flags.append("multi_change")
            if any(not wt.in_scope(file, session.config.scope) for file in diff.changed_files):
                row = _record(
                    session,
                    iteration,
                    idea,
                    "candidate",
                    {"status": "rejected_scope"},
                    before,
                    diff,
                    flags,
                )
                _tried(session, iteration, idea, row["status"])
                return row
            if not diff.changed_files:
                row = _record(
                    session,
                    iteration,
                    idea,
                    "candidate",
                    {"status": "no_change"},
                    before,
                    diff,
                    flags,
                )
                _tried(session, iteration, idea, row["status"])
                return row
            return _measure_candidate(session, path, idea, iteration, before, diff, flags, parent)
        finally:
            wt.remove(session, path)


def _measure_candidate(session, path, idea, iteration, before, diff, flags, parent):
    records = []
    for index in range(session.config.remeasure_runs + 1):
        result = run(session, path, session.next_seed())
        records.append(result)
        if result["status"] != "ok":
            row = _record(session, iteration, idea, "candidate", result, before, diff, flags)
            _tried(session, iteration, idea, row["status"])
            return row

        try:
            unchanged = wt.snapshot(path, parent, session).tree == diff.tree
        except wt.GitMetadataChanged:
            result.update(status="crash", error="git metadata changed")
            row = _record(session, iteration, idea, "candidate", result, before, diff, flags)
            _tried(session, iteration, idea, row["status"])
            return row
        except (ValueError, CalledProcessError):
            unchanged = False
        if not unchanged:
            result.update(status="crash", error="candidate code changed during training")
            row = _record(
                session,
                iteration,
                idea,
                "candidate",
                result,
                before,
                diff,
                [*flags, "candidate_changed"],
            )
            _tried(session, iteration, idea, row["status"])
            return row

        confirmed = records[1:] if index else records
        center = median(record["metric"] for record in confirmed)
        passes = improves(center, before["metric"], before["threshold"], session.config.direction)
        last = index == session.config.remeasure_runs
        if (index == 0 and not passes) or last:
            result = {**result, "status": "discarded", "kept": False}
            if passes:
                try:
                    commit = wt.keep(session, path, idea, parent, diff.tree)
                    _best(session, confirmed, commit, iteration)
                    result.update(status="kept", kept=True)
                except wt.GitMetadataChanged:
                    result.update(status="crash", error="git metadata changed")
                except (OSError, RuntimeError, ValueError, CalledProcessError) as exc:
                    result.update(status="crash", error=f"keep failed: {exc}")
            row = _record(session, iteration, idea, "candidate", result, before, diff, flags)
            _tried(session, iteration, idea, row["status"])
            return {**row, "metric": center}
        _record(session, iteration, idea, "candidate", result, before, diff, flags)
    raise AssertionError("unreachable candidate decision")


def finalize(session: Session) -> dict:
    """Train the selected best once more, then use holdout_cmd instead of eval_cmd."""
    with session.queue.lock:
        before = session.best
        if not before:
            raise ValueError("run baseline before finalizing")
        if not session.config.holdout_cmd:
            raise ValueError("holdout_cmd is required for finalize")
        iteration = session.next_iteration()
        path = wt.create(session, iteration)
        try:
            result = run(session, path, session.next_seed(), holdout=True)
            row = _record(
                session, iteration, "final held-out evaluation", "holdout", result, before
            )
            _tried(session, iteration, row["idea"], row["status"])
            return row
        finally:
            wt.remove(session, path)
