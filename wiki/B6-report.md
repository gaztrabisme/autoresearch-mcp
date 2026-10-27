# B6 security and recovery fixes

Owner: autoresearch-mcp maintainers. Reviewed: 2026-10-27.

Implemented all nine requested findings. Offline verification passes;
native sandbox and hook checks are deferred to the coordinator. No commit made.

| Finding | Change | Regression test |
| --- | --- | --- |
| H1 | Training write allowlist contains only its output directory, fresh TMPDIR and /dev, with the private-session deny retained. README requires outputs in AUTORESEARCH_OUT. | `test_training_write_allow_and_fresh_tmpdir`; `test_training_profile_denies_writes_before_allowing_outputs`; native `test_native_training_cannot_write_repo_or_git` |
| H2 | After training and group cleanup, copy artifacts through no-follow file descriptors into the separate private snapshot root. Reject symlinks, hardlinks and special files with crash/error `artifact contains a link or special file`. Evaluate the private copy. | `test_artifact_links_and_special_files_crash` (five variants); `test_artifact_snapshot_copies_nested_regular_files`; snapshot-root correction below |
| H3 | All Git launches use the six requested config overrides and system/global config environment exclusions. Before worktree commands, verify the regular .git pointer and absolute common directory. Invalidate changed metadata with shutil.rmtree and crash/error `git metadata changed` before scope checks. | `test_git_helper_disables_execution_config`; `test_changed_git_metadata_crashes_before_scope_check` (four variants); native `test_native_git_ignores_fsmonitor_and_hooks` |
| H5 | Existing training process-group cleanup precedes the private snapshot, protecting evaluation from detached writers. Claude CLI now starts a new session and kills its group after success and timeout. | `test_detached_writer_cannot_change_private_artifact_snapshot`; `test_claude_cli_kills_descendants_on_exit_and_timeout`; `test_claude_cli_argv_is_wrapped_and_uses_worktree`; `test_claude_cli_timeout` |
| M7 | Bound subagent readiness by proposer_timeout_s, close the timed-out agent and return timeout. | `test_subagent_deadline_closes_timed_out_agent` |
| M8 | Screening remains logged; only the remeasure_runs confirmation median determines a keep. Best statistics also use confirmations alone. README updated. | `test_confirmation_median_excludes_screening_run` (10 vs screening 0, confirmations 1/11/11); `test_kept_best_statistics_use_confirmations_only` |
| M10 | At iterate start, log unrecorded it-n as abandoned, remove with Git then rmtree fallback, and advance numbering. | `test_resume_abandons_unlogged_worktrees` (normal/fallback cleanup, repeated recovery after reload) |
| M11 | Under the shared mutation lock, append saves incomplete final bytes to log.torn, truncates to the last newline, and writes a complete row. | `test_append_repairs_torn_tail_repeatedly_under_lock` (empty log, existing rows, torn UTF-8, repeated reloads); `test_append_waits_for_mutation_lock` |
| M12 | Transfer foreground admission ownership to the executor worker; shield the future so caller cancellation cannot release admission or cancel queued work. Release only after the operation exits. | `test_cancelled_foreground_call_holds_admission_until_worker_exits` (baseline, iterate, finalize) |

Source usage:

| Requested LOAD | Used | Application |
| --- | --- | --- |
| src/autoresearch_mcp/runner.py | Yes | Restricted training writes, artifact snapshots and process cleanup. |
| src/autoresearch_mcp/worktree.py | Yes | Central Git hardening, metadata validation and cleanup fallback. |
| src/autoresearch_mcp/loop_core.py | Yes | Guarded snapshots and confirmation-only best/decision statistics. |
| src/autoresearch_mcp/proposer.py | Yes | Bounded readiness and Claude process-group cleanup. |
| src/autoresearch_mcp/store.py | Yes | Locked torn-tail repair before append. |
| src/autoresearch_mcp/service.py | Yes | Worker-owned foreground admission. |
| src/autoresearch_mcp/server.py | Yes | Apply foreground admission to baseline, iterate and finalize. |
| README.md | Yes | Updated training, evaluation, selection and recovery contracts. |
| /Users/GaryT/Documents/Work/tools/mcp/subagent-mcp-wt/autoresearch-aware/src/subagent/sandbox.py | Yes, read only | Confirmed build_profile/wrap_argv write_allow support; reused the wrapper unchanged. |
| /Users/GaryT/Documents/Work/lab/autoresearch-e2e/session_files/eval.py | Yes, read only | Confirmed candidate model child denies the session folder and receives the snapshot path. |

Books: none. Changes stay in this repository. No sandbox-exec or real Git hooks
were executed here. Native tests use the existing `macos_sandbox` marker.
The test-only AUTORESEARCH_SKIP_NATIVE=1 flag suppresses native collection probes
and explicitly skips marked cases; it does not alter the production sandbox.

Validation of the original B6 patch:

- Initial full offline run: 136 passed, 5 failed, 3 skipped. Four new metadata
  assertions counted rejected helper requests rather than actual launches; the
  fifth expected the old screening-inclusive best wall-time count. Corrected
  these assertions and reran the affected checks.
- Ruff: passes after import ordering and line-length corrections.
- Focused rerun: 27 passed, two native cases skipped in 13.00 s.
- Final full run: **142 passed, 3 native cases skipped in 67.59 s** with
  `AUTORESEARCH_SKIP_NATIVE=1 UV_CACHE_DIR=.cache/uv uv run --no-sync pytest -q`.
  The skip flag prevents any native sandbox probe or hook execution here.
- `UV_CACHE_DIR=.cache/uv uv run --no-sync ruff check`: **PASS**.
- `git diff --check` and `wiki lint wiki`: **PASS**.
- Native acceptance: coordinator runs `uv run pytest -q` without the skip flag
  and `uv run ruff check` outside this sandbox.

The three skipped tests are `test_trainer_cannot_read_session_secret`,
`test_native_training_cannot_write_repo_or_git`, and
`test_native_git_ignores_fsmonitor_and_hooks`. Native behavior is not claimed
verified by the offline wrapper tests.

Remaining contract limits: one server process, trusted evaluator scripts,
cooperative stop between iterations, and no atomic transaction spanning the Git
ref and best.json updates. Independent review of this patch is deferred; this
report records executable regression checks, not a separate reviewer verdict.

## Live evaluator correction

Coordinator live evidence: training passed with the write allowlist, but an
evaluator that ran candidate model code in a child denying the session directory
failed with exit 3 / EOFError. The original snapshot lived inside the denied
session. Read the supplied live evaluator at
`/Users/GaryT/Documents/Work/lab/autoresearch-e2e/session_files/eval.py` to confirm
the child receives the artifact path and denies only the private session.

Snapshots now live at `$AUTORESEARCH_SNAP_ROOT/<session>/<run_id>/`, defaulting
to `~/.cache/autoresearch-snap/<session>/<run_id>/`. Create/load validate that
the snapshot root and session/workspace roots never overlap in either direction,
including symlink aliases. New snapshot directories use mode 0700. Training's
write allowlist excludes this root. Every SubagentProposer and ClaudeCliProposer
denies reads and writes of the entire snapshot root; runtime bootstrap files
are also refused inside it. The no-follow / regular-file / nlink==1 copy is
unchanged. Rows record the reserved `snapshot` path and successful rows' `artifact`
matches the path supplied to evaluation as `AUTORESEARCH_ARTIFACT`.

README now requires evaluators that execute candidate code to sandbox that code
with a deny on the session folder only. The separate snapshot stays readable to
that child while evaluator scripts and validation/holdout data remain denied.

Regression checks:

- `test_snapshot_root_default`
- `test_snapshot_root_roundtrips_and_has_private_permissions`
- `test_snapshot_root_overlap_rejected_on_create_and_load` (six root relations)
- `test_snapshot_root_symlink_overlap_rejected` (session/workspace aliases)
- `test_evaluator_child_reads_external_snapshot_and_logs_path`
- `test_subagent_api_and_server_lifecycle` and
  `test_claude_cli_argv_is_wrapped_and_uses_worktree` now assert both deny paths.
- `test_proposer_refuses_runtime_inside_snapshot_root`
- `test_training_write_allow_and_fresh_tmpdir` now excludes the snapshot root.
- Native `test_native_evaluator_child_reads_snapshot_but_cannot_read_session`
- Native `test_native_proposer_snapshot_deny_blocks_reads_and_writes`

Verification of this correction:

- Focused `.venv/bin/python -m pytest -q` run covering snapshot roots, B6,
  subagent and Claude adapters: **80 passed, 4 native tests skipped in 28.84 s**
  with AUTORESEARCH_SKIP_NATIVE=1.
- Full `.venv/bin/python -m pytest -q -m 'not macos_sandbox'`: **154 passed,
  5 native tests deselected in 68.64 s**.
- `.venv/bin/ruff check` and `git diff --check`: **PASS**.
- Automatic approval review rejected the environment-prefixed full-suite command.
  The full run used explicit native-test deselection instead. Collection now runs
  after marker selection and performs no sandbox probe when no native cases remain.
- Native execution remains for the coordinator with
  `.venv/bin/python -m pytest -q` outside this sandbox. No sandbox-exec or real
  hooks were executed here; no commit was made.
