#!/usr/bin/env bash
# Setup script for apple-mail-mcp competitive benchmarks.
# Installs all 8 competitor MCP servers into ~/.cache/apple-mail-mcp-bench/.
#
# Usage: bash benchmarks/setup.sh

set -euo pipefail

CACHE_DIR="$HOME/.cache/apple-mail-mcp-bench"
mkdir -p "$CACHE_DIR"

log() { printf "\033[1;34m==> %s\033[0m\n" "$1"; }
warn() { printf "\033[1;33m  ! %s\033[0m\n" "$1"; }
ok() { printf "\033[1;32m  ✓ %s\033[0m\n" "$1"; }
fail() { printf "\033[1;31m  ✗ %s\033[0m\n" "$1"; }

# Track results
declare -a INSTALLED=()
declare -a SKIPPED=()

install_or_skip() {
    local name="$1"
    shift
    log "Installing $name..."
    if "$@"; then
        ok "$name"
        INSTALLED+=("$name")
    else
        fail "$name (skipped)"
        SKIPPED+=("$name")
    fi
}

# ─── 1. imdinu/apple-mail-mcp (ours) ─────────────────────────
log "Checking apple-mail-mcp (ours)..."
if command -v uvx &>/dev/null; then
    ok "apple-mail-mcp (installed via uvx at runtime)"
    INSTALLED+=("imdinu/apple-mail-mcp")
else
    warn "uvx not found — install uv first: https://docs.astral.sh/uv/"
    SKIPPED+=("imdinu/apple-mail-mcp")
fi

# ─── 2. patrickfreyer/apple-mail-mcp ─────────────────────────
install_patrickfreyer() {
    local dir="$CACHE_DIR/patrickfreyer-apple-mail-mcp"
    if [ -d "$dir" ]; then
        cd "$dir" && git pull --quiet
    else
        git clone --quiet --depth 1 \
            https://github.com/patrickfreyer/apple-mail-mcp.git "$dir"
    fi
    cd "$dir"
    python3 -m venv .venv 2>/dev/null || true
    # v3.x switched from requirements.txt to a hatch-built package
    # at plugin/apple_mail_mcp; install editable so the
    # mcp-apple-mail entrypoint script lands in .venv/bin/
    .venv/bin/pip install -q -e . 2>/dev/null
}
install_or_skip "patrickfreyer/apple-mail-mcp" install_patrickfreyer

# ─── 3. s-morgan-jeffries/apple-mail-mcp (Python) ────────────
install_smorgan() {
    local dir="$CACHE_DIR/smorgan-apple-mail-mcp"
    if [ -d "$dir" ]; then
        cd "$dir" && git pull --quiet
    else
        git clone --quiet --depth 1 \
            https://github.com/s-morgan-jeffries/apple-mail-mcp.git "$dir"
    fi
    cd "$dir"
    python3 -m venv .venv 2>/dev/null || true
    .venv/bin/pip install -q -e . 2>/dev/null
}
install_or_skip "s-morgan-jeffries/apple-mail-mcp" install_smorgan

# ─── 4. like-a-freedom/rusty_apple_mail_mcp (Rust) ──────────
install_rusty() {
    if ! command -v cargo &>/dev/null; then
        warn "cargo not found — skipping rusty_apple_mail_mcp"
        return 1
    fi
    local dir="$CACHE_DIR/rusty-apple-mail-mcp"
    if [ -d "$dir" ]; then
        cd "$dir" && git pull --quiet
    else
        git clone --quiet --depth 1 \
            https://github.com/like-a-freedom/rusty_apple_mail_mcp.git "$dir"
    fi
    cd "$dir"
    cargo build --release 2>/dev/null
}
install_or_skip "rusty_apple_mail_mcp" install_rusty

# ─── 5. sweetrb/apple-mail-mcp (TypeScript, npm) ─────────────
install_sweetrb() {
    if ! command -v npm &>/dev/null; then
        warn "npm not found — skipping sweetrb"
        return 1
    fi
    local dir="$CACHE_DIR/sweetrb-apple-mail-mcp"
    if [ -d "$dir" ]; then
        cd "$dir" && git pull --quiet
    else
        git clone --quiet --depth 1 \
            https://github.com/sweetrb/apple-mail-mcp.git "$dir"
    fi
    cd "$dir"
    npm install --silent 2>/dev/null
    npm run build --silent 2>/dev/null
}
install_or_skip "sweetrb/apple-mail-mcp" install_sweetrb

# ─── 6. BastianZim/apple-mail-mcp (Python, SQLite + .emlx) ───
install_bastianzim() {
    local dir="$CACHE_DIR/bastianzim-apple-mail-mcp"
    if [ -d "$dir" ]; then
        cd "$dir" && git pull --quiet
    else
        git clone --quiet --depth 1 \
            https://github.com/BastianZim/apple-mail-mcp.git "$dir"
    fi
    cd "$dir"
    python3 -m venv .venv 2>/dev/null || true
    .venv/bin/pip install -q -e . 2>/dev/null
}
install_or_skip "BastianZim/apple-mail-mcp" install_bastianzim

# ─── 7. pl-lyfx/apple-mail-mcp (Python single-file, Envelope Index) ──
install_pl_lyfx() {
    local dir="$CACHE_DIR/pl-lyfx-apple-mail-mcp"
    if [ -d "$dir" ]; then
        cd "$dir" && git pull --quiet
    else
        git clone --quiet --depth 1 \
            https://github.com/pl-lyfx/apple-mail-mcp.git "$dir"
    fi
    chmod +x "$dir/apple_mail_mcp.py"
    warn "pl-lyfx: edit MAIL_DIR / EMAIL / MAIL_VERSION constants at the top of $dir/apple_mail_mcp.py before running benchmarks (no env-var fallback upstream yet)"
}
install_or_skip "pl-lyfx/apple-mail-mcp" install_pl_lyfx

# ─── 8. titouancreach/apple-mail-mcp (Haskell, pre-compiled binary) ──
install_titouancreach() {
    if ! command -v cabal &>/dev/null; then
        warn "cabal not found — install via ghcup: https://www.haskell.org/ghcup/"
        return 1
    fi
    local dir="$CACHE_DIR/titouancreach-apple-mail-mcp"
    if [ -d "$dir" ]; then
        cd "$dir" && git pull --quiet
    else
        git clone --quiet --depth 1 \
            https://github.com/titouancreach/apple-mail-mcp.git "$dir"
    fi
    cd "$dir"
    # Pre-compile to a binary at $CACHE_DIR/titouancreach-bin/apple-mail-mcp
    # so cold-start parity with Rust/Go entrants is fair (no cabal
    # resolution cost on every spawn).
    mkdir -p "$CACHE_DIR/titouancreach-bin"
    cabal install --overwrite-policy=always \
        --installdir="$CACHE_DIR/titouancreach-bin" \
        --install-method=copy \
        apple-mail-mcp.hs 2>&1 | tail -3
}
install_or_skip "titouancreach/apple-mail-mcp" install_titouancreach

# ─── Summary ─────────────────────────────────────────────────
echo ""
log "Setup complete"
echo "  Installed: ${#INSTALLED[@]}"
for name in "${INSTALLED[@]}"; do
    ok "$name"
done
if [ ${#SKIPPED[@]} -gt 0 ]; then
    echo "  Skipped:   ${#SKIPPED[@]}"
    for name in "${SKIPPED[@]}"; do
        fail "$name"
    done
fi
echo ""
echo "  Cache dir: $CACHE_DIR"
