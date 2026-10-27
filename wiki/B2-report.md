# B2 acceptance report

Owner: autoresearch-mcp maintainers. Reviewed: 2026-10-27.

Verdict: **PARTIAL — offline implementation checks pass; coordinator acceptance
is pending.** All requested modules and named tests are implemented. No MCP
tools, proposer dispatch or CLI were added (B3).

Validation on Python 3.11.5:

| Check | Result |
| --- | --- |
| `python3 -m pytest -q -rs --tb=short` | **41 passed, 1 skipped**, 21.45 s. Real fixture Git repositories, train/eval scripts and artifacts; 38 test functions, 42 parameterized cases. |
| `uv --cache-dir .cache/uv run --no-sync ruff check` | **All checks passed!** |
| `python3 -m ruff format src tests` | 11 files already formatted on final run. |
| `git diff --check` | Pass, no whitespace errors. |
| `uv sync` with repository-local cache | Blocked: PyPI DNS resolution failed for `https://pypi.org/simple/mcp/`. Offline sync also failed because pytest metadata was not cached. |
| uv pytest commands | Automatic approval review rejected the uv test invocations as unclassifiable. The Python pytest invocation above ran successfully. |
| Independent agent review | Deferred: automatic approval review rejected `collaboration.spawn_agent` as unrecognized. Focused source inspection and regression checks ran; these are not an independent review. |

The installed offline tools are pytest **7.4.3** and ruff **0.13.0**. The project
declares pytest >=8; its dependency-resolved environment could not be installed
here. No lockfile was fabricated. Full `uv run pytest -q` validation in that
environment remains a coordinator check. The uv Ruff command used the available
installed executable with synchronization explicitly disabled.

Skipped test: **test_trainer_cannot_read_session_secret**, marked
`macos_sandbox`. Its collection-time probe was exactly
`sandbox-exec -p "(version 1)(allow default)" /usr/bin/true` and failed with
`sandbox-exec: sandbox_apply: Operation not permitted`. The test is automatically
enabled wherever that probe succeeds; the coordinator must run it outside the
nested sandbox. It uses the real imported wrapper, attempts to open a canary
secret, requires a crash, and searches retained logs for the secret content.
Other tests replace only the OS wrapper explicitly; production has no bypass.

Module inventory (physical lines, including comments and blanks):

| Module | Lines | Responsibility |
| --- | ---: | --- |
| `src/autoresearch_mcp/__init__.py` | 1 | Package boundary. |
| `src/autoresearch_mcp/store.py` | 209 | Validated pre-registration, configurable roots, JSONL/tried/state/best records. |
| `src/autoresearch_mcp/runner.py` | 122 | Separate train/eval processes, real sandbox wrapper, evaluator-only parsing, shared timeout, process-group cleanup. |
| `src/autoresearch_mcp/noise.py` | 26 | MAD, minimum gain and strict min/max improvement. |
| `src/autoresearch_mcp/loop_core.py` | 226 | Baseline, frozen candidate gate, fresh-seed median confirmation, keep/discard and final holdout. |
| `src/autoresearch_mcp/worktree.py` | 107 | Branch/worktree lifecycle, scope/hunks, candidate snapshots, server commits and compare-and-swap branch updates. |
| `src/autoresearch_mcp/queue.py` | 52 | Global in-process lock and injectable memory-pressure gate. |
| **Total** | **743** | All production modules remain below 230 lines. |

The core protects the measured tree against changes during training, checks
ignored and untracked edits, preserves discarded branches, records failures
during a keep, and refuses a branch/best mismatch on resume. Process stdout and
stderr are separate; neither training output nor evaluator stderr can supply
the score. Descendant timeout tests cover both training and evaluation.

Every actual baseline/candidate/holdout run gets one JSONL row with the requested
fields plus seed, phase, run ID, artifact location and timeout budget. Rejected
scope edits and no-ops receive explicit synthetic audit rows. Per-run metrics
stay in JSONL; a candidate's returned metric is its aggregate median. Only the
last candidate row carries its keep/discard decision. The threshold in all its
rows is the frozen threshold from the previous best. The current best's new
MAD, threshold and wall times are persisted in best.json after a keep.

LOAD usage:

| Input | Used | Application |
| --- | --- | --- |
| Spike REPORT.md section 1 | Yes | Pre-register configuration, seed repeats, strict gate, timeout inconclusive. |
| Spike REPORT.md section 3 | Yes | Recompute current-best noise; retain wall time and throughput. |
| Spike REPORT.md section 5 | Yes | One shared in-process execution lane. |
| Spike REPORT.md section 8 | Yes | Fixed steps, refreshed noise, one best instead of family/arm bookkeeping, hunk/repetition flags, tried.md, separated evaluator. Proposer clean-context dispatch is reserved for B3. |
| Spike loop.py | Yes, reference only | Inspected process/metric handling and memory sampling; no prototype import. Its memory sampling used sysctl, so the required memory_pressure reader is new. |
| Spike harness.py | Yes, reference only | METRIC output contract; rebuilt as artifact-based train/eval subprocesses. No MLX dependency or import. |
| subagent/sandbox.py | Yes, reused | Production runner calls subagent.sandbox.wrap_argv(argv, [session_dir]). |
| applied-ml-problem-solver/references/train-loop.md | Yes | Frozen threshold, separate splits, fresh-seed confirmation, negative outcomes, final holdout. |
| Books | None | No additional books needed, as requested. |

The report, loop and harness were read from
`/Users/GaryT/Documents/Work/lab/autoresearch-spike/`. The imported dependency is
`/Users/GaryT/Documents/Work/tools/mcp/subagent-mcp-wt/autoresearch-aware/src/subagent/sandbox.py`.
The experiment guidance was read from
`/Users/GaryT/Documents/Work/tools/agent-workflow/skills/applied-ml-problem-solver/references/train-loop.md`.
No prototype module is imported and no source outside this repository was edited.

Complete test function names in `tests/test_core.py`:

```text
test_fake_metric_from_trainer_is_ignored
test_trainer_cannot_read_session_secret
test_zero_mad_uses_min_gain
test_timeout_is_inconclusive
test_out_of_scope_edit_rejected
test_workspace_not_under_protected_path
test_keep_requires_remeasure_median
test_discard_leaves_branch_unchanged
test_log_rows_have_required_fields
test_memory_gate_waits_then_runs
test_sensitive_home_contains_autoresearch
test_config_and_best_reload
test_invalid_eval_metric_crashes
test_failed_remeasure_is_inconclusive
test_holdout_only_at_finalize
test_untracked_out_of_scope_file_rejected
test_multi_change_and_repeated_idea_are_recorded
test_baseline_failure_does_not_create_best
test_scope_globs_are_rooted_and_slash_aware
test_best_noise_and_timeout_follow_current_best
test_eval_nonzero_exit_crashes_even_with_metric
test_runner_passes_training_contract
```

Complete test function names in `tests/test_edges.py`:

```text
test_evaluator_stderr_cannot_supply_metric
test_sandbox_wrapper_receives_session_path
test_sandbox_failure_never_falls_back
test_timeout_kills_descendants
test_memory_gate_serializes_across_queue_instances
test_ignored_out_of_scope_edit_is_rejected
test_training_cannot_change_the_candidate_being_committed
test_noop_is_recorded_without_training
test_max_direction_keeps_a_higher_median
test_overlapping_storage_roots_are_rejected
test_branch_best_mismatch_fails_visibly
test_max_iter_prevents_another_candidate
test_reload_preserves_best_and_append_only_logs
test_commit_failure_is_recorded
test_config_defaults_and_validation
test_baseline_uses_distinct_seeds_and_median
```

`test_invalid_eval_metric_crashes` has four cases (`nan`, `inf`, `bad`, empty).
`test_timeout_kills_descendants` has two cases (`train`, `eval`). All eleven
explicitly requested test names are present, including the skipped real sandbox
test. The other 31 collected cases pass in this environment.

Remaining coordinator checks: install the declared dependencies; run
`uv run pytest -q` and `uv run ruff check` outside this sandbox; confirm the real
macOS secret-read test runs rather than skips. Independent review is still
deferred. In-process serialization, trusted evaluator scripts, caller-enforced
training steps, and explicit reconciliation after interrupted Git-ref/JSON
updates are the documented v1 limits; automatic crash recovery is not claimed.
