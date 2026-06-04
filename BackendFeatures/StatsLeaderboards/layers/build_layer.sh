#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# Build script for the Valkey-GLIDE Lambda layer.
#
# Cross-platform by design: this downloads prebuilt Linux wheels for the Lambda
# runtime (it never compiles), so it produces an identical, correct layer whether
# run on macOS (x86_64 or Apple Silicon), Linux, or Windows (Git Bash / WSL). The
# build host's OS and CPU do not affect the output.
#
# Why this matters: valkey-glide (imported as `glide`) and pydantic-core ship
# compiled native extensions (.so). If the layer is built for the host instead of
# the Lambda platform, the import fails on Lambda and every leaderboard endpoint
# returns HTTP 503 "Valkey dependency not found". This script therefore pins the
# Lambda platform/ABI and refuses to fall back to a host-native install.
#
# Configurable via environment variables (defaults match app.py / app_post_deploy.py):
#   LAMBDA_PY_VERSION   Python runtime version       (default: 3.13)
#   LAMBDA_ARCH         Target architecture           (default: x86_64; or arm64/aarch64)
#   PYTHON_BIN          Python interpreter to drive pip (default: autodetected)

set -euo pipefail

echo "🏗️  Building Valkey-GLIDE Lambda layer"
echo "================================================================"

# --- Resolve paths ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAYER_DIR="$SCRIPT_DIR/valkey-glide-layer"
LAYER_PY="$LAYER_DIR/python"
REQUIREMENTS="$LAYER_DIR/requirements.txt"

if [ ! -f "$REQUIREMENTS" ]; then
    echo "❌ requirements file not found: $REQUIREMENTS"
    exit 1
fi

echo "📁 Layer directory: $LAYER_DIR"

# --- Configuration (env-overridable; defaults track the CDK definition) -----
PY_VERSION="${LAMBDA_PY_VERSION:-3.13}"
ARCH="${LAMBDA_ARCH:-x86_64}"

# Map architecture -> (manylinux platform tag, expected .so substring)
case "$ARCH" in
    x86_64|amd64)
        PLATFORM_TAG="manylinux2014_x86_64"
        SO_MATCH="x86_64-linux-gnu"
        ;;
    arm64|aarch64)
        PLATFORM_TAG="manylinux2014_aarch64"
        SO_MATCH="aarch64-linux-gnu"
        ;;
    *)
        echo "❌ Unsupported LAMBDA_ARCH='$ARCH' (use x86_64 or arm64)"
        exit 1
        ;;
esac

# cpXY ABI tag, e.g. 3.13 -> cp313
ABI_TAG="cp$(echo "$PY_VERSION" | tr -d '.')"

# --- Resolve a Python interpreter and use `python -m pip` -------------------
# `python -m pip` is more reliable than a bare `pip` shim across OSes/venvs.
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then
            PYTHON_BIN="$cand"
            break
        fi
    done
fi
if [ -z "$PYTHON_BIN" ] || ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "❌ No Python interpreter found. Install Python 3, or set PYTHON_BIN."
    exit 1
fi

if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
    echo "❌ pip is not available for '$PYTHON_BIN'. Install pip (python -m ensurepip --upgrade)."
    exit 1
fi

# --platform + --only-binary cross-compilation needs a reasonably recent pip (>=20).
PIP_MAJOR="$("$PYTHON_BIN" -m pip --version 2>/dev/null | sed -E 's/^pip ([0-9]+).*/\1/')"
if [ -z "$PIP_MAJOR" ] || [ "$PIP_MAJOR" -lt 20 ] 2>/dev/null; then
    echo "⚠️  pip looks old ($("$PYTHON_BIN" -m pip --version 2>/dev/null)); upgrading for --platform support..."
    "$PYTHON_BIN" -m pip install --upgrade pip >/dev/null 2>&1 || {
        echo "❌ Could not upgrade pip. Upgrade manually: $PYTHON_BIN -m pip install --upgrade pip"
        exit 1
    }
fi

echo "🐍 Interpreter:   $("$PYTHON_BIN" --version 2>&1) ($PYTHON_BIN)"
echo "🧰 pip:           $("$PYTHON_BIN" -m pip --version 2>&1 | sed -E 's/ \(.*//')"
echo "🎯 Target:        Python $PY_VERSION ($ABI_TAG), $ARCH"
echo "📦 Platform tag:  $PLATFORM_TAG"

# --- Clean target so a prior (possibly host-native) build cannot leak in ----
rm -rf "$LAYER_PY"
mkdir -p "$LAYER_PY"

# --- Install Lambda-platform wheels -----------------------------------------
# Flags:
#   --platform / --python-version / --implementation / --abi : resolve wheels for
#       the Lambda runtime regardless of the build host.
#   --only-binary=:all: : never build from source on the host (no host toolchain
#       leaks into the layer; fails clearly if a required wheel is unavailable).
#   deps ARE installed (no --no-deps): glide pulls cffi/protobuf/cryptography,
#       which also have native wheels and must be the Lambda build.
# No fallback: a host-native install would import-fail on Lambda (runtime 503).
echo ""
echo "📥 Installing dependencies (prebuilt Lambda wheels only)..."
if ! "$PYTHON_BIN" -m pip install \
    -r "$REQUIREMENTS" \
    --target "$LAYER_PY" \
    --platform "$PLATFORM_TAG" \
    --implementation cp \
    --python-version "$PY_VERSION" \
    --abi "$ABI_TAG" \
    --only-binary=:all: \
    --upgrade; then
    echo ""
    echo "❌ Layer install failed for $PLATFORM_TAG / $ABI_TAG."
    echo "   A required package likely does not publish a $ARCH $ABI_TAG wheel."
    echo "   Check the versions in $REQUIREMENTS against the target Python/arch."
    echo "   Do NOT fall back to a host-native install — the resulting layer would"
    echo "   import-fail on Lambda (runtime HTTP 503: 'Valkey dependency not found')."
    exit 1
fi
echo "✅ Dependencies installed for the Lambda platform"

# --- Report ------------------------------------------------------------------
echo ""
echo "📊 Layer size (unzipped; Lambda limit is 250 MB unzipped across all layers):"
du -sh "$LAYER_PY"

echo ""
echo "📋 Top-level packages:"
# Portable listing (avoids GNU-specific ls/awk parsing).
( cd "$LAYER_PY" && find . -maxdepth 1 -mindepth 1 -type d \
    ! -name '__pycache__' -exec basename {} \; | sort )

# --- Verify layer contents ---------------------------------------------------
# These are Linux wheels, so the host (possibly macOS/arm64) generally cannot
# import the native extensions. The meaningful build-time check is that the
# native packages are present and built for the TARGET platform. Pure-Python
# packages are import-checked when the host can.
echo ""
echo "🧪 Verifying layer contents..."

verify_native() {
    # $1 = package dir name, $2 = human label
    local pkg="$1" label="$2" so
    if [ ! -d "$LAYER_PY/$pkg" ]; then
        echo "❌ $label: package '$pkg' missing from layer"
        return 1
    fi
    so="$(find "$LAYER_PY/$pkg" -maxdepth 1 -name '*.so' | head -1)"
    if [ -z "$so" ]; then
        echo "❌ $label: native extension (.so) missing from '$pkg'"
        return 1
    fi
    if basename "$so" | grep -q "$SO_MATCH"; then
        echo "✅ $label: native extension built for $ARCH linux"
        echo "   $(basename "$so")"
    else
        echo "❌ $label: native extension is not $SO_MATCH (wrong platform — will fail on Lambda)"
        echo "   $(basename "$so")"
        return 1
    fi
}

# valkey-glide imports as the 'glide' package.
verify_native glide "valkey-glide (glide)"
# pydantic v2 ships the native pydantic_core extension.
verify_native pydantic_core "pydantic_core"

# Pure-Python: import-check only when the host Python matches closely enough.
if "$PYTHON_BIN" -c "import sys; sys.path.insert(0, '$LAYER_PY'); import aws_lambda_powertools" 2>/dev/null; then
    echo "✅ aws-lambda-powertools: present and importable on this host"
else
    echo "ℹ️  aws-lambda-powertools: present (import not verified on this host — normal cross-platform)"
fi

echo ""
echo "🎉 Layer build completed: $LAYER_DIR"
echo "   Next: deploy with CDK (cdk deploy GameStatsLeaderboardsStack)."
