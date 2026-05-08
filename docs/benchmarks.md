# Benchmarks

Competitive benchmarks comparing Apple Mail MCP against 8 other Apple Mail MCP servers — inspired by [uv's BENCHMARKS.md](https://github.com/astral-sh/uv/blob/main/BENCHMARKS.md).

All benchmarks are run at the **MCP protocol level**: we spawn each server as a subprocess, connect as a JSON-RPC client over stdio, and time real tool calls. This measures what an AI assistant actually experiences.

## The Big Picture

On a real **72K-message mailbox**, only two servers complete every benchmarked operation: ours and BastianZim. The rest hit timeouts, AppleScript errors, or skip operations they don't support.

But "completes the operation" isn't the same as "covers the full mailbox." BastianZim's body search live-scans only the **5000 most recent messages** (per their README) — fast, but silent on anything older. Apple Mail MCP's FTS5 index covers the entire mailbox at every size we've tested.

![Capability Matrix](benchmark_overview.png)

### What This Means

- **Full-coverage body search is exclusive to Apple Mail MCP.** BastianZim has a body parameter but caps the scan at 5000 messages — see the "5K cap" cells in the matrix above. Every other competitor doesn't support body search at all.
- **Apple's Envelope Index is the secret sauce when you don't need body search.** BastianZim and the Rust server both read it directly, which is why their `list_accounts`, `get_emails`, and subject-search numbers are sub-10ms — they're querying an index Apple already maintained for you.
- **AppleScript-based servers struggle at this scale.** patrickfreyer, attilagyorffy, smorgan, and dhravya either timeout, hit AppleScript syntax errors on this macOS version, or simply don't implement the operation.
- **Single email fetch is a near-tie at the top.** Our disk-first `.emlx` reader hits ~3ms; BastianZim's hits ~1ms via direct envelope-index lookup; Rust hits ~4ms.

## Test Environment

| Property | Value |
|----------|-------|
| **macOS** | 26.4.1 (Tahoe) |
| **Chip** | Apple M4 Max |
| **Mailbox size** | ~72,000 messages across multiple accounts |
| **Python** | 3.12.0 |
| **Date** | 2026-05-07 |

## Competitors

| # | Project | Type | Notes |
|---|---------|------|-------|
| 1 | **[imdinu/apple-mail-mcp](https://github.com/imdinu/apple-mail-mcp)** (ours) | Python | Disk-first `.emlx` + batch JXA + FTS5 over the full mailbox |
| 2 | **[BastianZim/apple-mail-mcp](https://github.com/BastianZim/apple-mail-mcp)** | Python | Reads Apple's Envelope Index directly; live `.emlx` body scan **capped at 5000 most recent messages** |
| 3 | **[rusty_apple_mail_mcp](https://github.com/like-a-freedom/rusty_apple_mail_mcp)** | Rust | Reads Apple's Envelope Index directly; no body search |
| 4 | **[patrickfreyer/apple-mail-mcp](https://github.com/patrickfreyer/apple-mail-mcp)** | Python | AppleScript-based, 26+ tools |
| 5 | **[sweetrb/apple-mail-mcp](https://github.com/sweetrb/apple-mail-mcp)** | TypeScript | AppleScript-based, 40+ tools, mail-merge & templates |
| 6 | **[attilagyorffy/apple-mail-mcp](https://github.com/attilagyorffy/apple-mail-mcp)** | Go | AppleScript-based |
| 7 | **[s-morgan-jeffries/apple-mail-mcp](https://github.com/s-morgan-jeffries/apple-mail-mcp)** | Python | AppleScript-based |
| 8 | **[dhravya/apple-mcp](https://github.com/supermemoryai/apple-mcp)** | TypeScript | Multi-app (archived Jan 2026) |

> **Note**: `kiki830621/che-apple-mail-mcp` (Swift) is currently uncompilable on Xcode 14 SDKs (`PackageDescription` link error) and is omitted from this run.

## Detailed Results

Each scenario: **5 warmup runs + 10 measured runs**. We report the **median** with **p5/p95** error bars. A single probe call screens out tools that exceed 10 seconds, and responses are validated for correctness.

### Cold Start

Time from spawning the server process to receiving an MCP `initialize` response. Native binaries (Rust, Go) and lean Python servers (BastianZim) have a natural advantage here — no FastMCP overhead, no FTS5 schema check.

![Cold Start](benchmark_cold_start.png)

### List Accounts

Servers reading Apple's Envelope Index directly (BastianZim, rusty) finish in ~1ms — they're issuing a `SELECT DISTINCT` against an index Apple already maintained. AppleScript- and JXA-based paths pay the `osascript` round-trip, which dominates the time.

![List Accounts](benchmark_list_accounts.png)

### Fetch 50 Emails

Three servers can't complete this on a 72K mailbox: smorgan and attilagyorffy throw AppleScript errors, patrickfreyer exceeds the 10-second probe cutoff. Our JXA-based `batchFetch` is correct and reliable but ~500x slower than direct SQLite reads.

![Fetch Emails](benchmark_get_emails.png)

### Fetch Single Email

Our disk-first strategy reads `.emlx` files directly — no JXA needed. Performance is within ~2x of BastianZim's envelope-index-only metadata lookup.

![Fetch Single Email](benchmark_get_email.png)

### Search by Subject

FTS5 column filtering gives us sub-10ms subject search, competitive with the Rust server's direct SQLite queries.

![Search Subject](benchmark_search_subject.png)

### Search by Body

**This is where the project's thesis holds.** Apple Mail MCP is the only server that searches **the entire indexed mailbox** for body matches. Most competitors don't support body search at all. BastianZim does, but caps at the 5000 most recent messages — so the chart below excludes it.

> **Why BastianZim is excluded from this chart, not just labeled slow:** their median is ~3ms because the work *is* small (5000 messages instead of 72,000). The number is real but the comparison would be misleading. On the user's mailbox that's roughly 7% coverage — anything older than the most recent 5000 messages will return zero matches with no warning. Our median of ~20ms is for full-coverage FTS5 search; the comparable BastianZim scenario (uncapped body search) doesn't exist.

![Search Body](benchmark_search_body.png)

## Methodology

- **Protocol**: MCP over JSON-RPC/stdio (spawn subprocess, connect, time tool calls)
- **Warmup**: 5 runs discarded before measurement
- **Measured**: 10 runs per scenario
- **Statistic**: Median (robust to outliers)
- **Variance**: p5/p95 shown as error bars
- **Tool calls**: For non-cold-start scenarios, a single server process handles all runs
- **Probe screening**: A single probe call runs before warmup; if it exceeds 10s the scenario is skipped
- **Response validation**: Tool responses are checked for hidden errors (e.g. `{"success": false}` inside valid MCP content)

## Caveats

1. **Mailbox size matters.** Results depend on the number of emails. Our test mailbox has ~72,000 messages — AppleScript-based servers struggle at this scale.
2. **FTS5 requires one-time indexing.** Body and subject search require `apple-mail-mcp index` first. Cold start time does not include indexing.
3. **Not all servers support all operations.** The capability matrix above shows which operations each server supports.
4. **Capped competitors are flagged, not run.** BastianZim's body search is fast but covers only the 5000 most recent messages on this mailbox — we mark its `search_body` cell as "5K cap" in the matrix and omit it from the body-search bar chart entirely. Including the bar would imply apples-to-apples comparison.
5. **macOS and Mail.app versions matter.** Performance varies across OS versions; some AppleScript errors are version-specific (e.g., attilagyorffy's `date -j` flag incompatibility on macOS 26).
6. **Archived projects benchmarked as-is.** dhravya/apple-mcp is archived with known bugs.

## Reproduction

```bash
# Install competitors
bash benchmarks/setup.sh

# Run all benchmarks
uv run --group bench python -m benchmarks.run

# Generate charts
uv run --group bench python -m benchmarks.charts

# Single competitor or scenario
uv run --group bench python -m benchmarks.run --competitor imdinu
uv run --group bench python -m benchmarks.run --scenario cold_start
```

See the [benchmarks suite](https://github.com/imdinu/apple-mail-mcp/tree/main/benchmarks) in the repository for harness code and competitor configs.
