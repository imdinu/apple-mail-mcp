"""Plotly chart generation for benchmark results.

Generates uv-style horizontal bar charts (shorter = better) with:
- One chart per scenario
- Green highlight for our server, gray for competitors
- Error bars showing p5-p95 range
- Sorted by median time (fastest at top)
- Export to interactive HTML and static PNG

Usage:
    python -m benchmarks.charts                          # latest results
    python -m benchmarks.charts results/2025-01-15.json  # specific file
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import plotly.graph_objects as go  # type: ignore[import-untyped]
except ImportError:
    print(
        "plotly is required: uv run --group bench python -m benchmarks.charts",
        file=sys.stderr,
    )
    sys.exit(1)

RESULTS_DIR = Path(__file__).parent / "results"
CHARTS_DIR = Path(__file__).parent.parent  # repo root for PNGs

# Colors
COLOR_OURS = "#22c55e"  # green-500
COLOR_OTHER = "#9ca3af"  # gray-400
COLOR_FAILED = "#ef4444"  # red-500
COLOR_BG = "#ffffff"
COLOR_GRID = "#e5e7eb"

# Our server key
OUR_KEY = "imdinu"

SCENARIO_TITLES = {
    "cold_start": "Cold Start (spawn → initialize)",
    "list_accounts": "List Accounts",
    "get_emails": "Fetch 50 Emails",
    "search_subject": "Search by Subject",
    "search_body": "Search by Body (FTS5)",
}


def load_results(path: Path) -> dict:
    """Load benchmark results from JSON."""
    return json.loads(path.read_text())


def find_latest_results() -> Path:
    """Find the most recent results file."""
    json_files = sorted(RESULTS_DIR.glob("*.json"))
    if not json_files:
        print(
            "No results found. Run benchmarks first: python -m benchmarks.run",
            file=sys.stderr,
        )
        sys.exit(1)
    return json_files[-1]


def generate_chart(
    scenario: str,
    results: list[dict],
    output_dir: Path,
) -> Path | None:
    """Generate a horizontal bar chart for one scenario.

    Returns the PNG path or None if no data.
    """
    # Filter to successful results for this scenario
    scenario_data = [
        r for r in results if r["scenario"] == scenario and r["success"]
    ]
    if not scenario_data:
        return None

    # Sort by median (slowest at top → fastest at bottom)
    # so the fastest bar appears at the bottom of the chart
    scenario_data.sort(key=lambda r: r["median_ms"], reverse=True)

    names = [r["competitor"] for r in scenario_data]
    medians = [r["median_ms"] for r in scenario_data]
    p5s = [r["p5_ms"] for r in scenario_data]
    p95s = [r["p95_ms"] for r in scenario_data]

    # Error bars: asymmetric (median - p5, p95 - median)
    error_minus = [max(0, m - p5) for m, p5 in zip(medians, p5s, strict=True)]
    error_plus = [max(0, p95 - m) for m, p95 in zip(medians, p95s, strict=True)]

    # Colors: green for ours, gray for others
    colors = [COLOR_OURS if n == OUR_KEY else COLOR_OTHER for n in names]

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            y=names,
            x=medians,
            orientation="h",
            marker=dict(
                color=colors,
                line=dict(width=0),
            ),
            error_x=dict(
                type="data",
                symmetric=False,
                array=error_plus,
                arrayminus=error_minus,
                color="#6b7280",
                thickness=1.5,
                width=3,
            ),
            text=[f"{m:.0f}ms" for m in medians],
            textposition="outside",
            textfont=dict(size=12, color="#374151"),
            hovertemplate=(
                "<b>%{y}</b><br>Median: %{x:.1f}ms<br><extra></extra>"
            ),
        )
    )

    title = SCENARIO_TITLES.get(scenario, scenario)

    fig.update_layout(
        title=dict(
            text=title,
            font=dict(size=18, color="#111827"),
            x=0.0,
        ),
        xaxis=dict(
            title=dict(
                text="Time (ms) - shorter is better",
                font=dict(size=12, color="#6b7280"),
            ),
            gridcolor=COLOR_GRID,
            zeroline=True,
            zerolinecolor=COLOR_GRID,
        ),
        yaxis=dict(
            tickfont=dict(size=12),
            automargin=True,
        ),
        plot_bgcolor=COLOR_BG,
        paper_bgcolor=COLOR_BG,
        margin=dict(l=10, r=80, t=50, b=50),
        height=max(250, 60 * len(names) + 100),
        width=700,
        showlegend=False,
    )

    # Save PNG
    png_path = output_dir / f"benchmark_{scenario}.png"
    fig.write_image(str(png_path), scale=2)

    # Save interactive HTML to results/
    html_path = RESULTS_DIR / f"benchmark_{scenario}.html"
    fig.write_html(str(html_path), include_plotlyjs="cdn")

    return png_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate benchmark charts from results"
    )
    parser.add_argument(
        "results_file",
        nargs="?",
        help="Path to results JSON (default: latest)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=str(CHARTS_DIR),
        help="Directory for PNG output (default: repo root)",
    )
    args = parser.parse_args()

    if args.results_file:
        results_path = Path(args.results_file)
    else:
        results_path = find_latest_results()

    print(f"Loading results from {results_path}")
    data = load_results(results_path)
    results = data["results"]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scenarios = [
        "cold_start",
        "list_accounts",
        "get_emails",
        "search_subject",
        "search_body",
    ]

    generated = []
    for scenario in scenarios:
        png = generate_chart(scenario, results, output_dir)
        if png:
            print(f"  Generated {png.name}")
            generated.append(png)
        else:
            print(f"  Skipped {scenario} (no data)")

    print(f"\nGenerated {len(generated)} charts in {output_dir}")

    # Print metadata
    meta = data.get("metadata", {})
    if meta:
        print("\nEnvironment:")
        print(f"  macOS {meta.get('macos_version', '?')}")
        print(f"  {meta.get('cpu', '?')}")
        print(f"  Python {meta.get('python_version', '?')}")


if __name__ == "__main__":
    main()
