"""CLI entry point for the benchmarking suite.

Usage:
    python -m benchmarks.run                          # run all
    python -m benchmarks.run --competitor imdinu      # one competitor
    python -m benchmarks.run --scenario search_body   # one scenario
    python -m benchmarks.run --runs 5 --warmup 3      # custom counts
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from .competitors import COMPETITORS, Competitor
from .harness import (
    MEASURED_RUNS,
    WARMUP_RUNS,
    BenchmarkResult,
    run_scenario,
)

RESULTS_DIR = Path(__file__).parent / "results"

# Standard scenario order
SCENARIOS = [
    "cold_start",
    "list_accounts",
    "get_emails",
    "search_subject",
    "search_body",
]


def run_competitor(
    competitor: Competitor,
    scenarios: list[str],
    warmup: int,
    runs: int,
) -> list[BenchmarkResult]:
    """Run all requested scenarios for a single competitor."""
    results: list[BenchmarkResult] = []

    print(f"\n{'─' * 50}")
    print(f"  {competitor.name}")
    if competitor.notes:
        print(f"  ({competitor.notes})")
    print(f"{'─' * 50}")

    for scenario in scenarios:
        if (
            scenario != "cold_start"
            and scenario not in competitor.supported_ops
        ):
            print(f"  {scenario}: SKIP (not supported)")
            results.append(
                BenchmarkResult(
                    competitor=competitor.key,
                    scenario=scenario,
                    success=False,
                    error="Not supported",
                )
            )
            continue

        # Get tool call details for non-cold-start scenarios
        tool_name = None
        tool_args = None
        if scenario != "cold_start":
            tc = competitor.tool_mapping[scenario]
            tool_name = tc.name
            tool_args = tc.arguments

        print(f"  {scenario}: ", end="", flush=True)
        result = run_scenario(
            competitor_name=competitor.key,
            command=competitor.command,
            scenario=scenario,
            tool_name=tool_name,
            tool_args=tool_args,
            cwd=competitor.cwd,
            warmup=warmup,
            runs=runs,
        )
        results.append(result)

        if result.success:
            print(
                f"{result.median_ms:>8.1f}ms "
                f"(p5={result.p5_ms:.1f}, "
                f"p95={result.p95_ms:.1f})"
            )
        else:
            print(f"FAILED — {result.error}")

    return results


def collect_metadata() -> dict:
    """Collect environment metadata for reproducibility."""
    uname = platform.uname()
    meta = {
        "timestamp": datetime.now(UTC).isoformat(),
        "macos_version": platform.mac_ver()[0],
        "hardware": uname.machine,
        "python_version": platform.python_version(),
        "hostname": uname.node,
    }
    # Try to get chip info
    try:
        chip = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            text=True,
            timeout=5,
        ).strip()
        meta["cpu"] = chip
    except Exception:
        meta["cpu"] = uname.machine
    # Try to get RAM
    try:
        mem = subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"],
            text=True,
            timeout=5,
        ).strip()
        meta["ram_gb"] = round(int(mem) / (1024**3))
    except Exception:
        pass
    return meta


def print_summary(
    all_results: list[BenchmarkResult],
) -> None:
    """Print a human-readable summary table."""
    print(f"\n{'═' * 60}")
    print("  BENCHMARK RESULTS SUMMARY")
    print(f"{'═' * 60}\n")

    for scenario in SCENARIOS:
        scenario_results = [
            r for r in all_results if r.scenario == scenario and r.success
        ]
        if not scenario_results:
            continue

        print(f"  {scenario}")
        print(f"  {'─' * 45}")

        # Sort by median (fastest first)
        scenario_results.sort(key=lambda r: r.median_ms)
        fastest = scenario_results[0].median_ms

        for r in scenario_results:
            ratio = f"{r.median_ms / fastest:.1f}x" if fastest > 0 else "—"
            marker = " ◀" if r.competitor == "imdinu" else ""
            print(
                f"  {r.competitor:<25} "
                f"{r.median_ms:>8.1f}ms  "
                f"({ratio:>6}){marker}"
            )
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run apple-mail-mcp competitive benchmarks"
    )
    parser.add_argument(
        "--competitor",
        "-c",
        help="Run only this competitor (by key)",
    )
    parser.add_argument(
        "--scenario",
        "-s",
        help="Run only this scenario",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=WARMUP_RUNS,
        help=f"Warmup runs (default: {WARMUP_RUNS})",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=MEASURED_RUNS,
        help=f"Measured runs (default: {MEASURED_RUNS})",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output JSON file path (default: results/<date>.json)",
    )
    args = parser.parse_args()

    # Select competitors
    if args.competitor:
        if args.competitor not in COMPETITORS:
            print(
                f"Unknown competitor: {args.competitor}",
                file=sys.stderr,
            )
            print(
                f"Available: {', '.join(COMPETITORS)}",
                file=sys.stderr,
            )
            sys.exit(1)
        competitors = [COMPETITORS[args.competitor]]
    else:
        competitors = list(COMPETITORS.values())

    # Select scenarios
    if args.scenario:
        if args.scenario not in SCENARIOS:
            print(
                f"Unknown scenario: {args.scenario}",
                file=sys.stderr,
            )
            print(
                f"Available: {', '.join(SCENARIOS)}",
                file=sys.stderr,
            )
            sys.exit(1)
        scenarios = [args.scenario]
    else:
        scenarios = SCENARIOS

    print("Apple Mail MCP — Competitive Benchmarks")
    print(f"  Warmup: {args.warmup} | Runs: {args.runs}")
    print(f"  Competitors: {len(competitors)} | Scenarios: {len(scenarios)}")

    # Run benchmarks
    all_results: list[BenchmarkResult] = []
    for competitor in competitors:
        results = run_competitor(competitor, scenarios, args.warmup, args.runs)
        all_results.extend(results)

    # Print summary
    print_summary(all_results)

    # Save results
    metadata = collect_metadata()
    output_data = {
        "metadata": metadata,
        "config": {
            "warmup_runs": args.warmup,
            "measured_runs": args.runs,
            "scenarios": scenarios,
        },
        "results": [r.to_dict() for r in all_results],
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.output:
        output_path = Path(args.output)
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_path = RESULTS_DIR / f"{date_str}.json"

    output_path.write_text(json.dumps(output_data, indent=2) + "\n")
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
