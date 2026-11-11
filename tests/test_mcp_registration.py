"""The lazily-built FastMCP server exposes every decorated tool/resource.

``server.mcp`` defers ``import fastmcp`` (≈1s) until the first ``run()``
so CLI commands don't pay for it. The cost of that laziness is that a
tool whose signature fastmcp/pydantic can't turn into a schema would
only fail at ``serve`` time — so this test builds the real server in
the suite and checks the roster matches the ``@mcp.tool`` decorators.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
SERVER_PY = SRC / "apple_mail_mcp" / "server.py"


def _run_python(code: str) -> str:
    """Run ``code`` in a fresh interpreter and return its stdout.

    ``PYTHONPATH=src`` makes it import this checkout's package even
    when it isn't installed into the interpreter running the suite.
    """
    pythonpath = os.pathsep.join(
        p for p in (str(SRC), os.environ.get("PYTHONPATH")) if p
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": pythonpath},
    )
    return out.stdout.strip()


def _decorated(attr: str) -> set[str]:
    tree = ast.parse(SERVER_PY.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if (
                isinstance(target, ast.Attribute)
                and target.attr == attr
                and isinstance(target.value, ast.Name)
                and target.value.id == "mcp"
            ):
                names.add(node.name)
    return names


def test_importing_server_does_not_import_fastmcp():
    """The whole point of the lazy wrapper; guards against a stray import.

    Runs in a subprocess: evicting modules from ``sys.modules`` in-process
    would break every other test's ``patch("apple_mail_mcp.server...")``.
    """
    code = (
        "import sys, apple_mail_mcp.server; "
        "print(sorted(m for m in sys.modules if m.startswith('fastmcp')))"
    )
    assert _run_python(code) == "[]"


@pytest.mark.asyncio
async def test_real_server_registers_every_tool_and_resource():
    from apple_mail_mcp import server

    real = server.mcp.server  # triggers the fastmcp import + registration
    tools = {t.name for t in await real.list_tools()}
    resources = {str(r.uri) for r in await real.list_resources()}

    assert tools == _decorated("tool")
    assert resources == {"index://status"}
    assert _decorated("resource") == {"index_status"}
    # Cached: a second access must not rebuild (would double-register).
    assert server.mcp.server is real


@pytest.mark.asyncio
async def test_registry_accepts_bare_and_called_tool_decorators():
    """``@mcp.tool`` and ``@mcp.tool(**kwargs)`` both register, and the
    keyword arguments (e.g. MCP annotations) reach fastmcp."""
    from apple_mail_mcp.server import _LazyFastMCP

    reg = _LazyFastMCP("t")

    @reg.tool
    def bare() -> str:
        return "a"

    @reg.tool(annotations={"readOnlyHint": True})
    def hinted() -> str:
        return "b"

    assert bare() == "a" and hinted() == "b"  # functions returned unchanged
    tools = {t.name: t for t in await reg.server.list_tools()}
    assert set(tools) == {"bare", "hinted"}
    assert tools["hinted"].annotations is not None
    assert tools["hinted"].annotations.readOnlyHint is True


def test_decorating_and_dunder_probes_stay_lazy():
    """``__getattr__`` must not make the decorator path build the server.

    ``tool``/``resource`` are real methods, so normal lookup finds them
    and ``__getattr__`` never runs. Dunder probes (``inspect.unwrap``'s
    ``hasattr(f, "__wrapped__")``, ``copy.copy``'s ``__setstate__``)
    are refused rather than forwarded, so they stay lazy too.
    """
    code = (
        "import copy, sys\n"
        "from apple_mail_mcp.server import _LazyFastMCP, mcp\n"
        "reg = _LazyFastMCP('t')\n"
        "reg.tool(lambda: 1)\n"
        "reg.tool(annotations={'readOnlyHint': True})(lambda: 2)\n"
        "reg.resource('t://r')(lambda: 'r')\n"
        "assert not hasattr(mcp, '__wrapped__')\n"
        "copy.copy(mcp)\n"
        "assert reg._server is None and mcp._server is None\n"
        "print(sorted(m for m in sys.modules if m.startswith('fastmcp')))"
    )
    assert _run_python(code) == "[]"


@pytest.mark.asyncio
async def test_unknown_attributes_fall_through_to_real_server():
    """``mcp.<anything>`` reaches the real FastMCP; ``mcp.server`` is it.

    Covers what external tooling does with the exported object:
    attribute access (``fastmcp inspect``), ``mount()``, and ``Client``,
    which needs the real instance because transport inference is an
    ``isinstance`` check that ``__getattr__`` can't satisfy.
    """
    from fastmcp import Client, FastMCP

    from apple_mail_mcp.server import _LazyFastMCP

    reg = _LazyFastMCP("t")

    @reg.tool
    def ping() -> str:
        return "pong"

    assert reg._server is None
    assert reg.name == "t"  # not defined on the wrapper: falls through
    assert isinstance(reg.server, FastMCP)
    assert {t.name for t in await reg.list_tools()} == {"ping"}
    with pytest.raises(AttributeError):
        reg.no_such_attribute  # noqa: B018

    parent = FastMCP("parent")
    parent.mount(reg)
    async with Client(parent) as client:
        assert {t.name for t in await client.list_tools()} == {"ping"}

    async with Client(reg.server) as client:
        result = await client.call_tool("ping", {})
        assert result.data == "pong"
