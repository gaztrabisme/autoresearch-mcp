# B2 decisions

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
