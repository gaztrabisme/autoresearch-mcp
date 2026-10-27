# Active work

Owner: autoresearch-mcp maintainers. Reviewed: 2026-10-27.

B6 fixes the reviewed H1/H2/H3/H5 and M7/M8/M10/M11/M12 findings. The
[B6 report](B6-report.md) maps each change to tests and records LOAD usage.
Training has an output/temp/dev write allowlist, artifacts are copied privately
without following links, Git is hardened and validates worktree metadata,
proposer waits and process groups are bounded, confirmation medians exclude
screening, unlogged worktrees are abandoned, torn log tails are repaired, and
foreground cancellation retains admission until the worker exits.

Coordinator live training passed, but the evaluator's sandboxed candidate child
could not read session-local snapshots. The correction moves snapshots to
`$AUTORESEARCH_SNAP_ROOT/<session>/<run_id>/` (default `~/.cache/autoresearch-snap`),
validates root separation and denies the whole snapshot root to both proposers.
Training's write allowlist and the no-follow regular-file copy remain intact.
Rows record `snapshot`; evaluation and successful `artifact` records use that path.
The README describes the candidate-child deny on the session folder only.

Final offline verification: 154 passed, five native cases deselected in 68.64 s
using `.venv/bin/python -m pytest -q -m 'not macos_sandbox'`. Focused checks:
80 passed, four native cases skipped. Ruff and Git whitespace checks pass.
No commit, live provider call, native sandbox or real Git hooks were executed
here. Marked native checks need coordinator execution.

Next: coordinator reviews the change, runs `.venv/bin/python -m pytest -q` and
`.venv/bin/ruff check` outside this environment, including marked native checks
with AUTORESEARCH_SKIP_NATIVE unset. Independent review of this patch remains
deferred. Historical acceptance is recorded in the B2, B3 and Claude CLI reports.

Standing assumptions: trainers honor AUTORESEARCH_STEPS and write artifacts to
AUTORESEARCH_OUT (now enforced); evaluator scripts are trusted; v1 has one server
process, one best branch,
and queued execution. Callers stop tuning when they request held-out finalization.
Run admission and activity are process-local. Stop/time caps finish an admitted
iteration; memory waiting before the next iteration can stop early. Provider
runtime files must stay outside the evaluator session and snapshot root denied
to proposers. Evaluator candidate children deny only the session folder so
they can read the separate artifact snapshot.
