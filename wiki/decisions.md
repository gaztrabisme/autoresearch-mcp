# Implementation decisions

Owner: autoresearch-mcp maintainers. Reviewed: 2026-10-27.

Pattern: intrinsic-artifact gate — fixed acceptance criteria drive real Git and
train/eval subprocess tests. Implementation is coordinator-direct because the
delegation doctor was rejected by automatic approval review. Review will check
the trust boundary and selection logic independently if an agent is available.

Grounded: the complete project wiki (index and log) contained only the repository
skeleton. The user-specified spike REPORT sections 1, 3, 5, 8 and train-loop.md
supply the relevant prior work; no additional books are needed.

- Context: wall-clock budgets measured shared GPU load. Decision: pass fixed
  AUTORESEARCH_STEPS, preserve throughput as an extra evaluator metric, serialize
  runs, and recompute current-best MAD and timeout after keeps.
- Context: family keeps inflated the prototype's progress. Decision: one best,
  one branch, one frozen threshold for each candidate and all its remeasurements.
- Context: bundled changes and repeated failures obscured attribution. Decision:
  count zero-context Git hunk regions, flag multi_change and repeated_idea, and
  append every outcome to tried.md; these flags inform B3 rather than veto ideas.
- Context: training must not inspect evaluation or held-out data. Decision: reuse
  subagent.sandbox.wrap_argv without an unsandboxed production fallback; only the
  independent evaluator's stdout is parsed. Proving tests:
  test_fake_metric_from_trainer_is_ignored,
  test_trainer_cannot_read_session_secret.
- Context: branch changes must represent measured improvements. Decision: stage
  and snapshot the candidate before evaluation, verify the tree is unchanged
  afterwards, commit on the detached worktree, then compare-and-swap the session
  branch. Proving tests: test_discard_leaves_branch_unchanged,
  test_keep_requires_remeasure_median, test_log_rows_have_required_fields.
- Context: timeouts are resource observations, not merit decisions. Decision:
  terminate the process group and preserve the existing best. Proving test:
  test_timeout_is_inconclusive.

Rejected approaches: importing the prototype, parsing training metrics, retaining
beam/family state, moving thresholds after observing candidates, or silently
falling back when the macOS sandbox cannot start.

Limits: in-process serialization is the v1 contract; multi-server coordination
and automatic recovery across an interrupted Git-ref/best.json update are not
claimed. A branch/best mismatch fails visibly on the next operation.

## B3

- Context: B2 JSONL is append-only, with one row per measurement. Decision: a
  transient Session.log_context adds proposer state/denials/call identity to
  those rows. Reports group iterations for keep/gain/noise decisions and dedupe
  proposer_call_id for calls/denials. Tests:
  test_run_keeps_and_discards_with_fake_proposer,
  test_no_duplicate_denials_across_remeasurements.
- Context: the child needs its runtime bootstrap files but must not read the
  evaluator session. Decision: fresh runtime roots under subagent's configured
  session_root, with optional AUTORESEARCH_SUBAGENT_ROOT or constructor override;
  reject runtime roots inside the denied session. Tests:
  test_proposer_runtime_is_outside_denied_session,
  test_proposer_refuses_runtime_inside_private_session,
  test_subagent_api_and_server_lifecycle.
- Context: await_ready wakes at human gates as well as terminal completion.
  Decision: close every returned agent in a finally, and score only completed
  proposals. Failed, unverified, cancelled or working proposals get an
  inconclusive proposer_error row. Tests:
  test_noncompleted_proposals_are_never_scored,
  test_agent_closed_even_when_wait_fails.
- Context: a loop needs one reusable runtime and reliable cleanup. Decision:
  run owns one context-managed runtime; standalone iterate owns one; explicitly
  injected proposer instances are caller-owned. Tests:
  test_run_starts_and_stops_one_proposer_runtime,
  test_server_is_stopped_after_start_failure.
- Context: MCP run must return promptly while status/stop remain usable.
  Decision: a lock protects background-run admission and other mutations;
  progress reads use atomic state.md and only complete JSONL lines. Failure is
  visible in status. Tests: test_run_refuses_a_second_concurrent_run,
  test_mcp_run_returns_immediately_and_status_stop_remain_available,
  test_status_ignores_only_an_unfinished_final_log_row.
- Context: cancellation inside a keep/re-measure transaction risks partial
  bookkeeping. Decision: stop/time caps are cooperative between iterations;
  the pre-proposal memory gate is interruptible. Plateau history counts prior
  candidates and resets on keep; default ten is configurable. Tests:
  test_default_plateau_stops_after_ten_non_keeps,
  test_keep_resets_plateau_streak,
  test_memory_wait_can_stop_without_creating_worktree.
- Context: stdout carries MCP protocol frames. Decision: library/CLI finalize
  prints its report path; MCP passes print_path=False and returns structured
  content. Reuse MCP 2's MCPServer from the existing SDK dependency.
  Tests: test_mcp_finalize_does_not_print_on_stdio,
  test_mcp_stdio_handshake_lists_tools_and_reads_status.

Independent agent review is deferred: automatic approval review rejected that
tool in B2. B3 used source inspection and executable checks, without claiming
those are independent review. Codex registration syntax was checked against
subagent/install.py after the official documentation lookup was denied.

## B3 MCP 2 correction

Context: the original server and tests were verified under system Python with
MCP 1.22.0; project MCP 2.3.0 removes that FastMCP import and uses snake_case
models/CallToolResult. Decision: follow subagent/mcp_server.py's MCPServer,
app.tool, app.run and ToolError pattern. Port the existing schema, result and
stdio assertions to MCP 2 and run the entire suite with .venv/bin/python plus
.venv/bin/ruff. The original system-interpreter pass is superseded as acceptance
evidence. No dependency was added or pinned back to MCP 1.

## Claude CLI and fallback

Context: the provider lanes cannot currently supply a working proposer, and the
lane environment has no Anthropic API key. Decision: `claude-cli:<model>` runs
the locally authenticated Claude CLI with the spike's working flags, through
the same private-session deny wrapper. A named subagent provider still selects
its configured lane. `fallback_provider` is empty by default; when configured,
only failed/cancelled/timeout or raised primary attempts retry once, from a
clean worktree at the same iteration. No-change, unfinished and scored outcomes
retain their normal decisions. Tests and limitations are in the
[Claude CLI report](claude-cli-report.md).

Each row's `proposer` identifies the effective provider; `fallback_used` records
whether the retry ran; `proposer_attempts` preserves both attempts and their
distinct call IDs. Final reports deduplicate these IDs across remeasurements,
including primary failures. Rejected approaches: an API-key-only adapter,
unrestricted execution after a wrapper failure, retries on no-change or
discard, and scoring a combination of failed primary edits and fallback edits.

## B6

Grounded: searched the whole project wiki for trust, sandbox, recovery,
cancellation, median, Git and lock contracts; used the requested repo files
and the read-only sibling sandbox wrapper. Books: none.
Pattern: coordinator-direct; changes share a small set of boundaries and have
fixed, executable acceptance criteria. No agent delegation was needed.

- Authorization/identity: candidate-controlled paths and config must not select
  server Git metadata or execute hooks. Pin Git config, verify the pointer before
  Git, and verify the common directory before worktree operations. Proving tests:
  test_git_helper_disables_execution_config and
  test_changed_git_metadata_crashes_before_scope_check.
- Effects across the trust boundary: candidate output must not alias private
  evaluation data. Restrict training writes and evaluate a private no-follow copy
  of singly linked regular files after process-group cleanup. Proving tests:
  test_training_write_allow_and_fresh_tmpdir,
  test_artifact_links_and_special_files_crash and
  test_detached_writer_cannot_change_private_artifact_snapshot.
- Failure/restart state: abandoned unlogged worktrees consume an iteration;
  incomplete audit bytes remain recoverable in log.torn. Proving tests:
  test_resume_abandons_unlogged_worktrees and
  test_append_repairs_torn_tail_repeatedly_under_lock; both repeat recovery.
- Guarantee reachability: caller cancellation must not admit overlapping
  mutation while an executor worker remains active. The worker owns release;
  its shielded future survives caller cancellation. Proving test:
  test_cancelled_foreground_call_holds_admission_until_worker_exits.
- Measurement units: keep thresholds remain in evaluator metric units and use
  only confirmation scores; the screening metric stays in JSONL. Proving tests:
  test_confirmation_median_excludes_screening_run and
  test_kept_best_statistics_use_confirmations_only.

Rejected approaches: scope checks after following a candidate .git pointer,
evaluating public artifacts in place, a screening-inclusive median, reusing an
unlogged iteration path, appending past torn bytes, or releasing admission in
the cancelled caller. Native checks remain marked for coordinator execution.

### B6 live evaluator snapshot correction

Context: the coordinator's live evaluator sandboxes candidate model code with
the session folder denied. Session-local snapshots therefore caused EOFError.
Decision: place snapshots under a third, disjoint root with environment/library
overrides; the evaluator child can read it while both proposer adapters deny the
whole root. Keep training's write allowlist unchanged and retain the no-follow
copy. The live evaluator supplied by the user was read to confirm this boundary.
Proving tests: test_evaluator_child_reads_external_snapshot_and_logs_path,
test_snapshot_root_overlap_rejected_on_create_and_load,
test_subagent_api_and_server_lifecycle, and
test_claude_cli_argv_is_wrapped_and_uses_worktree. Native proof:
test_native_evaluator_child_reads_snapshot_but_cannot_read_session and
test_native_proposer_snapshot_deny_blocks_reads_and_writes.
Rejected approach: allowing candidate model code to read the session directory
just to reach its artifact. This would also expose evaluator data.
