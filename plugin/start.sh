#!/bin/bash
# Launcher for apple-mail-mcp. Runs the released PyPI package via the
# best tool available on this machine — nothing is vendored here, so
# the plugin always serves the same code as `pipx install`.
#
# Resolution order: uvx → pipx → private venv (created on first run).
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[apple-mail-mcp] $1" >&2; }

# MCP hosts (Claude Desktop especially) launch servers with a minimal
# PATH that omits Homebrew and ~/.local/bin, where uvx/pipx live.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if command -v uvx >/dev/null 2>&1; then
    exec uvx apple-mail-mcp serve --watch
fi

if command -v pipx >/dev/null 2>&1; then
    exec pipx run apple-mail-mcp serve --watch
fi

# No uv or pipx: bootstrap a private venv next to this script.
VENV_DIR="${SCRIPT_DIR}/venv"
if [ ! -x "${VENV_DIR}/bin/apple-mail-mcp" ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        log "ERROR: none of uvx, pipx, or python3 found."
        log "Install uv (recommended): https://docs.astral.sh/uv/"
        exit 1
    fi
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        log "ERROR: python3 is older than 3.11 and uvx/pipx are not installed."
        log "Install uv (recommended): https://docs.astral.sh/uv/"
        exit 1
    fi
    log "First run: creating a private environment (one-time setup)..."
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
    "${VENV_DIR}/bin/pip" install --quiet apple-mail-mcp
    log "Setup complete."
fi

exec "${VENV_DIR}/bin/apple-mail-mcp" serve --watch
