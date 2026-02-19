#!/usr/bin/env bash
# Setup script for apple-mail-mcp competitive benchmarks.
# Installs all competitor MCP servers into ~/.cache/apple-mail-mcp-bench/.
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
    .venv/bin/pip install -q -r requirements.txt 2>/dev/null
}
install_or_skip "patrickfreyer/apple-mail-mcp" install_patrickfreyer

# ─── 3. kiki830621/che-apple-mail-mcp (Swift) ────────────────
install_che_apple_mail() {
    local dir="$CACHE_DIR/che-apple-mail-mcp"
    if [ -d "$dir" ]; then
        cd "$dir" && git pull --quiet
    else
        git clone --quiet --depth 1 \
            https://github.com/kiki830621/che-apple-mail-mcp.git "$dir"
    fi
    cd "$dir"
    swift build -c release 2>/dev/null
}
install_or_skip "kiki830621/che-apple-mail-mcp" install_che_apple_mail

# ─── 4. fatbobman/mail-mcp-bridge ────────────────────────────
install_fatbobman() {
    local dir="$CACHE_DIR/mail-mcp-bridge"
    if [ -d "$dir" ]; then
        cd "$dir" && git pull --quiet
    else
        git clone --quiet --depth 1 \
            https://github.com/fatbobman/mail-mcp-bridge.git "$dir"
    fi
    cd "$dir"
    python3 -m venv .venv 2>/dev/null || true
    .venv/bin/pip install -q mcp 2>/dev/null
}
install_or_skip "fatbobman/mail-mcp-bridge" install_fatbobman

# ─── 5. supermemoryai/apple-mcp (dhravya, archived) ──────────
install_dhravya() {
    if ! command -v bunx &>/dev/null && ! command -v npx &>/dev/null; then
        warn "Neither bunx nor npx found — skipping dhravya"
        return 1
    fi
    # Verify the package exists (dry run)
    if command -v bunx &>/dev/null; then
        ok "dhravya/apple-mcp (will use bunx at runtime)"
    else
        ok "dhravya/apple-mcp (will use npx at runtime)"
    fi
}
install_or_skip "supermemoryai/apple-mcp" install_dhravya

# ─── 6. steipete/macos-automator-mcp ─────────────────────────
install_steipete() {
    if ! command -v npx &>/dev/null; then
        warn "npx not found — skipping steipete"
        return 1
    fi
    ok "steipete/macos-automator-mcp (will use npx at runtime)"
}
install_or_skip "steipete/macos-automator-mcp" install_steipete

# ─── 7. PeakMojo/applescript-mcp ─────────────────────────────
install_peakmojo() {
    if ! command -v npx &>/dev/null; then
        warn "npx not found — skipping PeakMojo"
        return 1
    fi
    ok "PeakMojo/applescript-mcp (will use npx at runtime)"
}
install_or_skip "PeakMojo/applescript-mcp" install_peakmojo

# ─── 8. 54yyyu/pyapple-mcp ───────────────────────────────────
install_pyapple() {
    local dir="$CACHE_DIR/pyapple-mcp"
    python3 -m venv "$dir/.venv" 2>/dev/null || true
    "$dir/.venv/bin/pip" install -q pyapple-mcp 2>/dev/null
}
install_or_skip "54yyyu/pyapple-mcp" install_pyapple

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
