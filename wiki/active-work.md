# Active work

Owner: autoresearch-mcp maintainers. Reviewed: 2026-10-27.

B2 library and [acceptance report](B2-report.md) are written. Final offline
verification: 41 passed, 1 macos_sandbox skip; Ruff passes. The declared uv
dependency environment still needs network access. Automatic approval review
rejected uv test invocations and the independent agent review tool; direct Python
pytest succeeded. Coordinator acceptance and independent review remain pending.

Next: coordinator runs the declared uv commands and real macOS sandbox tests
outside this environment; B3 adds MCP tools, proposer dispatch and CLI.

Standing assumptions: trainers honor AUTORESEARCH_STEPS and write artifacts to
OUT; evaluator scripts are trusted; v1 has one server process, one best branch,
and queued execution. Callers stop tuning when they request held-out finalization.
