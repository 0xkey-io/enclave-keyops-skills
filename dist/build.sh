#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the keyops self-contained binary for the current platform.
#
# Usage (from repo root):
#   pip install pyinstaller
#   bash dist/build.sh
#
# Outputs (in dist/out/):
#   keyops.<platform>        e.g. keyops.darwin-arm64 or keyops.linux-amd64
#   keyops.<platform>.sha256 SHA256 sidecar (sha256sum format)
#
# This script is also called by .github/workflows/release.yml; the CI
# environment sets GITHUB_OUTPUT for the platform variable.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$REPO_ROOT/dist/out"

# ---------------------------------------------------------------------------
# Detect platform slug (matches fetch_keyops.py / fetch_qos_client.py map)
# ---------------------------------------------------------------------------
_SYSTEM="$(uname -s | tr '[:upper:]' '[:lower:]')"
_MACHINE="$(uname -m | tr '[:upper:]' '[:lower:]')"

case "${_SYSTEM}-${_MACHINE}" in
  linux-x86_64|linux-amd64)  PLATFORM="linux-amd64"  ;;
  darwin-arm64|darwin-aarch64) PLATFORM="darwin-arm64" ;;
  *)
    echo "ERROR: unsupported platform ${_SYSTEM}-${_MACHINE}" >&2
    echo "CI publishes only linux-amd64 and darwin-arm64." >&2
    exit 1
    ;;
esac

echo "Building keyops for platform: ${PLATFORM}"

# ---------------------------------------------------------------------------
# PyInstaller build
# ---------------------------------------------------------------------------
rm -rf "$REPO_ROOT/dist/build" "$OUT_DIR/keyops"
mkdir -p "$OUT_DIR"

# Run PyInstaller from the repo root so relative paths in the spec resolve.
cd "$REPO_ROOT"
pyinstaller dist/keyops.spec --distpath dist/out --workpath dist/build --noconfirm

# PyInstaller names the output after the `name=` in the spec (i.e. `keyops`).
RAW="$OUT_DIR/keyops"
if [[ ! -f "$RAW" ]]; then
  echo "ERROR: PyInstaller did not produce $RAW" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Rename and hash
# ---------------------------------------------------------------------------
DEST="$OUT_DIR/keyops.${PLATFORM}"
mv "$RAW" "$DEST"
chmod 0755 "$DEST"

# sha256sum on Linux, shasum on macOS — produce the same `<hex>  <filename>` format.
SHA256_FILE="${DEST}.sha256"
if command -v sha256sum &>/dev/null; then
  (cd "$OUT_DIR" && sha256sum "keyops.${PLATFORM}" > "keyops.${PLATFORM}.sha256")
else
  (cd "$OUT_DIR" && shasum -a 256 "keyops.${PLATFORM}" > "keyops.${PLATFORM}.sha256")
fi

echo ""
echo "Built:   $DEST"
echo "Sidecar: $SHA256_FILE"
echo "SHA256:  $(cat "$SHA256_FILE" | awk '{print $1}')"

# Export for GitHub Actions step outputs if running in CI.
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "platform=${PLATFORM}" >> "$GITHUB_OUTPUT"
  echo "binary=${DEST}" >> "$GITHUB_OUTPUT"
  echo "sidecar=${SHA256_FILE}" >> "$GITHUB_OUTPUT"
fi
