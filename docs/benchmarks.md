# Benchmarks

Competitive benchmarks comparing Apple Mail MCP against 7 other Apple Mail MCP servers — inspired by [uv's BENCHMARKS.md](https://github.com/astral-sh/uv/blob/main/BENCHMARKS.md).

All benchmarks are run at the **MCP protocol level**: we spawn each server as a subprocess, connect as a JSON-RPC client over stdio, and time real tool calls. This measures what an AI assistant actually experiences.

## The Big Picture

On a real **72K-message mailbox**, only two servers complete every benchmarked operation: ours and BastianZim. The rest hit timeouts, AppleScript errors, or skip operations they don't support.

But "completes the operation" isn't the same as "covers the full mailbox." BastianZim's body search live-scans only the **5000 most recent messages** (per their README) — fast, but silent on anything older. Apple Mail MCP's FTS5 index covers the entire mailbox at every size we've tested.

![Capability Matrix](benchmark_overview.png)

> **Chart refresh pending.** The charts on this page were generated 2026-05-07 against the prior competitor slate. The current slate (Competitors table below) adds `pl-lyfx` and `titouancreach`, and drops `dhravya` (archived), `attilagyorffy` (stale), and `kiki830621` (uncompilable). A refreshed sweep will appear in the next release.

### What This Means

- **Full-coverage body search is exclusive to Apple Mail MCP.** BastianZim has a body parameter but caps the scan at 5000 messages — see the "5K cap" cells in the matrix above. Every other competitor either doesn't support body search at all or scans only a partial set.
- **Apple's Envelope Index is the secret sauce when you don't need body search.** BastianZim, rusty, and pl-lyfx all read it directly, which is why their `list_accounts` and subject-search numbers cluster at the top — they're querying an index Apple already maintained.
- **AppleScript-based servers struggle at this scale.** patrickfreyer, sweetrb, titouancreach, and smorgan all wrap `osascript` for live Mail.app queries — fine at small scale, slow or unreliable on 72K-message mailboxes.
- **Single email fetch is a near-tie at the top.** Our disk-first `.emlx` reader hits ~3ms; BastianZim's hits ~1ms via direct envelope-index lookup; Rust hits ~4ms.

## Test Environment

| Property | Value |
|----------|-------|
| **macOS** | 26.4.1 (Tahoe) |
| **Chip** | Apple M4 Max |
| **Mailbox size** | ~72,000 messages across multiple accounts |
| **Python** | 3.12.0 |
| **Date** | 2026-05-07 (last chart sweep — refresh pending) |

## Competitors

| # | Project | Type | Notes |
|---|---------|------|-------|
| 1 | **[imdinu/apple-mail-mcp](https://github.com/imdinu/apple-mail-mcp)** (ours) | Python | Disk-first `.emlx` + batch JXA + FTS5 over the full mailbox |
| 2 | **[BastianZim/apple-mail-mcp](https://github.com/BastianZim/apple-mail-mcp)** | Python | Reads Apple's Envelope Index directly; live `.emlx` body scan **capped at 5000 most recent messages** |
| 3 | **[rusty_apple_mail_mcp](https://github.com/like-a-freedom/rusty_apple_mail_mcp)** | Rust | Reads Apple's Envelope Index directly; no body search |
| 4 | **[pl-lyfx/apple-mail-mcp](https://github.com/pl-lyfx/apple-mail-mcp)** | Python | Single-file, reads Apple's Envelope Index directly; no `get_emails`-list or `get_email`-by-id surface |
| 5 | **[patrickfreyer/apple-mail-mcp](https://github.com/patrickfreyer/apple-mail-mcp)** | Python | AppleScript-based, 26+ tools |
| 6 | **[sweetrb/apple-mail-mcp](https://github.com/sweetrb/apple-mail-mcp)** | TypeScript | AppleScript-based, 40+ tools, mail-merge & templates |
| 7 | **[titouancreach/apple-mail-mcp](https://github.com/titouancreach/apple-mail-mcp)** | Haskell | Single-file cabal script, AppleScript-backed; only Haskell entry in the ecosystem |
| 8 | **[s-morgan-jeffries/apple-mail-mcp](https://github.com/s-morgan-jeffries/apple-mail-mcp)** | Python | AppleScript-based |

## Also noted

These projects exist in the ecosystem but aren't benchmarked above. Listed for completeness — not for direct comparison.

**Demoted from prior runs**

- **[supermemoryai/apple-mcp](https://github.com/supermemoryai/apple-mcp)** (dhravya) — Archived January 2026; historical baseline only.
- **[attilagyorffy/apple-mail-mcp](https://github.com/attilagyorffy/apple-mail-mcp)** — Go, 0⭐, last push March 2026; supplanted by other Go entrants and fails AppleScript probes on macOS 26+.
- **[kiki830621/che-apple-mail-mcp](https://github.com/kiki830621/che-apple-mail-mcp)** — Swift, currently uncompilable on Xcode 14 SDKs (`PackageDescription` link error).
- **[fatbobman/mail-mcp-bridge](https://github.com/fatbobman/mail-mcp-bridge)** — Python, "bridge" model that expects a user-supplied Message-ID copied from inside Mail.app; no list or search surface, not a benchmark peer.

**New entrants (Feb–May 2026, not yet benchmarked)**

`Clarus-Moof/AppleMailMCP`, `ANemcov/apple-mailapp-mcp`, `maximbilan/apple-mail-mcp-go`, `dastrobu/apple-mail-mcp`, `jayvee6/apple-mail-mcp`, `Agentic-Assets/apple-mail-mcp` — all 0–1⭐ single-author exploratory projects without distinct architectural claims. Watch this space.

## Detailed Results

Each scenario: **5 warmup runs + 10 measured runs**. We report the **median** with **p5/p95** error bars. A single probe call screens out tools that exceed 10 seconds, and responses are validated for correctness.

### Cold Start

Time from spawning the server process to receiving an MCP `initialize` response. Native binaries (Rust, Go, the pre-compiled Haskell entrant) and lean Python servers (BastianZim, pl-lyfx) have a natural advantage here — no FastMCP overhead, no FTS5 schema check.

![Cold Start](benchmark_cold_start.png)

### List Accounts

Servers reading Apple's Envelope Index directly (BastianZim, rusty, pl-lyfx) finish in ~1ms — they're issuing a `SELECT DISTINCT` against an index Apple already maintained. AppleScript- and JXA-based paths pay the `osascript` round-trip, which dominates the time.

![List Accounts](benchmark_list_accounts.png)

### Fetch 50 Emails

Several AppleScript-based servers can't complete this on a 72K mailbox: smorgan throws AppleScript errors, patrickfreyer exceeds the 10-second probe cutoff. pl-lyfx has no list-emails surface and is omitted from this chart. Our JXA-based `batchFetch` is correct and reliable but ~500x slower than direct SQLite reads.

![Fetch Emails](benchmark_get_emails.png)

### Fetch Single Email

Our disk-first strategy reads `.emlx` files directly — no JXA needed. Performance is within ~2x of BastianZim's envelope-index-only metadata lookup. pl-lyfx has no `get_email`-by-id surface and is omitted from this chart.

![Fetch Single Email](benchmark_get_email.png)

### Search by Subject

FTS5 column filtering gives us sub-10ms subject search, competitive with the Rust server's direct SQLite queries and pl-lyfx's direct Envelope-Index reads.

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
5. **macOS and Mail.app versions matter.** Performance varies across OS versions; some AppleScript errors are version-specific.
6. **pl-lyfx requires manual configuration.** The upstream script hardcodes `MAIL_DIR`, `EMAIL`, and `MAIL_VERSION` constants at the top of `apple_mail_mcp.py` — edit before running the benchmarks.
7. **titouancreach requires GHC + cabal.** The Haskell toolchain (~2 GB via [ghcup](https://www.haskell.org/ghcup/)) is needed to build the binary. The benchmark pre-compiles via `cabal install` for cold-start parity with Rust/Go entrants.

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
