import asyncio
import json
import os
import sys
import threading
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from autoresearch_mcp import cli
from autoresearch_mcp.loop_core import baseline
from autoresearch_mcp.server import create_server
from autoresearch_mcp.service import RunManager


def payload(result):
    assert not result.is_error
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def test_mcp_server_lists_seven_tools():
    tools = asyncio.run(create_server().list_tools())
    assert {tool.name for tool in tools} == {
        "init",
        "baseline",
        "iterate",
        "run",
        "status",
        "stop",
        "finalize",
    }
    init = next(tool for tool in tools if tool.name == "init")
    assert {
        "scope",
        "target_repo",
        "noise_k",
        "provider",
        "plateau_limit",
        "holdout_cmd",
        "fallback_provider",
        "proposer_timeout_s",
    } <= (init.input_schema["properties"].keys())


def test_run_refuses_a_second_concurrent_run(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)
    entered = threading.Event()
    release = threading.Event()

    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return {"reason": "stop"}

    manager = RunManager(run_function=blocking)
    try:
        assert manager.start(session, max_iter=2, time_cap_s=60)["running"]
        assert entered.wait(2)
        with pytest.raises(RuntimeError, match="running"):
            manager.start(session, max_iter=2, time_cap_s=60)
        status = manager.status(session)
        assert status["run_active"]
        assert "Current best" in status["state"]
        assert len(status["last_10"]) == 3
    finally:
        release.set()
        manager.join(3)
    assert not manager.status(session)["run_active"]


def test_background_failure_is_visible(session_factory, offline_sandbox):
    session = session_factory()
    baseline(session)

    def broken(*args, **kwargs):
        raise RuntimeError("background failed")

    manager = RunManager(run_function=broken)
    manager.start(session, max_iter=1, time_cap_s=60)
    manager.join(3)
    assert "background failed" in manager.status(session)["error"]


def test_cli_status_stop_and_init(session_factory, capsys):
    session = session_factory()
    assert cli.main(["status", session.name]) == 0
    assert "Awaiting baseline" in capsys.readouterr().out
    assert cli.main(["stop", session.name]) == 0
    assert (session.session_dir / "stop").exists()
    assert cli.main(["init", "second", "--config", str(session.session_dir / "config.toml")]) == 0
    assert "second" in capsys.readouterr().out


def test_mcp_run_returns_immediately_and_status_stop_remain_available(
    session_factory,
    offline_sandbox,
):
    session = session_factory()
    baseline(session)
    release = threading.Event()

    def blocking(*args, **kwargs):
        assert release.wait(5)
        return {"reason": "stop"}

    manager = RunManager(run_function=blocking)
    server = create_server(manager)

    async def exercise():
        result = payload(await server.call_tool("run", {"name": session.name, "max_iter": 2}))
        assert result["running"]
        assert payload(await server.call_tool("status", {"name": session.name}))["run_active"]
        with pytest.raises(ToolError, match="already running"):
            await server.call_tool("run", {"name": session.name})
        assert payload(await server.call_tool("stop", {"name": session.name}))["stop_flag"]
        assert (session.session_dir / "stop").exists()

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        manager.join(3)


def test_mcp_finalize_does_not_print_on_stdio(
    session_factory, offline_sandbox, monkeypatch, capsys
):
    session = session_factory()
    baseline(session)
    monkeypatch.setattr("autoresearch_mcp.server.Session.load", lambda name: session)
    result = payload(asyncio.run(create_server().call_tool("finalize", {"name": session.name})))
    assert Path(result["report_path"]).is_file()
    assert "Held-out score: 42" in result["report"]
    assert capsys.readouterr().out == ""


def test_mcp_stdio_handshake_lists_tools_and_reads_status(session_factory):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    session = session_factory()
    project = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join(
            [
                str(project / "src"),
                str(project.parent / "subagent-mcp-wt/autoresearch-aware/src"),
            ]
        ),
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "autoresearch_mcp.server"], cwd=str(project), env=env
    )

    async def exercise():
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as client,
        ):
            initialized = await client.initialize()
            assert initialized.server_info.name == "autoresearch-mcp"
            assert len((await client.list_tools()).tools) == 7
            result = await client.call_tool("status", {"name": session.name})
            assert not result.is_error
            assert "Awaiting baseline" in result.content[0].text

    asyncio.run(exercise())


def test_cli_run_is_foreground_and_uses_requested_limits(
    session_factory,
    offline_sandbox,
    monkeypatch,
    capsys,
):
    from autoresearch_mcp import loop

    from .test_b3_loop import fake_values

    session = session_factory()
    baseline(session)
    real_run = loop.run
    requested = []

    def run_in_process(loaded, max_iter, time_cap_s, **kwargs):
        requested.append((max_iter, time_cap_s, kwargs["plateau_limit"]))
        return real_run(loaded, max_iter, time_cap_s, proposer=fake_values(8, 12), **kwargs)

    monkeypatch.setattr(cli.Session, "load", lambda name: session)
    monkeypatch.setattr(cli.loop, "run", run_in_process)
    assert (
        cli.main(
            ["run", session.name, "--max-iter", "2", "--time-cap-s", "60", "--plateau-limit", "3"]
        )
        == 0
    )
    assert requested == [(2, 60, 3)]
    assert json.loads(capsys.readouterr().out)["iterations"] == 2
    assert session.best["metric"] == 8


def test_status_ignores_only_an_unfinished_final_log_row(session_factory):
    session = session_factory()
    with (session.session_dir / "log.jsonl").open("a") as output:
        output.write('{"iteration":')
    assert RunManager().status(session)["last_10"] == []
