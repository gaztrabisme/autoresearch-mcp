"""MCP 2 stdio entry point; provider runtimes start only on experiment calls."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import loop, loop_core
from .service import RunManager
from .service import stop as request_stop
from .store import Config, Session

INSTRUCTIONS = (
    "autoresearch-mcp runs a sequential autonomous experiment loop for any measurable target. "
    "Initialize a session, install its private evaluator/data, baseline, then iterate or run. "
    "Training cannot read private session material; only the evaluator supplies metrics. "
    "Children/proposer agents never call this server or its tools. "
    "Sessions live in ~/.autoresearch by default (AUTORESEARCH_HOME overrides it); "
    "public worktrees/data/artifacts use AUTORESEARCH_WT_ROOT. "
    "run returns immediately; inspect status and request stop before finalize. "
    "Stop and time caps are cooperative; a started iteration finishes before stopping."
)


def create_server(manager: RunManager | None = None) -> MCPServer:
    manager = manager or RunManager()

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await asyncio.to_thread(manager.shutdown)

    app = MCPServer("autoresearch-mcp", instructions=INSTRUCTIONS, lifespan=lifespan)

    @app.tool(name="init")
    def init(
        name: str,
        target_repo: str,
        scope: list[str],
        train_cmd: list[str] | str,
        eval_cmd: list[str] | str,
        metric: str = "score",
        direction: str = "min",
        steps: int = 100,
        noise_k: float = 2.0,
        min_gain: float = 0.0,
        baseline_runs: int = 5,
        remeasure_runs: int = 3,
        timeout_factor: float = 2.0,
        initial_timeout_s: float = 60.0,
        max_iter: int = 100,
        memory_free_min_pct: float = 20.0,
        provider: str = "",
        holdout_cmd: list[str] | str | None = None,
        goal: str = "",
        plateau_limit: int = 10,
    ) -> dict:
        """Pre-register all configuration and create the private session and Git branch."""
        with manager.exclusive():
            config = Config(
                target_repo=Path(target_repo),
                scope=scope,
                train_cmd=train_cmd,
                eval_cmd=eval_cmd,
                metric=metric,
                direction=direction,
                steps=steps,
                noise_k=noise_k,
                min_gain=min_gain,
                baseline_runs=baseline_runs,
                remeasure_runs=remeasure_runs,
                timeout_factor=timeout_factor,
                initial_timeout_s=initial_timeout_s,
                max_iter=max_iter,
                memory_free_min_pct=memory_free_min_pct,
                provider=provider,
                holdout_cmd=holdout_cmd or [],
                goal=goal,
                plateau_limit=plateau_limit,
            )
            session = Session.create(name, config)
            return {
                "session": name,
                "session_dir": str(session.session_dir),
                "workspace_dir": str(session.workspace_dir),
                "branch": session.branch,
            }

    @app.tool(name="baseline")
    async def baseline(name: str) -> dict:
        """Measure the committed target with baseline_runs fresh seeds; waits for completion."""
        with manager.exclusive():
            return await asyncio.to_thread(loop_core.baseline, Session.load(name))

    @app.tool(name="iterate")
    async def iterate(name: str) -> dict:
        """Propose and score one iteration; waits for its final decision."""
        with manager.exclusive():
            return await asyncio.to_thread(loop.iterate, Session.load(name))

    @app.tool(name="run")
    def run(
        name: str,
        max_iter: int | None = None,
        time_cap_s: float | None = None,
        plateau_limit: int | None = None,
    ) -> dict:
        """Start a background loop and return immediately; refuses a concurrent operation."""
        try:
            return manager.start(
                Session.load(name),
                max_iter=max_iter,
                time_cap_s=time_cap_s,
                plateau_limit=plateau_limit,
            )
        except (RuntimeError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @app.tool(name="status")
    def status(name: str) -> dict:
        """Read state.md, the last ten audit rows, and background activity/result/error."""
        return manager.status(Session.load(name))

    @app.tool(name="stop")
    def stop(name: str) -> dict:
        """Persist a stop flag; the current iteration finishes and later ones do not start."""
        return request_stop(Session.load(name))

    @app.tool(name="finalize")
    async def finalize(name: str) -> dict:
        """Evaluate the selected best on held-out data and write the final report."""
        with manager.exclusive():
            path = await asyncio.to_thread(loop.finalize, Session.load(name), print_path=False)
            return {"report_path": str(path), "report": path.read_text()}

    return app


app = create_server()


def main():
    app.run()


if __name__ == "__main__":
    main()
