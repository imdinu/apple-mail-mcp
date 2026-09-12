#!/bin/bash
# Build the Claude Desktop bundle (.mcpb) into dist/.
#
# The bundle is deliberately tiny: manifest.json plus the launcher
# script from plugin/start.sh, which runs the released PyPI package.
# No source code is vendored — the bundle and `pipx install` always
# serve identical code.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${SCRIPT_DIR}/.."
BUILD_DIR="${SCRIPT_DIR}/build"
DIST_DIR="${REPO_DIR}/dist"

VERSION=$(python3 -c "import json; print(json.load(open('${SCRIPT_DIR}/manifest.json'))['version'])")
OUTPUT="${DIST_DIR}/apple-mail-mcp-${VERSION}.mcpb"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

cp "${SCRIPT_DIR}/manifest.json" "${BUILD_DIR}/"
cp "${REPO_DIR}/plugin/start.sh" "${BUILD_DIR}/"
chmod +x "${BUILD_DIR}/start.sh"

(cd "${BUILD_DIR}" && zip -q -r "${OUTPUT}" .)
rm -rf "${BUILD_DIR}"

echo "Built ${OUTPUT}"
