# Claude CLI proposer acceptance report

Owner: autoresearch-mcp maintainers. Reviewed: 2026-10-27.

Status: implementation and offline verification PASS. Live acceptance and
independent review remain pending. No live Claude proposer call was made. The
existing pytest collection hook checks sandbox availability; the real sandbox
test was skipped in this environment. No repository commit requested or made.

## LOAD usage

| Requested source | Used | Application |
| --- | --- | --- |
| `src/autoresearch_mcp/proposer.py` | Yes | Preserve the Proposer / ProposalResult contract and subagent lifecycle. |
| `src/autoresearch_mcp/loop.py` | Yes | Extend provider selection, failure handling and run ownership. |
| `src/autoresearch_mcp/store.py` | Yes | Persist provider, fallback and proposer timeout configuration. |
| `src/autoresearch_mcp/brief.py` | Yes | Make read instructions compatible with Read-only CLI tools. |
| `README.md` | Yes | Document the two proposer kinds, fallback and global Claude instructions. |
| `/Users/GaryT/Documents/Work/lab/autoresearch-spike/loop.py` | Yes | Reuse the working print/model/tools/permission/JSON flags, setting sources, strict MCP configuration, no persistence and focused system prompt. |
| `/Users/GaryT/Documents/Work/tools/mcp/subagent-mcp-wt/autoresearch-aware/src/subagent/sandbox.py` | Yes | Call wrap_argv with the private session path; wrapper failures must fail closed. |
| Books | None | None requested or used. |

## Key decisions and review scope

Pattern: coordinator-direct with an independent code review. The project wiki
search found the existing proposer and audit contracts in `decisions.md` and
`B3-report.md`; no book knowledge base is needed for this repository change.

- A CLI provider uses existing local Claude login, with no API-key integration.
- A fallback gets a clean worktree at the same iteration and current best.
- Candidate rows identify the effective `proposer`, `fallback_used`, and both
  `proposer_attempts`, with separate call IDs and states.
- Authorization boundary: the CLI always goes through the imported wrapper;
  `test_claude_cli_sandbox_failure_never_launches` proves fail-closed dispatch.
- Failure state: failed/cancelled/timeout and startup exceptions retry once;
  no-change, incomplete, and scored outcomes never retry.
- Pre-registered review weaknesses: startup exceptions bypassing fallback in run;
  partial primary edits leaking into fallback; losing primary errors in the log;
  no-change accidentally triggering paid retries; double-counting attempts across
  remeasurements; wrapper failure launching an unrestricted process.

## Verification

| Check | Result |
| --- | --- |
| Initial test-first run of `tests/test_claude_cli.py` | 21 expected failures for missing CLI class, fallback configuration and selection before implementation. |
| `.venv/bin/python -m pytest -q tests/test_claude_cli.py tests/test_b3_loop.py` | 45 passed in 22.22 s before the final additional edge cases. |
| `uv run pytest -q` | Could not start: the default uv cache under `/Users/GaryT/.cache/uv` is inaccessible in this environment. |
| `UV_CACHE_DIR=.cache/uv uv run --no-sync pytest -q` | **116 passed, 1 skipped in 44.84 s**, using the installed project environment and a writable repository-local cache. |
| `.venv/bin/ruff check` | PASS. |
| `UV_CACHE_DIR=.cache/uv uv run --no-sync ruff check` | PASS. |
| `git diff --check` | PASS. |
| `wiki log wiki build "<one line>"` | PASS; appended the dated build entry to `wiki/log.md`. |
| Wiki lint | Passed before the final wiki edits; the final invocation was rejected by automatic approval review because the CLI was classified as an unverified program. Final lint remains deferred. |
| Live Claude CLI / Haiku iterate | Not run: reserved for the coordinator outside this environment. |

The skipped case is `test_trainer_cannot_read_session_secret`, which requires
real macOS sandbox support. No new tests invoke a real Claude CLI. Their
subprocess is mocked after real Git-backed session setup; loop tests use real
worktrees, edits, local training/evaluation and audit files with only the OS
wrapper replaced.

Independent review: **DEFERRED**. The applicable dev skill requires a separate
code review; automatic approval review rejected the review-agent call because
`collaborationspawn_agent` was unrecognized and its external-data behavior could
not be verified. No independent review is claimed. Source inspection and
executable checks cover the pre-registered weaknesses, with the named startup,
partial-edit, logging, no-change and report-count tests below. Coordinator review
must still assess the implementation before acceptance.

An optional sandbox-disable test switch was briefly introduced; automatic
approval review rejected that test invocation. The switch was removed. Final
verification uses the unchanged sandbox availability hook and normal tests.

## Test names

New tests in `tests/test_claude_cli.py` (parameterized cases cover the state,
configuration and error variants):

```text
test_claude_cli_argv_is_wrapped_and_uses_worktree
test_claude_cli_bin_override_model_and_timeout
test_claude_cli_failures
test_claude_cli_timeout
test_claude_cli_idea_can_precede_trailing_text
test_claude_cli_sandbox_failure_never_launches
test_claude_cli_missing_binary_is_failed
test_claude_cli_provider_selection_and_config_roundtrip
test_claude_cli_timeout_config_is_validated
test_empty_claude_cli_model_is_rejected
test_fallback_after_primary_failure_logs_both_attempts
test_fallback_not_used_after_finished_or_incomplete_proposal
test_fallback_failure_is_not_retried
test_run_uses_claude_cli_provider
test_run_falls_back_after_primary_startup_exception
test_run_falls_back_after_primary_constructor_exception
```

Also updated `test_run_starts_and_stops_one_proposer_runtime` to model lazy
startup and cleanup, and `test_mcp_server_lists_seven_tools` to check that MCP
initialization exposes `fallback_provider` and `proposer_timeout_s`.

## Coordinator handoff

Run `uv run pytest -q` and `uv run ruff check` outside this environment. For a
new session, set these entries in its pre-registration TOML:

```toml
provider = "claude-cli:haiku"
proposer_timeout_s = 600.0
```

After installing its private evaluator and completing its baseline, run
`autoresearch iterate <session>`. Inspect the final row for `proposer =
"claude-cli:haiku"`, `fallback_used = false`, the expected idea and terminal
state. To exercise fallback live, a separate session can use its configured
subagent primary and `fallback_provider = "claude-cli:haiku"`.

The README records the spike finding that global `~/.claude/CLAUDE.md` loads
despite `--setting-sources project`; the focused appended system prompt is
retained. CLI denials remain zero by design, since this adapter has no subagent
guard trace. Proposer timeout is separate from train/evaluation timeouts.
