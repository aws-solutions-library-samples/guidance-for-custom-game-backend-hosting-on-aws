# Lambda Layers for Game Stats & Leaderboards System

This directory contains Lambda Layers that provide shared dependencies for all Lambda functions in the system.

## Layer Structure

### valkey-glide-layer
Contains all shared dependencies including:
- **valkey-glide**: High-performance Valkey client for MemoryDB
- **aws-lambda-powertools**: Logging, tracing, and metrics
- **pydantic**: Data validation and serialization
- **asyncio-throttle**: Async rate limiting
- **nest-asyncio**: Nested async event loop support
- **python-dateutil**: Date/time utilities

## Building Layers

### Automatic Build (Recommended)
```bash
# Build all layers
./build_layer.sh
```

### Manual Build
```bash
# Create python directory
mkdir -p valkey-glide-layer/python

# Install dependencies
pip install -r valkey-glide-layer/requirements.txt \
    --target valkey-glide-layer/python \
    --platform linux_x86_64 \
    --implementation cp \
    --python-version 3.13 \
    --only-binary=:all: \
    --upgrade
```

## Deployment

Layers are automatically deployed with the CDK stack:

```bash
cdk deploy GameLeaderboardsStack
```

The CDK will:
1. Create the Lambda Layer from the `valkey-glide-layer` directory
2. Attach the layer to all Lambda functions
3. Configure proper permissions and compatibility

## Layer Benefits

- **Reduced Function Size**: Functions contain only business logic (~1MB vs ~53MB)
- **Faster Cold Starts**: Dependencies pre-loaded (1-2s vs 5-8s)
- **Efficient Deployments**: Shared dependencies updated once
- **Better Memory Usage**: Shared libraries across functions
- **Cost Optimization**: Reduced storage and transfer costs

## Troubleshooting

### Layer Too Large
If the layer exceeds 50MB (zipped), consider:
- Removing unused dependencies
- Using `--no-deps` and installing only required packages
- Splitting into multiple layers

### Import Errors
If functions can't import from the layer:
- Verify layer is attached to function
- Check Python path in layer (`python/` directory)
- Ensure compatible runtime (Python 3.13)

### Build Issues
Common solutions:
- Use `--platform linux_x86_64` for Lambda compatibility
- Use `--only-binary=:all:` to avoid compilation issues
- Clear pip cache: `pip cache purge`

## Layer Contents

After building, the layer will contain:
```
valkey-glide-layer/
├── requirements.txt
└── python/
    ├── valkey_glide/
    ├── aws_lambda_powertools/
    ├── pydantic/
    ├── pydantic_core/
    ├── asyncio_throttle/
    ├── nest_asyncio/
    ├── dateutil/
    └── ... (other dependencies)
```

## Version Management

- Layer versions are immutable once created
- CDK automatically creates new versions when layer content changes
- Functions can reference specific layer versions for stability
- Old layer versions are retained for rollback capability
