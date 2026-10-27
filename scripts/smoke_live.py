#!/usr/bin/env -S uv run
"""Two real provider proposals against a deterministic, privately scored toy."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from autoresearch_mcp import loop
from autoresearch_mcp.loop_core import baseline
from autoresearch_mcp.proposer import SubagentProposer
from autoresearch_mcp.store import Config, Session
from autoresearch_mcp.worktree import git


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="codex")
    args = parser.parse_args()
    scratch = Path(os.environ.get("AUTORESEARCH_SMOKE_ROOT", Path.cwd() / ".cache" / "smoke"))
    scratch.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="live-", dir=scratch)).resolve()
    repo = root / "target"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Autoresearch smoke")
    git(repo, "config", "user.email", "smoke@example.invalid")
    (repo / "solution.py").write_text("def solve():\n    return [0, 0, 0, 0, 0]\n")
    (repo / "train.py").write_text(
        "import json, os\nfrom pathlib import Path\nfrom solution import solve\n"
        "Path(os.environ['AUTORESEARCH_OUT'], 'answer.json').write_text(json.dumps(solve()))\n"
        "print('METRIC score=0')  # Deliberately ignored by the server.\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "--no-gpg-sign", "-qm", "deterministic toy baseline")
    session = Session.create(
        "toy",
        Config(
            target_repo=repo,
            scope=["solution.py"],
            provider=args.provider,
            train_cmd=[sys.executable, "train.py"],
            eval_cmd=[sys.executable, "eval.py"],
            holdout_cmd=[sys.executable, "holdout.py"],
            baseline_runs=3,
            min_gain=0.1,
            initial_timeout_s=60,
            timeout_factor=10,
            max_iter=2,
            goal="Return five perfect squares in ascending order starting from zero in solve().",
        ),
        home=root / "private",
        wt_root=root / "public",
    )
    # Only the evaluator can open these reference files; neither child sees the session directory.
    (session.session_dir / "validation.json").write_text(json.dumps([0, 1, 4, 9, 16]))
    (session.session_dir / "heldout.json").write_text(json.dumps([0, 1, 4, 9, 16]))
    evaluator = (
        "import json, os\nfrom pathlib import Path\n"
        "artifact = Path(os.environ['AUTORESEARCH_ARTIFACT'], 'answer.json')\n"
        "actual = json.loads(artifact.read_text())\n"
        "expected = json.loads(Path(SPLIT).read_text())\n"
        "score = sum(abs(a-b) for a,b in zip(actual, expected))\n"
        "score += 100*abs(len(actual)-len(expected))\n"
        "print(f'METRIC score={score}')\n"
    )
    (session.session_dir / "eval.py").write_text(evaluator.replace("SPLIT", "'validation.json'"))
    (session.session_dir / "holdout.py").write_text(evaluator.replace("SPLIT", "'heldout.json'"))
    print(f"Retained smoke directory: {root}", flush=True)
    result = baseline(session)
    if result["status"] != "baseline":
        raise RuntimeError(f"baseline failed: {result}")
    with SubagentProposer(session, session_root=root / "subagent") as proposer:
        for _ in range(2):
            before = len(session.rows())
            loop.iterate(session, proposer=proposer)
            for row in session.rows()[before:]:
                print(
                    json.dumps({key: row[key] for key in ("status", "metric", "idea", "denials")}),
                    flush=True,
                )
        print(f"Trace: {proposer.trace_path}", flush=True)


if __name__ == "__main__":
    main()
