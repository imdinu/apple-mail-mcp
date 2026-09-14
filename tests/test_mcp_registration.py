"""The lazily-built FastMCP server exposes every decorated tool/resource.

``server.mcp`` defers ``import fastmcp`` (≈1s) until the first ``run()``
so CLI commands don't pay for it. The cost of that laziness is that a
tool whose signature fastmcp/pydantic can't turn into a schema would
only fail at ``serve`` time — so this test builds the real server in
the suite and checks the roster matches the ``@mcp.tool`` decorators.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SERVER_PY = (
    Path(__file__).parent.parent / "src" / "apple_mail_mcp" / "server.py"
)


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
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


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
