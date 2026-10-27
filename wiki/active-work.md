# Active work

Owner: autoresearch-mcp maintainers. Reviewed: 2026-10-27.

B2 was accepted by the coordinator: 42 passed outside the sandbox, commit
60b8112. B3 now implements guarded proposals, brief construction, controlled loops,
seven MCP tools, foreground CLI, final reports and a retained live smoke script.
The MCP 2 correction follows subagent's MCPServer API. The [B3 report](B3-report.md)
records 81 passed, one expected macos_sandbox skip and a passing Ruff check using
the project venv (MCP 2.3.0). The earlier system-Python validation is superseded.
No live provider call was made here.

Next: coordinator runs `uv run pytest -q`, `uv run ruff check`, then
`scripts/smoke_live.py --provider codex` outside this environment. The requested
`.venv/bin/python -m pytest -q` and `.venv/bin/ruff check` both passed here.
Independent review remains deferred following the B2 review-tool rejection.

Standing assumptions: trainers honor AUTORESEARCH_STEPS and write artifacts to
OUT; evaluator scripts are trusted; v1 has one server process, one best branch,
and queued execution. Callers stop tuning when they request held-out finalization.
Run admission and activity are process-local. Stop/time caps finish an admitted
iteration; memory waiting before the next iteration can stop early. Provider
runtime files must stay outside the evaluator session denied to children.
