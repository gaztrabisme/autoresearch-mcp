"""A final report reconstructed from the append-only audit history."""

from collections import Counter, defaultdict
from statistics import median

from .noise import mad
from .store import _atomic_write


def write_report(session, heldout):
    rows = session.rows()
    baseline = [row for row in rows if row["phase"] == "baseline"]
    end = next(
        (i for i in range(len(baseline) - 1, -1, -1) if baseline[i]["status"] == "baseline"), None
    )
    samples = baseline[end + 1 - session.config.baseline_runs : end + 1] if end is not None else []
    baseline_values = [row["metric"] for row in samples]
    groups = defaultdict(list)
    for row in rows:
        if row["phase"] == "candidate":
            groups[row["iteration"]].append(row)
    keeps, noises = [], []
    counts = Counter()
    calls = {}
    if baseline_values:
        noises.append(f"- Baseline: MAD {mad(baseline_values):g}")
    for iteration, measurements in groups.items():
        final = measurements[-1]
        if final.get("proposer_call_id"):
            attempts = final.get("proposer_attempts") or [final]
            for attempt in attempts:
                calls[attempt["proposer_call_id"]] = attempt.get("denials", 0)
        if final["kept"]:
            values = [row["metric"] for row in measurements if row["metric"] is not None]
            center = median(values)
            gain = (final["best_before"] - center) * (
                1 if session.config.direction == "min" else -1
            )
            idea = " ".join(final["idea"].split()).replace("|", "\\|")
            keeps.append(f"| {iteration} | {idea} | {center:g} | {gain:g} |")
            noises.append(f"- Iteration {iteration}: MAD {mad(values):g}")
        else:
            counts[final["status"]] += 1
    best = session.best
    score = f"{heldout['metric']:g}" if heldout["metric"] is not None else "unavailable"
    text = (
        f"# Experiment report: {session.name}\n\nGoal: {session.config.goal}\n\n"
        f"Metric: {session.config.metric} ({session.config.direction})\n\n"
        f"Baseline: {median(baseline_values):g}\n\n"
        f"Final best: {best['metric']:g}\n\nBest commit: `{best['commit']}`\n\n"
        "## Keeps\n\nIteration | Idea | Score | Gain\n--- | --- | --- | ---\n"
        + "\n".join(keeps)
        + "\n\n"
        "## Discards and other non-keeps by status\n\n"
        + ("\n".join(f"- {status}: {count}" for status, count in sorted(counts.items())) or "None")
        + "\n\nTimeouts, crashes and proposer failures are inconclusive.\n\n"
        f"Held-out score: {score}\n\nHeld-out status: {heldout['status']}\n\n"
        "## Noise history\n\n" + "\n".join(noises) + "\n\n"
        f"Proposer calls: {len(calls)}\n\nDenials: {sum(calls.values())}\n"
    )
    path = session.session_dir / "report.md"
    _atomic_write(path, text)
    return path
