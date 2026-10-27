# autoresearch-mcp

B2 implements the experiment-loop library for Python 3.11+. MCP tools, proposer
dispatch and the CLI follow in B3. The only runtime dependency is the editable
`subagent` checkout configured in `pyproject.toml`.

```sh
uv sync
uv run pytest -q
uv run ruff check
```

Training requires macOS and a working `sandbox-exec`. There is no production
fallback to unrestricted training. The `macos_sandbox` tests probe the sandbox
and skip when it cannot run; other tests replace only the wrapper and use real
Git repositories and Python subprocesses. Test scratch files stay in `.cache/`.
The pytest source paths permit offline testing against the specified sibling
checkout when package installation is unavailable.

```python
from pathlib import Path

from autoresearch_mcp.loop_core import baseline, finalize, score_candidate
from autoresearch_mcp.store import Config, Session
from autoresearch_mcp.worktree import create

session = Session.create("example", Config(
    target_repo=Path("/path/to/target"),
    scope=["train.py", "src/**/*.py"],
    train_cmd=["python3", "train.py"],
    eval_cmd=["python3", "eval.py"],
    holdout_cmd=["python3", "holdout.py"],
    metric="score", direction="min", steps=1000, min_gain=0.01,
    initial_timeout_s=120, goal="Reduce validation error",
))
# Install eval.py, holdout.py and validation/holdout data into session.session_dir.
# Put only the training split into session.workspace_dir / "data".
baseline(session)
candidate = create(session, session.next_iteration())
# A proposer edits candidate within the configured scope.
result = score_candidate(session, candidate, "one-line hypothesis")
# score_candidate removes the worktree; artifacts and outcome records remain.
final = finalize(session)
```

The target needs an existing Git commit and a configured Git author identity.
Session branches start at its committed `HEAD`; dirty files in the owner's
checkout are excluded. Iteration worktrees are detached. A successful keep makes
a commit with the idea as its message and advances `autoresearch/<name>` using a
compare-and-swap ref update. The owner's checkout stays on its existing branch.
Scope globs are rooted at the worktree, `*` matches within one path component,
and `**` matches zero or more components. Ignored and untracked edits are checked.
Proposers edit files; precommitted candidates are rejected as invalid.

Session records default to `~/.autoresearch/<name>/`; set `AUTORESEARCH_HOME` or
pass `home=` to `Session.create/load`. Public data, artifacts and worktrees
default to `~/.cache/autoresearch-wt/<name>/`; set `AUTORESEARCH_WT_ROOT` or pass
`wt_root=`. These roots must be separate. Workspaces under `~/.autoresearch`
are refused even with a custom private root. Use the same roots when reloading.
`Config` is persisted in `config.toml` before measurement. `goal` is an additional
optional field for the regenerated state summary. Set `provider` for B3 dispatch.

The trainer runs in its iteration worktree with `AUTORESEARCH_STEPS`,
`AUTORESEARCH_SEED`, `AUTORESEARCH_DATA` and `AUTORESEARCH_OUT`. It must honor
the step budget and write artifacts to OUT. Its stdout is retained under the
private `runs/<run-id>/` directory and never scored. The evaluator runs in the
private session directory with `AUTORESEARCH_ARTIFACT` and `AUTORESEARCH_SEED`.
Only its stdout is parsed: `METRIC score=1.23`. Additional finite metrics such
as `METRIC tokens_per_s=45000` are recorded. Stderr cannot supply a metric.
Nonzero exits, missing/invalid primary metrics, and duplicate metrics crash.

The initial baseline uses distinct seeds and its median becomes the best.
The selection threshold is `max(noise_k * MAD, min_gain)` in metric units;
improvement must be strictly greater. Set a meaningful `min_gain`, including for
deterministic targets where MAD is zero (the default is zero). A promising first
run gets `remeasure_runs` additional fresh-seed runs. Only their combined median
can pass the candidate's frozen threshold. A keep recomputes MAD and timeout
from those successful runs. Hunk counts flag `multi_change`; normalized exact
idea repeats flag `repeated_idea`. Both are recorded for proposer context.

Timeout covers training and evaluation together and uses monotonic elapsed
seconds. Its budget is `initial_timeout_s` until the baseline completes, then
`timeout_factor * median(current_best_wall_times)`. Each phase runs in a fresh
process group; the group is killed on timeout and remaining descendants are
terminated on phase completion. Timeout and crash outcomes preserve the best
and are recorded as inconclusive in `tried.md`.

All core operations share an in-process lock across sessions. Before every
train/eval pair, `memory_pressure` must report enough free memory; the gate waits
and rechecks. `RunQueue(memory_reader=..., clock=..., poll_interval_s=...)` makes
this injectable. Memory-reader failures raise instead of inventing a value.
Calls to the low-level `runner.run` return a record and retain raw process logs;
`loop_core` appends the JSONL audit rows and regenerates state. Baseline and
candidate functions log each actual run once. Scope rejections and no-ops also
get an audit row, with null seed/memory and zero wall time. The last candidate
row carries its decision; earlier remeasurement rows remain `ok`. Returned
candidate `metric` is the combined median, while JSONL `metric` is that row's
individual measurement. `finalize` trains the selected commit and evaluates it
with `holdout_cmd`, without using that result for selection or changing best.

Serialization is in-process only. `best.json` and regenerated state use atomic
file replacement; Git ref and JSON updates are separate operations. A branch
that disagrees with `best.json` fails visibly before further work; automatic
interruption recovery and multi-server coordination are outside B2. Calling
finalize is the caller's decision to stop tuning; this library does not enforce
a session lifecycle beyond the candidate iteration limit.

See [the acceptance report](wiki/B2-report.md) for source usage and validation.
