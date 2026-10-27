# Active work

Owner: autoresearch-mcp maintainers. Reviewed: 2026-10-27.

B2 was accepted by the coordinator: 42 passed outside the sandbox, commit
60b8112. B3 now implements guarded proposals, brief construction, controlled loops,
seven MCP tools, foreground CLI, final reports and a retained live smoke script.
The MCP 2 correction follows subagent's MCPServer API. The [B3 report](B3-report.md)
records 81 passed, one expected macos_sandbox skip and a passing Ruff check using
the project venv (MCP 2.3.0). The earlier system-Python validation is superseded.
No live provider call was made here.

The [Claude CLI report](claude-cli-report.md) adds a local Claude proposer with
`provider = "claude-cli:haiku"`, optional `fallback_provider`, a configurable
600-second proposer timeout, and per-attempt audit metadata. Failed primary
edits are removed before a single fallback retry of the same iteration.
No-change, incomplete and scored outcomes do not retry. Current evidence:
116 passed, one sandbox-dependent skip in 44.84 s with
`UV_CACHE_DIR=.cache/uv uv run --no-sync pytest -q`; Ruff passes. The default uv
cache could not be opened from this environment. Independent review was
rejected by automatic approval review and remains deferred.

Next: coordinator reviews the change, runs `uv run pytest -q`,
`uv run ruff check`, then a live `autoresearch iterate <session>` with
`provider = "claude-cli:haiku"` outside this environment. The existing
`scripts/smoke_live.py --provider codex` remains a subagent-specific smoke.

Standing assumptions: trainers honor AUTORESEARCH_STEPS and write artifacts to
OUT; evaluator scripts are trusted; v1 has one server process, one best branch,
and queued execution. Callers stop tuning when they request held-out finalization.
Run admission and activity are process-local. Stop/time caps finish an admitted
iteration; memory waiting before the next iteration can stop early. Provider
runtime files must stay outside the evaluator session denied to children.
