# B3 acceptance report

Owner: autoresearch-mcp maintainers. Reviewed: 2026-10-27.

Verdict: **Project-venv checks PASS; live coordinator acceptance pending.** B2
was accepted with 42 tests passing outside the sandbox and committed as 60b8112.
B3 implementation and documentation are written; no live provider call was made.

The previous system-Python pass did not establish B3 compatibility: it used MCP
1.22.0, while the project resolves MCP 2.3.0. The coordinator reproduced the
FastMCP import failure; `.venv/bin/python -m pytest` reproduced it here before
the correction. The following project-interpreter results supersede that claim.

Current validation:

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | **81 passed, 1 skipped**, 43.84 s. All 42 B2 cases plus 40 B3 cases are collected. |
| `.venv/bin/python -m pytest -q tests/test_b3_server.py --tb=short` | **9 passed**, including real stdio handshake, background refusal and finalization. |
| `.venv/bin/ruff check` | **All checks passed!** |
| `git diff --check` | Pass. |
| `scripts/smoke_live.py --provider codex` | **Not run**, as explicitly requested for this sandbox. Script is executable and included for the coordinator. |

Correction verification uses `/Users/GaryT/Documents/Work/tools/mcp/autoresearch-mcp/.venv/bin/python`,
MCP **2.3.0**, pytest **9.1.1** and Ruff **0.17.3**. No separate fastmcp package
was added. server.py imports MCPServer from mcp.server, registers tools through
app.tool and starts with app.run(), following subagent's MCP 2 server. Tests use
the MCP 2 snake_case model fields and CallToolResult API. Expected run refusals
use the SDK's ToolError so their actionable message is preserved.

Skipped case: `test_trainer_cannot_read_session_secret` (`macos_sandbox`). The
required sandbox-exec probe failed with `sandbox_apply: Operation not permitted`.
No B3 test skipped. The stdio handshake launches the real MCP server and client
without starting a provider runtime or approval socket.

New production modules (physical lines, comments/blanks included):

| Module | Lines | Behavior |
| --- | ---: | --- |
| `proposer.py` | 142 | Protocol/result, subagent lifecycle, IDEA extraction, trace denial counts and FakeProposer. |
| `brief.py` | 39 | Full tried list, state/goal/metric/gate, scope matches and editing rules. |
| `loop.py` | 153 | Iteration, runtime ownership, memory/stop/time/plateau controls and finalization. |
| `report.py` | 61 | Baseline/final metrics, keep ideas/gains, status counts, holdout, noise and proposer totals. |
| `service.py` | 96 | Background-run admission, mutation exclusion, result/error, stop and shutdown. |
| `server.py` | 151 | MCP 2 MCPServer stdio server and all seven tools; lazy provider startup. |
| `cli.py` | 76 | All seven foreground commands and TOML initialization. |
| **New module total** | **718** | All new modules are below 160 lines. |
| `scripts/smoke_live.py` | 88 | Retained deterministic toy, three baseline seeds and two real proposals sharing one runtime. |

Existing modules changed: store.py (215 lines: plateau_limit, transient log
context, complete-line progress reads); queue.py (59: reusable interruptible
pre-proposal memory gate); loop_core.py (229: proposer audit metadata);
__init__.py (1: package description). B2 selection and Git commit logic stay in
place. pyproject.toml adds both console entry points; no new runtime dependency
was introduced.

LOAD usage (read before implementation):

| Input | Used | Application |
| --- | --- | --- |
| subagent/core.py | Yes | InProcessServer construction, start/delegate/close_agent/stop lifecycle and trace_path. |
| subagent/runs.py Run | Yes | await_ready is a threading.Event; terminal state, result_text/final_response and agent_id drive proposal outcomes. Human gates are not treated as completed work. |
| subagent/config.py | Yes | Settings.session_root and config.load; runtime files use a fresh, configurable subdirectory outside the denied evaluator session, with tracing enabled. |
| knowledge_base/server.py lines 1–60 | Yes, initial style reference | Name/instructions and lightweight server construction; its MCP 1 FastMCP API was replaced during the correction. |
| subagent/mcp_server.py | Yes, correction reference | MCPServer import/construction, app.tool decorators, app.run and ToolError handling provide the MCP 2 pattern. |
| B2 references | Continued use | Existing scoring, sandbox, Git and recording contracts remain the base. |
| Books | None | No additional book sources needed. |

Pattern: intrinsic-artifact gate — FakeProposer and real local Git/train/eval
fixtures check the complete loop; a contract-faithful in-process-server fake
checks API calls without paid provider access. Direct implementation continues
because delegation and independent-agent tools were denied in B2. The additional
official OpenAI documentation lookup for Codex registration was also rejected by
automatic approval review as an unrecognized web tool; it did not run.

Pre-registered weaknesses: duplicate proposer counts across remeasurements,
cleanup on proposer errors, stopped/failed runs appearing successful, background
run admission races, and accidental stdout output corrupting MCP stdio.

Source paths: the subagent LOAD files are under
`/Users/GaryT/Documents/Work/tools/mcp/subagent-mcp-wt/autoresearch-aware/src/subagent/`.
The FastMCP reference is
`/Users/GaryT/Documents/Work/tools/knowledge-base/knowledge_base/server.py` lines
1–60. Focused reads of subagent/telemetry/trace.py confirmed the kind/agent_id/action
schema, and subagent/install.py confirmed local Codex mcp_servers/command/
tool_timeout_sec registration. B2 source usage is detailed in [the B2 report](B2-report.md).
No external source was edited and no prototype module was imported.

The pre-registered weaknesses are covered: call IDs prevent double counting
remeasurements; finally blocks close agents and remove worktrees; non-completed
proposals never train; background errors appear in status; admission is locked;
MCP finalization suppresses printing and a real stdio handshake succeeds. Source
inspection caught the runtime-bootstrap/deny-root collision; dedicated tests now
require runtime files outside the private session and reject invalid placement.

Complete new test function names (33 functions, 40 parameterized cases):

`tests/test_b3_proposer.py`:

```text
test_idea_extraction
test_brief_contains_tried_list_scope_and_rules
test_denials_are_counted_for_only_that_agent
test_subagent_api_and_server_lifecycle
test_agent_closed_even_when_wait_fails
test_proposer_runtime_is_outside_denied_session
test_proposer_refuses_runtime_inside_private_session
test_server_is_stopped_after_start_failure
```

`tests/test_b3_loop.py`:

```text
test_run_keeps_and_discards_with_fake_proposer
test_plateau_stop_is_configurable
test_stop_flag_prevents_a_proposer_call
test_stop_flag_is_observed_between_iterations
test_time_cap_prevents_another_iteration
test_memory_gate_precedes_worktree_and_proposer
test_finalize_writes_report_with_holdout_and_proposer_totals
test_proposer_failure_is_recorded_without_training
test_proposer_exception_cleans_up_and_logs
test_no_duplicate_denials_across_remeasurements
test_invalid_plateau_limit_is_rejected
test_run_starts_and_stops_one_proposer_runtime
test_noncompleted_proposals_are_never_scored
test_keep_resets_plateau_streak
test_memory_wait_can_stop_without_creating_worktree
test_default_plateau_stops_after_ten_non_keeps
```

`tests/test_b3_server.py`:

```text
test_mcp_server_lists_seven_tools
test_run_refuses_a_second_concurrent_run
test_background_failure_is_visible
test_cli_status_stop_and_init
test_mcp_run_returns_immediately_and_status_stop_remain_available
test_mcp_finalize_does_not_print_on_stdio
test_mcp_stdio_handshake_lists_tools_and_reads_status
test_cli_run_is_foreground_and_uses_requested_limits
test_status_ignores_only_an_unfinished_final_log_row
```

IDEA extraction has five cases; invalid plateau limits have two; non-completed
proposals have three (working/human gate, completed_unverified, cancelled).

Coordinator handoff:

```sh
uv run pytest -q
uv run ruff check
scripts/smoke_live.py --provider codex
```

The smoke script retains its scratch directory and trace for inspection. The
provider must exist in subagent config and have working login/access; the script
chooses no model and performs no registration or provider setup.

Operational limits: run admission/status are process-local; one server or CLI
loop should own execution. Stop/time caps finish an admitted iteration and do
not replace hard train/eval timeouts. Injected proposer instances are caller-owned.
Normal MCP shutdown writes stop and waits for cleanup. Automatic crash recovery
and force-cancellation of a provider are outside this unit. Independent agent
review remains deferred following the B2 tool rejection; source inspection and
tests are not presented as independent review.
