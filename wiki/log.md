# Log

## [2026-10-27] uat | B6 fixes live-checked: e2e3 session (100 steps) baseline 3 runs + 1 codex iteration scored via snapshot root, discarded correctly; native sandbox tests pass

## [2026-10-27] fix | Move snapshots to a separate AUTORESEARCH_SNAP_ROOT, deny it to proposers, preserve evaluator-child access and log snapshot paths; 154 offline tests pass, 5 native checks pending.

## [2026-10-27] fix | B6 fixes restrict training writes, snapshot artifacts, harden Git, bound proposers, isolate confirmation medians, recover worktrees and logs, and retain cancelled admission. Offline: 142 passed, 3 native checks pending.

## [2026-10-27] uat | Haiku CLI proposer live smoke: kept change score 30->0 after 3 re-measures; next iteration no_change. Smoke script now honours claude-cli providers.

## [2026-10-27] build | Added sandboxed Claude CLI Haiku proposer and one-shot failure fallback; offline tests 116 passed, one skipped; live acceptance and independent review pending.

## [2026-10-27] init | Repo created for the autoresearch MCP server (plan: ~/.claude/plans/1-ok-2-ok-fluttering-raven.md)

## [2026-10-27] build | B2 sequential experiment-loop library

Built seven small production modules (743 lines) with configurable private/public
roots, pre-registered configuration, append-only run/idea history, regenerated
state, a real imported subagent sandbox wrapper, separate trusted evaluation,
process-group timeouts, a memory gate and one session-best Git branch. Candidate
keeps require a fresh-seed remeasurement median above a frozen threshold; scope,
ignored-file and measured-tree checks protect commits. Holdout runs only through
finalize. The spike REPORT sections 1/3/5/8, prototype code and train-loop guidance
were used as recorded in [the B2 report](B2-report.md); prototype imports were
avoided. MCP tools, proposer dispatch and CLI remain B3.

Final local evidence: 41 pytest cases passed and one real macOS sandbox case
auto-skipped (nested sandbox_apply denied); uv --no-sync Ruff passed. Declared uv
environment resolution needs unavailable PyPI DNS, and automatic approval review
rejected uv pytest invocations and the independent review agent. Full coordinator
acceptance remains pending. See [decisions](decisions.md), [active work](active-work.md)
and [API documentation](../README.md).

## [2026-10-27] accept | B2 coordinator acceptance

Coordinator reported 42 tests passing outside the sandbox and committed B2 as
60b8112. The earlier local sandbox skip is now covered by that external run.

## [2026-10-27] build | B3 proposer dispatch, loop controls, MCP and CLI

Added SubagentProposer/FakeProposer, complete state/tried/scope briefs, controlled
iteration/run loops, a final report, seven FastMCP stdio tools, foreground CLI
entry points and an executable two-iteration live Codex smoke script. Each loop
owns one runtime, each proposal closes its agent, and run metadata joins B2's
existing audit rows. Calls and denials are deduplicated across remeasurements.
Runtime bootstrap files stay outside the denied evaluator session.

Final local evidence: 81 passed, one expected macos_sandbox skip, Ruff passes.
Tests include a real stdio MCP handshake, admission refusal and responsive
status/stop, one keep plus one discard, default/configurable plateaus, runtime
cleanup and report contents. Automatic approval review rejected uv pytest;
direct Python pytest ran. No live provider invocation was attempted, as directed.
Coordinator uv checks and `scripts/smoke_live.py --provider codex` remain pending.
See [B3 report](B3-report.md), [decisions](decisions.md) and [active work](active-work.md).

## [2026-10-27] fix | B3 MCP 2 compatibility and project-interpreter verification

The coordinator rejected B3 because system Python had MCP 1 while the project
venv has MCP 2.3.0. Reproduced the removed FastMCP import under .venv/bin/python,
then ported server.py to MCPServer, app.tool and app.run using subagent's server
reference. Ported schema/client/result assertions to MCP 2 snake_case APIs and
CallToolResult. Expected run refusals now raise ToolError to preserve their
message. No fastmcp package was added.

Project checks: `.venv/bin/python -m pytest -q` reported 81 passed, 1 expected
macos_sandbox skip in 43.84 s; `.venv/bin/ruff check` passed. These results
supersede the earlier system-interpreter acceptance claim. The real stdio
handshake and all nine MCP server tests pass under MCP 2. Live smoke remains
for the coordinator. See [B3 report](B3-report.md).
