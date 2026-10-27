# autoresearch-mcp

An autonomous experiment loop for any measurable target: ML training, prompt
tuning or code performance. Python 3.11+, one current best, queued execution.
B2 supplies scoring and Git bookkeeping; B3 adds guarded subagent proposals,
seven MCP tools, foreground CLI commands and a final experiment report.

The editable `subagent` checkout configured in `pyproject.toml` supplies the
provider runtime and macOS sandbox wrapper. The server uses `MCPServer` from its
existing MCP 2 SDK dependency; no separate `fastmcp` package is needed.

```sh
uv sync
.venv/bin/python -m pytest -q
.venv/bin/ruff check
```

Keep the sibling `../subagent-mcp-wt/autoresearch-aware` checkout in place. To use
the installed console commands in this shell:

```sh
export PATH="$PWD/.venv/bin:$PATH"
```

Configure the chosen provider in subagent before running proposals. Provider
names, such as `codex`, refer to that configuration; autoresearch does not choose
or replace its model. A Codex provider needs its CLI installed and signed in.

Register the stdio server with Claude Code:

```sh
claude mcp add -s user autoresearch -- autoresearch-mcp
```

The process launching Claude Code must be able to find that executable. An
absolute `.venv/bin/autoresearch-mcp` path can also be used. For Codex, add an
entry to its `config.toml`, replacing the example executable path:

```toml
[mcp_servers.autoresearch]
command = "/absolute/path/to/autoresearch-mcp/.venv/bin/autoresearch-mcp"
tool_timeout_sec = 3600

[mcp_servers.autoresearch.env]
AUTORESEARCH_HOME = "/absolute/path/to/private-sessions"
AUTORESEARCH_WT_ROOT = "/absolute/path/to/public-workspaces"
```

The seven tools are `init`, `baseline`, `iterate`, `run`, `status`, `stop` and
`finalize`. `init` exposes every Config field. `baseline` and `iterate` wait for
completion; `run` returns immediately and admits only one background run in
the server process. `status` returns state.md, the last ten audit rows, activity,
the most recent result/error and whether stop was requested. Status and stop
remain available during a run. Other experiment mutations refuse while busy.
Children never call this server.

Create a pre-registration TOML file for CLI initialization. Commands can be
argument arrays or shell-quoted strings (they are split into argv, never passed
through a shell). The target must already be a Git repository with a commit and
author identity:

```toml
target_repo = "/absolute/path/to/target"
scope = ["solution.py", "src/**/*.py"]
goal = "Reduce validation error with one change at a time"
metric = "score"
direction = "min"
steps = 1000
noise_k = 2.0
min_gain = 0.01
baseline_runs = 5
remeasure_runs = 3
timeout_factor = 2.0
initial_timeout_s = 120.0
max_iter = 100
plateau_limit = 10
memory_free_min_pct = 20.0
provider = "codex"
train_cmd = ["python3", "train.py"]
eval_cmd = ["python3", "eval.py"]
holdout_cmd = ["python3", "holdout.py"]
```

```sh
autoresearch init demo --config /path/to/session.toml
# Install eval.py, holdout.py and validation/held-out data in the printed session_dir.
# Put the training split in the printed workspace_dir/data directory.
autoresearch baseline demo
autoresearch iterate demo
caffeinate -i -s autoresearch run demo --max-iter 20 --time-cap-s 3600
autoresearch status demo
autoresearch stop demo
autoresearch finalize demo
```

CLI `run` blocks in the foreground. A run stops at the requested iteration count,
the pre-registered total max_iter, time cap, persisted `stop` file, or plateau
limit (ten consecutive non-keeps by default). `--plateau-limit` overrides the
configured value; keeps reset the streak and previous iteration history counts
when resuming. Stop/time caps are cooperative: an admitted iteration finishes,
and memory waiting before a new iteration is interruptible. They are separate
from B2's hard train/eval timeout. Remove the stop file before resuming a stopped
loop. Single `iterate` is an explicit one-iteration operation, independent of
the run stop flag and plateau rule.

`finalize` retrains the best commit in a fresh worktree, runs only `holdout_cmd`
and writes `report.md` under the private session. CLI finalization prints its
path; MCP finalization returns the path and contents without printing on stdio.
The report contains baseline/final metrics, keep ideas and gains, non-keep counts
by status, held-out result, noise history and deduplicated proposer-call/denial
totals. Held-out scores never enter keep decisions.

Each full `run` owns one started/stopped `InProcessServer`; each proposal gets
one agent, which is closed after its run. Standalone `iterate` owns one runtime.
Library callers can share an explicit `with SubagentProposer(session)` across
several iterations; injected proposer instances remain caller-owned. Runtime
files live under subagent's configured `session_root/autoresearch/<name>/<id>`,
outside the denied private session so provider bootstrap files remain readable.
Set `AUTORESEARCH_SUBAGENT_ROOT` or pass `session_root=` to SubagentProposer to
change the base. Its `trace_path` identifies trace.jsonl; rows with kind=verdict,
matching agent_id and action=deny supply the denial count. Trace writing is
enabled for these runtimes even when the general subagent config disables it.

The brief includes the full tried list, scope matches, best/threshold and recent
state. It asks for one in-scope edit, reads through cat/head/rg only, no git,
python -c, network, package installs or training, and a final `IDEA: <one line>`.
Subagent verification is `true`; the server checks the diff and edit scope.
Both completed and completed_unverified proposals proceed to that check. An
empty diff is logged as `no_change` without training. Failed/cancelled/timeout
proposals are `proposer_error`; unfinished human gates are `proposer_incomplete`
and are never scored. All remeasurement rows retain proposer metadata; totals
count each proposer_call_id once.

Run the live smoke only in an environment that permits real sandbox-exec,
approval sockets and provider login:

```sh
scripts/smoke_live.py --provider codex
```

It retains a toy repository and private/public/runtime directories under
`.cache/smoke/`, measures three baseline seeds, makes two real proposals using
one runtime, prints status/metric/idea/denials and the trace path. Its deterministic
hidden reference lives only in the private session. Set AUTORESEARCH_SMOKE_ROOT
to change the scratch parent. The offline suite exercises FakeProposer and
contract-faithful backend fakes; it never calls a paid provider.

Training requires macOS and a working `sandbox-exec`. There is no production
fallback to unrestricted training. The `macos_sandbox` tests probe the sandbox
and skip when it cannot run; other tests replace only the wrapper and use real
Git repositories and Python subprocesses. Test scratch files stay in `.cache/`.
The pytest source paths permit offline testing against the specified sibling
checkout when package installation is unavailable.

```python
from pathlib import Path

from autoresearch_mcp.loop import finalize
from autoresearch_mcp.loop_core import baseline, score_candidate
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
optional field for the regenerated state summary. `provider` selects proposal dispatch.

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
candidate functions log each actual run once. Scope rejections and no-change outcomes also
get an audit row, with null seed/memory and zero wall time. The last candidate
row carries its decision; earlier remeasurement rows remain `ok`. Returned
candidate `metric` is the combined median, while JSONL `metric` is that row's
individual measurement. `finalize` trains the selected commit and evaluates it
with `holdout_cmd`, without using that result for selection or changing best.

Serialization and background-run activity are in-process only. Use one MCP
server or foreground CLI loop at a time; a separate CLI status command reads
durable state but cannot inspect another process's run thread/error. `best.json`
and regenerated state use atomic
file replacement; Git ref and JSON updates are separate operations. A branch
that disagrees with `best.json` fails visibly before further work; automatic
interruption recovery and multi-server coordination are outside B2. Calling
finalize is the caller's decision to stop tuning; this library does not enforce
a session lifecycle beyond the candidate iteration limit.

See the [B2 report](wiki/B2-report.md) and [B3 report](wiki/B3-report.md) for source
usage, validation and coordinator checks.
