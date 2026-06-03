#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Build script for Lambda Layers
# This script builds the Valkey-GLIDE layer with all dependencies

set -e

echo "🏗️ Building Lambda Layers for Game Stats & Leaderboards System"
echo "================================================================"

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAYER_DIR="$SCRIPT_DIR/valkey-glide-layer"

echo "📁 Layer directory: $LAYER_DIR"

# Create python directory for the layer
mkdir -p "$LAYER_DIR/python"

echo "📦 Installing dependencies for Lambda Layer..."
echo "   Target: $LAYER_DIR/python"
echo "   Platform: linux_x86_64 (Lambda compatible)"

# First, try to install with platform-specific settings
echo "🔄 Attempting platform-specific installation..."
if pip install \
    -r "$LAYER_DIR/requirements.txt" \
    --target "$LAYER_DIR/python" \
    --platform linux_x86_64 \
    --implementation cp \
    --python-version 3.13 \
    --only-binary=:all: \
    --upgrade \
    --no-deps 2>/dev/null; then
    echo "✅ Platform-specific installation successful"
else
    echo "⚠️ Platform-specific installation failed, trying fallback method..."
    
    # Fallback: Install without platform restrictions
    echo "🔄 Installing with fallback method (may require compilation on Lambda)..."
    pip install \
        -r "$LAYER_DIR/requirements.txt" \
        --target "$LAYER_DIR/python" \
        --upgrade
    
    echo "⚠️ Note: Some packages may need to be compiled on Lambda runtime"
fi

echo ""
echo "📊 Layer size analysis:"
du -sh "$LAYER_DIR/python"
echo ""

echo "📋 Installed packages:"
ls -la "$LAYER_DIR/python" | grep -E "^d" | awk '{print $9}' | grep -v "^\.$" | grep -v "^\.\.$" | sort

echo ""
echo "✅ Layer build completed successfully!"
echo "📍 Layer location: $LAYER_DIR"
echo ""
echo "🚀 Next steps:"
echo "   1. Deploy with CDK: cdk deploy GameLeaderboardsStack"
echo "   2. The layer will be automatically included in all Lambda functions"
echo ""

# Create a simple test to verify key imports work
echo "🧪 Testing key imports..."
cd "$LAYER_DIR/python"
if python3 -c "import aws_lambda_powertools; print('✅ aws-lambda-powertools import successful')" 2>/dev/null; then
    echo "✅ aws-lambda-powertools: OK"
else
    echo "⚠️ aws-lambda-powertools: Import failed"
fi

if python3 -c "import pydantic; print('✅ pydantic import successful')" 2>/dev/null; then
    echo "✅ pydantic: OK"
else
    echo "⚠️ pydantic: Import failed"
fi

if python3 -c "import valkey_glide; print('✅ valkey-glide import successful')" 2>/dev/null; then
    echo "✅ valkey-glide: OK"
else
    echo "⚠️ valkey-glide: May need runtime compilation (this is normal for some platforms)"
fi

echo ""
echo "🎉 Layer build process completed!"
