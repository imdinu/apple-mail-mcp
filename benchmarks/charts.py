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
    "get_email": "Fetch Single Email",
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
    # Filter to successful results for this scenario, dropping any
    # competitor explicitly excluded for this scenario
    excluded = BAR_CHART_EXCLUDE.get(scenario, set())
    scenario_data = [
        r
        for r in results
        if r["scenario"] == scenario
        and r["success"]
        and r["competitor"] not in excluded
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


# ─── Overview (capability matrix) ────────────────────────────

# Friendly display names for the overview chart
COMPETITOR_LABELS = {
    "imdinu": "apple-mail-mcp (ours)",
    "bastianzim": "BastianZim",
    "rusty": "rusty (Rust)",
    "patrickfreyer": "patrickfreyer",
    "sweetrb": "sweetrb",
    "dhravya": "dhravya (archived)",
    "smorgan": "s-morgan-jeffries",
    "attilagyorffy": "attilagyorffy (Go)",
    "che-apple-mail": "che (Swift)",
}

# Display order: us first, then by interest
COMPETITOR_ORDER = [
    "imdinu",
    "bastianzim",
    "rusty",
    "patrickfreyer",
    "sweetrb",
    "attilagyorffy",
    "smorgan",
    "dhravya",
]

# Per-scenario overrides: classify a (competitor, scenario) cell with a
# code+label that doesn't match the raw bench result. Used when a
# competitor technically returned a number but the comparison would be
# misleading.
#
# BastianZim's search_body live-scans only the 5000 most recent
# messages (per their README). On a 72K mailbox that's ~7% coverage —
# fast but incomplete. Showing their median ms next to ours would
# imply apples-to-apples; it isn't.
SCENARIO_OVERRIDES: dict[tuple[str, str], tuple[int, str]] = {
    ("bastianzim", "search_body"): (2, "5K cap"),
}

# Per-scenario competitor exclusions for the bar charts (per-scenario
# views). The matrix still shows the override label; the bar chart
# omits the bar entirely so the visual comparison stays honest.
BAR_CHART_EXCLUDE: dict[str, set[str]] = {
    "search_body": {"bastianzim"},
}

SCENARIO_SHORT = {
    "cold_start": "Cold Start",
    "list_accounts": "List\nAccounts",
    "get_emails": "Fetch 50\nEmails",
    "get_email": "Fetch\nSingle Email",
    "search_subject": "Search\nSubject",
    "search_body": "Search\nBody",
}

# Color codes: 0 = success, 1 = timeout/error, 2 = not supported
COLOR_MAP = {0: "#22c55e", 1: "#ef4444", 2: "#d1d5db"}


def _classify_result(
    results: list[dict], competitor: str, scenario: str
) -> tuple[int, str]:
    """Classify a benchmark result as (code, label).

    Returns (0, "Xms"), (1, "TIMEOUT"), (1, "ERROR"), or (2, "—").
    Per-scenario overrides (`SCENARIO_OVERRIDES`) take precedence over
    the raw bench result — used when the result number would be
    misleading without context (e.g. capped scans).
    """
    override = SCENARIO_OVERRIDES.get((competitor, scenario))
    if override is not None:
        return override

    matches = [
        r
        for r in results
        if r["competitor"] == competitor and r["scenario"] == scenario
    ]
    if not matches:
        return 2, "—"
    r = matches[0]
    if r["success"]:
        return 0, f"{r['median_ms']:.0f}ms"
    err = r.get("error", "")
    if "Not supported" in err:
        return 2, "—"
    if "No such file" in err:
        return 2, "N/A"
    if "timeout" in err.lower() or "too slow" in err.lower():
        return 1, "TIMEOUT"
    return 1, "ERROR"


def generate_overview_chart(
    results: list[dict],
    output_dir: Path,
) -> Path:
    """Generate the capability matrix overview chart."""
    scenarios = list(SCENARIO_SHORT.keys())
    # Filter to competitors present in results
    present = {r["competitor"] for r in results}
    competitors = [c for c in COMPETITOR_ORDER if c in present]

    # Build grid (rows = competitors, cols = scenarios)
    # Reverse so "ours" appears at top in the chart
    z_values: list[list[int]] = []
    annotations: list[list[str]] = []
    for comp in reversed(competitors):
        row_z = []
        row_a = []
        for sc in scenarios:
            code, label = _classify_result(results, comp, sc)
            row_z.append(code)
            row_a.append(label)
        z_values.append(row_z)
        annotations.append(row_a)

    y_labels = [
        COMPETITOR_LABELS.get(c, c) for c in reversed(competitors)
    ]
    x_labels = [SCENARIO_SHORT[s] for s in scenarios]

    # Custom colorscale: 0=green, 0.5=red, 1=gray
    colorscale = [
        [0.0, COLOR_MAP[0]],
        [0.33, COLOR_MAP[0]],
        [0.34, COLOR_MAP[1]],
        [0.66, COLOR_MAP[1]],
        [0.67, COLOR_MAP[2]],
        [1.0, COLOR_MAP[2]],
    ]

    fig = go.Figure(
        data=go.Heatmap(
            z=z_values,
            x=x_labels,
            y=y_labels,
            colorscale=colorscale,
            showscale=False,
            zmin=0,
            zmax=2,
            xgap=3,
            ygap=3,
        )
    )

    # Add text annotations
    for i, row in enumerate(annotations):
        for j, text in enumerate(row):
            font_color = "#ffffff" if z_values[i][j] < 2 else "#6b7280"
            fig.add_annotation(
                x=x_labels[j],
                y=y_labels[i],
                text=f"<b>{text}</b>",
                showarrow=False,
                font=dict(size=13, color=font_color),
            )

    fig.update_layout(
        title=dict(
            text=(
                "Apple Mail MCP Servers — Capability Matrix (72K mailbox)"
                "<br><sup style='color:#6b7280'>"
                "“5K cap” = competitor only scans the 5000 most recent "
                "messages (silent miss on older mail)."
                "</sup>"
            ),
            font=dict(size=16, color="#111827"),
            x=0.0,
        ),
        xaxis=dict(
            side="top",
            tickfont=dict(size=11),
        ),
        yaxis=dict(
            tickfont=dict(size=12),
            automargin=True,
        ),
        plot_bgcolor=COLOR_BG,
        paper_bgcolor=COLOR_BG,
        margin=dict(l=10, r=20, t=80, b=30),
        height=max(300, 50 * len(competitors) + 120),
        width=750,
    )

    png_path = output_dir / "benchmark_overview.png"
    fig.write_image(str(png_path), scale=2)

    html_path = RESULTS_DIR / "benchmark_overview.html"
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
        "get_email",
        "search_subject",
        "search_body",
    ]

    generated = []

    # Overview chart (capability matrix)
    overview = generate_overview_chart(results, output_dir)
    print(f"  Generated {overview.name}")
    generated.append(overview)

    # Per-scenario charts
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
