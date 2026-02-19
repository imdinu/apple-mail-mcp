"""MCP client harness for benchmarking.

Spawns competitor MCP servers via subprocess and times real tool calls
using the JSON-RPC over stdio protocol.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from statistics import median

TIMEOUT_SECONDS = 60
WARMUP_RUNS = 5
MEASURED_RUNS = 10


@dataclass
class BenchmarkResult:
    """Result of a single benchmark scenario."""

    competitor: str
    scenario: str
    timings_ms: list[float] = field(default_factory=list)
    success: bool = True
    error: str | None = None

    @property
    def median_ms(self) -> float:
        if not self.timings_ms:
            return 0.0
        return median(self.timings_ms)

    @property
    def p5_ms(self) -> float:
        if not self.timings_ms:
            return 0.0
        idx = max(0, int(len(self.timings_ms) * 0.05))
        return sorted(self.timings_ms)[idx]

    @property
    def p95_ms(self) -> float:
        if not self.timings_ms:
            return 0.0
        idx = min(
            len(self.timings_ms) - 1,
            int(len(self.timings_ms) * 0.95),
        )
        return sorted(self.timings_ms)[idx]

    def to_dict(self) -> dict:
        return {
            "competitor": self.competitor,
            "scenario": self.scenario,
            "median_ms": round(self.median_ms, 2),
            "p5_ms": round(self.p5_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "timings_ms": [round(t, 2) for t in self.timings_ms],
            "success": self.success,
            "error": self.error,
        }


class MCPClient:
    """Lightweight MCP client using JSON-RPC over stdio."""

    def __init__(self, command: list[str], cwd: str | None = None) -> None:
        self.command = command
        self.cwd = cwd
        self._process: subprocess.Popen | None = None
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def spawn(self) -> None:
        """Start the MCP server subprocess."""
        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            text=True,
            bufsize=1,
        )

    def _send(self, msg: dict) -> dict:
        """Send a JSON-RPC message and read the response."""
        assert self._process is not None
        assert self._process.stdin is not None
        assert self._process.stdout is not None

        line = json.dumps(msg)
        self._process.stdin.write(line + "\n")
        self._process.stdin.flush()

        # Read response lines, skipping notifications/logs
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            raw = self._process.stdout.readline()
            if not raw:
                raise RuntimeError("Server closed stdout")
            raw = raw.strip()
            if not raw:
                continue
            try:
                resp = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # Skip notifications (no "id" field)
            if "id" in resp:
                return resp
        raise TimeoutError(f"No response within {TIMEOUT_SECONDS}s")

    def initialize(self) -> dict:
        """Send MCP initialize request."""
        return self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "bench-harness",
                        "version": "1.0.0",
                    },
                },
            }
        )

    def send_initialized(self) -> None:
        """Send the initialized notification (no response)."""
        assert self._process is not None
        assert self._process.stdin is not None
        msg = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        self._process.stdin.write(json.dumps(msg) + "\n")
        self._process.stdin.flush()

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call an MCP tool and return the response."""
        return self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments or {},
                },
            }
        )

    def close(self) -> None:
        """Shut down the server process."""
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                self._process.kill()
            self._process = None


def measure_cold_start(command: list[str], cwd: str | None = None) -> float:
    """Measure spawn -> initialize response time in ms."""
    t0 = time.perf_counter()
    client = MCPClient(command, cwd=cwd)
    try:
        client.spawn()
        client.initialize()
        client.send_initialized()
        elapsed = (time.perf_counter() - t0) * 1000
    finally:
        client.close()
    return elapsed


def measure_tool_call(
    client: MCPClient, name: str, arguments: dict | None = None
) -> float:
    """Measure a single tool call in ms."""
    t0 = time.perf_counter()
    resp = client.call_tool(name, arguments)
    elapsed = (time.perf_counter() - t0) * 1000
    # Check for JSON-RPC level errors
    if "error" in resp:
        raise RuntimeError(f"Tool error: {resp['error']}")
    # Check for MCP-level errors (isError in result)
    result = resp.get("result", {})
    if result.get("isError"):
        content = result.get("content", [])
        msg = content[0].get("text", "unknown") if content else "unknown"
        raise RuntimeError(f"MCP tool error: {msg}")
    return elapsed


def run_scenario(
    competitor_name: str,
    command: list[str],
    scenario: str,
    tool_name: str | None = None,
    tool_args: dict | None = None,
    cwd: str | None = None,
    warmup: int = WARMUP_RUNS,
    runs: int = MEASURED_RUNS,
) -> BenchmarkResult:
    """Run a benchmark scenario with warmup + measured runs.

    For "cold_start" scenario, each run spawns a fresh process.
    For tool call scenarios, a single process is reused.
    """
    result = BenchmarkResult(competitor=competitor_name, scenario=scenario)

    try:
        if scenario == "cold_start":
            _run_cold_start(result, command, cwd, warmup, runs)
        else:
            assert tool_name is not None
            _run_tool_calls(
                result,
                command,
                tool_name,
                tool_args,
                cwd,
                warmup,
                runs,
            )
    except Exception as exc:
        result.success = False
        result.error = str(exc)
        print(
            f"  ERROR [{competitor_name}/{scenario}]: {exc}",
            file=sys.stderr,
        )

    return result


def _run_cold_start(
    result: BenchmarkResult,
    command: list[str],
    cwd: str | None,
    warmup: int,
    runs: int,
) -> None:
    """Measure cold start by spawning fresh processes."""
    # Warmup
    for _ in range(warmup):
        try:
            measure_cold_start(command, cwd)
        except Exception:
            pass

    # Measured
    for _ in range(runs):
        elapsed = measure_cold_start(command, cwd)
        result.timings_ms.append(elapsed)


def _run_tool_calls(
    result: BenchmarkResult,
    command: list[str],
    tool_name: str,
    tool_args: dict | None,
    cwd: str | None,
    warmup: int,
    runs: int,
) -> None:
    """Measure tool calls on a single long-lived process."""
    client = MCPClient(command, cwd=cwd)
    try:
        client.spawn()
        client.initialize()
        client.send_initialized()

        # Warmup (bail immediately if first attempt errors)
        for i in range(warmup):
            try:
                measure_tool_call(client, tool_name, tool_args)
            except Exception:
                if i == 0:
                    raise

        # Measured
        for _ in range(runs):
            elapsed = measure_tool_call(client, tool_name, tool_args)
            result.timings_ms.append(elapsed)
    finally:
        client.close()
