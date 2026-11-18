#!/usr/bin/env bash
# Builds build/lambda.zip with Linux-compatible dependencies for AWS Lambda.
# Usage: PYTHON_VERSION=3.13 LAMBDA_ARCH=x86_64 scripts/build_lambda.sh
set -euo pipefail

PYTHON_VERSION="${PYTHON_VERSION:-3.13}"
LAMBDA_ARCH="${LAMBDA_ARCH:-x86_64}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

case "$LAMBDA_ARCH" in
  x86_64) PLATFORM="manylinux2014_x86_64" ;;
  arm64) PLATFORM="manylinux2014_aarch64" ;;
  *) echo "LAMBDA_ARCH must be x86_64 or arm64" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE_DIR="$ROOT/build/lambda_package"
ZIP_PATH="$ROOT/build/lambda.zip"

rm -rf "$PACKAGE_DIR" "$ZIP_PATH"
mkdir -p "$PACKAGE_DIR"

"$PYTHON_BIN" -m pip install --quiet --requirement "$ROOT/requirements.txt" --target "$PACKAGE_DIR" \
  --platform "$PLATFORM" --implementation cp --python-version "$PYTHON_VERSION" --only-binary=:all:

cp "$ROOT/main.py" "$PACKAGE_DIR/"
mkdir -p "$PACKAGE_DIR/src"
cp "$ROOT"/src/*.py "$PACKAGE_DIR/src/"
find "$PACKAGE_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} +

(cd "$PACKAGE_DIR" && zip -qr "$ZIP_PATH" .)
echo "Built $ZIP_PATH ($(du -h "$ZIP_PATH" | cut -f1))"
