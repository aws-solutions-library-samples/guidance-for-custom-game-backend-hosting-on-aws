# Game Stats and Leaderboards System - Developer Documentation

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Deployment Guide](#3-deployment-guide)
4. [Integration](#4-integration)
    - [Integration Guide — Who Calls What](#integration-guide--who-calls-what)
    - [Before You Go to Production — Integration Points](#before-you-go-to-production--integration-points)
    - [API Reference](#api-reference) ([full documentation](docs/api_reference.md))
5. [Appendices](#appendices)
    - [Appendix A: Introduction to Leaderboards and Game Statistics](#appendix-a-introduction-to-leaderboards-and-game-statistics)
    - [Appendix B: Load Testing Framework](#appendix-b-load-testing-framework)

> This project has been tested across all supported score types, leaderboard strategies, time formats, and common edge cases using the included integration test suite (`testing/test_StatsAndLeaderboards.py`) and load/stress tested using the distributed test runner (`testing/load-stress-testing/test_LoadAndStressTests.py`). That said, please perform your own validation and testing against your specific game's data patterns and traffic profiles before deploying to production.

---

## 1. System Overview

The Game Stats and Leaderboards system is a backend built on AWS managed services for managing game statistics and real-time leaderboards. Lambda, API Gateway, and DynamoDB run on-demand; MemoryDB for Valkey runs as a provisioned cluster. For readers new to game leaderboards and statistics, see [Appendix A](#appendix-a-introduction-to-leaderboards-and-game-statistics).

> **Single-Tenant Design:** This system is designed for a single game per deployment. If you need to support multiple games, deploy a separate instance for each game (see [Deployment Guide, Step 3](#32-deployment-from-ec2-instance-recommended)).

### Key Capabilities

- **Real-time leaderboards** with sorted sets
- **Player statistics storage** with full game report history
- **Multiple leaderboard strategies**: best, cumulative, replace
- **Multiple score types**: score, time, distance, points, rank, level (ascending and descending)
- **Event leaderboards** with expiry and read-only/auto-delete modes
- **Batch submission and processing** for multiplayer game server reports
- **Leaderboard reset and rebuild** with backup/restore
- **Unit testing and distributed load testing** framework included

### Technology Stack

| Component | Technology |
|-----------|-----------|
| Compute | AWS Lambda (Python 3.13+) |
| Leaderboard Store | Amazon MemoryDB for Valkey (Valkey-GLIDE 2.0.1) |
| Stats Store | Amazon DynamoDB (on-demand) |
| API Layer | Amazon API Gateway (REST) |
| Auth | Lambda Authorizers (backend + player) + SSM Parameter Store |
| IaC | AWS CDK (Python) |
| Monitoring | CloudWatch + Lambda Powertools |
| Long-Running Operations | Lambda self-invoke relay |

### Lambda Functions (11 total)

| Function | Directory | Purpose |
|----------|-----------|---------|
| `backendAuthorizer` | `/auth` | Backend API key authentication |
| `playerAuthorizer` | `/auth` | Player authentication (INTEGRATION POINT) |
| `developerRegistration` | `/backend` | Studio/game registration, API key management |
| `leaderboardsConfig` | `/backend` | Leaderboard CRUD configuration |
| `batchStoreStatsAndScores` | `/backend` | Batch game report processing |
| `resetLeaderboard` | `/backend` | Reset leaderboard scores |
| `rebuildLeaderboard` | `/backend` | Rebuild leaderboard from DynamoDB |
| `storePlayerStatsAndScores` | `/player` | Individual player game report |
| `getLeaderboardScores` | `/player` | Query leaderboard scores |
| `getPlayerStatsAndScores` | `/player` | Query player stats history |
| `getPlayerLBStanding` | `/player` | Player rank, percentile, neighbours |

---

## 2. Architecture

![High-Level Architecture](Guidance%20for%20Game%20Stats%20and%20Leaderboards.png)

Game backends and player clients send requests through Amazon API Gateway, which routes them to one of two Lambda authorizers: the backend authorizer (validates StudioAPI keys via SSM Parameter Store) for developer/admin operations, or the player authorizer (integration point for your game's player authentication) for player-facing operations. Authorized requests reach the target Lambda function, which reads/writes leaderboard scores in MemoryDB for Valkey (sorted sets) and persists player stats and configurations in DynamoDB. The 8 Lambda functions that connect to MemoryDB run within VPC private subnets; the 3 that only use public AWS endpoints (authorizers and developer registration) run outside the VPC.

### Request Flow

```
Game Client / Game Server
    |
    v
API Gateway (REST)
    |
    v
Lambda Authorizer (backendAuthorizer)
    |  SSM Parameter Store lookup (cached)
    |  Returns Allow/Deny policy + context (studioId, gameId, permissions)
    v
Target Lambda Function
    |
    +---> MemoryDB for Valkey (leaderboard sorted sets)
    +---> DynamoDB (config table, stats table)
    +---> SSM Parameter Store (API keys, config)
```

### AWS Resources Created

The CDK deployment creates:

- **VPC** with private subnets and VPC endpoints for Lambda + MemoryDB connectivity
- **MemoryDB for Valkey** cluster (db.r6g.large, 1 shard, 1 replica)
- **DynamoDB tables**: leaderboard config table, player stats table
- **API Gateway** REST API with two Lambda authorizers (backend and player)
- **11 Lambda functions** with shared layer
- **Lambda Layer** (valkey-glide, aws-lambda-powertools, pydantic, etc.)
- **SSM Parameters** for API keys and configuration
- **CloudWatch** log groups, alarms, dashboards
- **Lambda self-invoke relay** for long-running reset/rebuild operations (when processing exceeds the ~15 minute Lambda timeout, the function asynchronously invokes itself with continuation state to resume the work)
- **IAM roles** for all Lambda functions

### CDK Stacks

| Stack | File | Purpose |
|-------|------|---------|
| `GameStatsLeaderboardsStack` | `app.py` (CDK main) | Core infrastructure: VPC, MemoryDB,<br>DynamoDB, API Gateway, Lambda functions |
| `GameStatsLeaderboardsMonitoringStack` | `app_post_deploy.py` | Post-deploy: CloudWatch dashboards,<br>alarms, provisioned concurrency,<br>Lambda Insights |

---

## 3. Deployment Guide

### 3.1 Prerequisites

- An AWS account with sufficient permissions
- An EC2 instance (recommended: Amazon Linux 2023 or Ubuntu) or local machine
- **Python 3.13+** on the deployment host (the deploy script will attempt to install it if missing, but having it pre-installed avoids a source compilation step)
- AWS CLI v2 configured with credentials
- Internet access for package downloads

### 3.2 Deployment from EC2 Instance (Recommended)

#### Step 1: Launch an EC2 Instance

```bash
# Recommended instance type: t3.medium or larger
# AMI: Amazon Linux 2023
# Ensure the instance has an IAM role with:
#   - CloudFormation full access
#   - Lambda full access
#   - DynamoDB full access
#   - MemoryDB full access
#   - API Gateway full access
#   - SSM Parameter Store full access
#   - VPC full access
#   - IAM role creation permissions
#   - CloudWatch full access
#   - (Step Functions access no longer required)
#   - S3 access (for CDK bootstrap)
```

#### Step 2: Clone or Upload the Project

```bash
# Upload the project directory to the EC2 instance
# or clone from your repository
cd /home/ec2-user
# Example: scp -r StatsLeaderboards ec2-user@<instance-ip>:~/
```

#### Step 3: Connect to the EC2 Instance

Connect and log into the EC2 bastion box, then navigate to the project directory:

```bash
# SSH into the EC2 instance
ssh -i <your-key.pem> ec2-user@<instance-ip>

# Navigate to the project directory
cd ~/StatsLeaderboards/

# Verify the project files are present
ls -la
```

> **Multi-Game Deployments:** This system is single-tenanted by design -- one deployment per game. If you need to support multiple games, repeat the full deployment process (Steps 3-5) for each game, each with its own `studio_parameters.json` configuration.

#### Step 4: Configure Studio Parameters

Edit `studio_parameters.json` with your studio details (on the EC2 instance before deploying). This is important metadata, used for the Studio API Key for all backend use:

```json
{
  "StudioName": "Your Studio Name",
  "ContactEmail": "contact@yourstudio.tld",
  "GameTitle": "Your Game Title",
  "GameGenre": "Your Game's Genre"
}
```

**Field Requirements:**

| Field | Type | Constraints |
|-------|------|------------|
| `StudioName` | string | Letters, numbers, spaces, hyphens, underscores, periods, `()!&@` |
| `ContactEmail` | string | Valid email format |
| `GameTitle` | string | Same character rules as StudioName |
| `GameGenre` | string | Same character rules as StudioName (e.g., `racing`, `space-rpg`, `action`) |

#### Step 5: Run the Deployment Script

```bash
cd StatsLeaderboards

# Optional: Set deployment environment (default: dev)
export ENVIRONMENT=dev   # Options: dev, staging, prod

# Optional: Set AWS region (default: us-west-2)
export AWS_DEFAULT_REGION=us-west-2

# Run the deployment
chmod +x deploy.sh
./deploy.sh
```

### 3.3 What the Deploy Script Does (In Order)

1. **Detects operating system** (Amazon Linux, Ubuntu/Debian, macOS)
2. **Installs Python 3.13+** (searches for 3.15 down to 3.13, installs from source if missing)
3. **Bootstraps and upgrades pip** (installs pip via ensurepip or get-pip.py if missing, then upgrades to latest)
4. **Installs AWS CLI v2** if not present
5. **Installs NVM and Node.js** (for AWS CDK)
6. **Installs AWS CDK** globally via npm
7. **Installs Python packages**: boto3, aws-cdk-lib, constructs, aws-lambda-powertools
8. **Persists environment** (PATH, aliases, NVM config) to shell rc files
9. **Validates `studio_parameters.json`** (required fields, character validation, email format)
10. **Builds Lambda Layer** (`layers/build_layer.sh`) with platform-specific wheels:
    - valkey-glide>=2.0.1
    - aws-lambda-powertools[all]==3.0.0
    - pydantic>=2.5.0
    - asyncio-throttle>=1.0.2
    - nest-asyncio>=1.5.8
    - python-dateutil>=2.8.2
11. **Installs CDK dependencies** from `requirements.txt`
12. **Bootstraps CDK** (`cdk bootstrap`)
13. **Synthesizes CDK template** (`cdk synth GameStatsLeaderboardsStack`)
14. **Deploys main stack** (`GameStatsLeaderboardsStack`) with studio parameters
15. **Deploys monitoring stack** (`GameStatsLeaderboardsMonitoringStack`)
16. **Retrieves and displays deployment outputs**: API Endpoint, Studio API Key, Studio ID, Game ID, Layer ARN
17. **Creates convenience scripts** (`deployment_info.sh`)

### 3.4 Deployment Outputs

After successful deployment, you receive:

| Output | Description |
|--------|-------------|
| `ApiEndpoint` | The API Gateway base URL<br>(e.g., `https://abc123.execute-api.us-west-2.amazonaws.com/dev`)<br>to prefix to the specific API end points |
| `StudioAPIKey` | Your generated API key for backend API authentication only.<br>Intended only for game developers' use,<br>do not use this for player API authentication |
| `StudioId` | Your studio identifier,<br>required by most API |
| `GameId` | Your game identifier,<br>required by most API |
| `SharedLayerArn` | Lambda Layer ARN |

These are also stored in CloudFormation outputs and SSM Parameter Store, and here onwards the system only refers to the SSM Parameter Store for this metadata.

### 3.5 Post-Deployment Verification

```bash
# Check deployment info — this convenience script is auto-generated by deploy.sh
# in the project root directory after a successful deployment. It queries CloudFormation
# for your API endpoint, Studio API Key, Studio ID, Game ID, and stack status.
./deployment_info.sh

# Verify the system is operational using the developer info endpoint (recommended health check)
curl -X GET "$API_ENDPOINT/developer/info?studioId=$STUDIO_ID&gameId=$GAME_ID" \
  -H "Authorization: Bearer $API_KEY"
```

> **Note:** There is no dedicated `/health` endpoint. The `GET /developer/info` call serves as the recommended health check, as it exercises the authentication flow and returns registration metadata.

### 3.6 Production Infrastructure Sizing

The system deploys with conservative defaults suitable for development and testing. Before going to production, review and adjust the resource sizing to match your expected workload. Incorrect sizing leads to either throttling (under-provisioned) or unnecessary cost (over-provisioned).

#### Default Deployment Sizing

| Resource | Default | Notes |
|----------|---------|-------|
| **DynamoDB** | On-Demand (PAY_PER_REQUEST) | No pre-provisioned RCU/WCU;<br>scales automatically but at higher per-request cost |
| **MemoryDB for Valkey** | db.r6g.large, 1 shard, 1 replica | Single-shard cluster<br>with one read replica |
| **Lambda (authorizers)** | 256 MB, 10s timeout | backendAuthorizer, playerAuthorizer |
| **Lambda (developer registration)** | 256 MB, 30s timeout | developerRegistration |
| **Lambda (config, player queries)** | 512 MB, 15s timeout | leaderboardsConfig, getPlayerStatsAndScores,<br>getLeaderboardScores, getPlayerLBStanding |
| **Lambda (player store)** | 512 MB, 30s timeout | storePlayerStatsAndScores |
| **Lambda (long-running ops)** | 512 MB, 60s timeout | batchStoreStatsAndScores, resetLeaderboard,<br>rebuildLeaderboard |
| **Lambda Provisioned Concurrency** | 5 min / 50 max (70% target) | Applied to store-stats,<br>get-scores, get-standing |
| **API Gateway** | 100 req/sec rate, 200 burst | Daily quota: 10,000 requests<br>(usage plan) |
| **VPC** | 1 NAT Gateway, 2 AZs | Single NAT Gateway is a<br>single point of failure |

#### Scaling Strategy

There are two approaches. Pick the one that matches your situation:

**Approach A: Start Low, Scale Up (recommended for new games / soft launch)**

Deploy with the defaults, monitor CloudWatch metrics for 1-2 weeks under real traffic, then increase resources where bottlenecks appear. This minimizes cost during the uncertain early period.

**Approach B: Start High, Scale Down (recommended for established games / hard launch)**

If you're launching to a known player base and expect high Day 1 traffic (e.g., >1,000 RPS), over-provision initially to ensure stability, then reduce over the following weeks as you gather real metrics.

#### Per-Service Recommendations

**DynamoDB**

| Setting | Development | Prod (Low) | Prod (High) |
|---------|-------------|------------|-------------|
| Billing Mode | On-Demand | On-Demand | Provisioned |
| Read Capacity (RCU) | Auto | Auto | 500-2,000<br>(with auto-scaling) |
| Write Capacity (WCU) | Auto | Auto | 200-1,000<br>(with auto-scaling) |

- On-Demand is simpler and handles unpredictable traffic well, but costs ~3.5x more per request than provisioned capacity at steady state (up to ~7x with reserved capacity commitments).
- Switch to **Provisioned with Auto-Scaling** once your traffic patterns are predictable. Set the base capacity to your sustained average and let auto-scaling handle peaks.
- Monitor `ConsumedReadCapacityUnits`, `ConsumedWriteCapacityUnits`, and `ThrottledRequests` in CloudWatch.

**MemoryDB for Valkey**

| Setting | Development | Prod (Low-Med) | Prod (High) |
|---------|-------------|----------------|-------------|
| Node Type | db.r6g.large | db.r6g.large | db.r6g.xlarge or 2xlarge |
| Shards | 1 | 1-2 | 2-4 |
| Replicas per Shard | 1 | 2 | 2-3 |

- The node type determines available memory and network throughput. For leaderboards with millions of entries, move to db.r6g.xlarge or higher.
- Add shards if you have many distinct leaderboards that can be distributed across shards (Valkey handles slot-based sharding).
- Add replicas for read throughput (leaderboard queries are read-heavy) and high availability.
- Monitor `DatabaseMemoryUsagePercentage`, `CPUUtilization`, and `CurrConnections`.

**Lambda Functions**

| Setting | Development | Prod (Low) | Prod (High) |
|---------|-------------|------------|-------------|
| Memory (standard) | 256 MB | 512 MB | 1024 MB |
| Memory (long-running) | 512 MB | 1024 MB | 2048 MB |
| Provisioned Concurrency | 5 min / 50 max | 10 min / 100 max | 50 min / 500 max |

- Lambda CPU scales linearly with memory. Increasing memory from 256 MB to 512 MB doubles available CPU and often reduces execution time (and cost) for compute-bound operations.
- Provisioned concurrency eliminates cold starts. The default applies to `store-stats`, `get-leaderboard-scores`, and `get-player-lb-standing`. Increase the minimum if you see consistent cold start latency in these functions.
- Monitor `Duration`, `ConcurrentExecutions`, `Throttles`, and `Errors` per function.

**API Gateway**

| Setting | Development | Prod (Low) | Prod (High) |
|---------|-------------|------------|-------------|
| Rate Limit | 100 req/sec | 1,000 req/sec | 10,000 req/sec |
| Burst Limit | 200 | 2,000 | 5,000 |
| Daily Quota | 10,000 | 1,000,000 | Remove or set<br>to 50,000,000 |

- The default daily quota of 10,000 requests is suitable only for development. A production game will exceed this within minutes.
- Rate and burst limits protect backend resources. Set them based on your expected peak concurrent users.
- Monitor `Count`, `4XXError`, `5XXError`, and `Latency` metrics.

**VPC / Networking**

| Setting | Development | Production |
|---------|-------------|------------|
| NAT Gateways | 1 | 2 (one per AZ) |
| AZs | 2 | 2-3 |

- A single NAT Gateway is a single point of failure. For production, deploy one per Availability Zone.
- Additional AZs improve availability but increase NAT Gateway and cross-AZ data transfer costs.

#### Cost-Performance Trade-offs

| Change | Performance Impact | Cost Impact |
|--------|-------------------|-------------|
| DynamoDB:<br>On-Demand -> Provisioned | Same (if sized correctly) | ~3.5x cheaper at steady-state<br>(up to ~7x with reserved capacity) |
| Lambda:<br>256 MB -> 512 MB | ~2x faster execution | Near-neutral<br>(faster = fewer billed ms) |
| Lambda:<br>Increase provisioned concurrency | Eliminates cold starts | $0.0000041667/GB-sec<br>provisioned |
| MemoryDB:<br>Add replica | Better read throughput, HA | +1 node cost per shard |
| MemoryDB:<br>Larger node type | More memory,<br>higher throughput | Varies by node type |
| API Gateway:<br>Raise limits | Handles more<br>concurrent users | No direct cost<br>(you pay per-request) |
| VPC:<br>Add NAT Gateway | Eliminates SPOF | ~$32/month<br>per NAT Gateway |

#### What to Monitor First

After deployment, set up CloudWatch dashboards (the monitoring stack creates some automatically) and watch these metrics during your first week of real traffic:

1. **DynamoDB**: `ThrottledRequests` (should be 0), `ConsumedReadCapacityUnits`/`ConsumedWriteCapacityUnits` (for sizing provisioned capacity)
2. **Lambda**: `Duration` p99 (for timeout risk), `ConcurrentExecutions` (for provisioned concurrency sizing), `Throttles` (should be 0)
3. **MemoryDB**: `DatabaseMemoryUsagePercentage` (stay under 80%), `CPUUtilization` (stay under 65%)
4. **API Gateway**: `5XXError` rate, `Latency` p99, `Count` (for understanding traffic patterns)

Use these metrics to make data-driven decisions about scaling up or down.

#### Service Quota Increases (for high-scale production)

At high traffic volumes, you will hit AWS account-level defaults that require formal quota increase requests via the [AWS Service Quotas console](https://console.aws.amazon.com/servicequotas/):

| Service | Default Limit | When You'll Hit It | How to Increase |
|---------|--------------|-------------------|-----------------|
| **Lambda concurrent executions** | 1,000 per Region (shared across all functions in the account) | >1,000 simultaneous requests across all Lambda functions in the account | Service Quotas console; increases typically granted to tens of thousands |
| **API Gateway account-level throttle** | 10,000 RPS per Region (shared across all REST/HTTP/WebSocket APIs in the account) | >10,000 req/sec aggregate | Service Quotas console; requires justification |
| **DynamoDB on-demand table throughput** | 40,000 read request units / 40,000 write request units per table | Sustained throughput beyond 40K on a single table | DynamoDB auto-scales: instantly supports up to 2x previous peak; exceeding 2x requires ~30 minutes of gradual ramp. New tables start at 4,000 WRU / 12,000 RRU. For immediate high capacity, switch to provisioned mode. |
| **DynamoDB provisioned account-level throughput** | 80,000 RCU / 80,000 WCU per account (shared across all provisioned tables) | Aggregate provisioned capacity across all tables exceeds 80K | Service Quotas console |
| **MemoryDB nodes per cluster** | Max 500 nodes per cluster (up to 500 shards with 0 replicas, or fewer shards with up to 5 replicas each) | When scaling beyond your current shard/replica configuration | Service Quotas console or AWS Support |

**Important notes:**

- These limits are **account-wide and shared** with other workloads in the same account and Region. If you run other Lambda functions, APIs, or DynamoDB tables in the same account, their usage counts toward the same quotas. Consider deploying production game workloads in a dedicated AWS account.
- Quota increase approvals are not guaranteed — AWS evaluates requests based on your account's usage history, payment history, and the specific limit being requested. Provide clear justification (expected player count, peak RPS, launch date) when submitting requests.
- Request increases **before** launch day. Approvals can take 1-3 business days for standard limits and up to 2 weeks for large increases. For major launches or events, submit requests at least 2-4 weeks in advance.
- Lambda concurrency is the most common bottleneck at scale. If 8 functions each handle 125 concurrent requests, you hit the 1,000 default. Request an increase to at least 3-5x your expected peak concurrent request count.

---

## 4. Integration

### Integration Guide — Who Calls What

This system has two distinct callers with separate authentication paths:

**Your Game Backend / Server (uses StudioAPI Key)**

These endpoints are called server-side, authenticated with the StudioAPI Key issued during deployment. The StudioAPI Key must never be embedded in game client builds or distributed to players.

| When | API Call | Purpose |
|------|----------|---------|
| Initial setup (one-time) | `POST /developer/register` | Register your game, receive StudioAPI Key |
| Verify deployment health | `GET /developer/info` | Confirm system is running and return registration metadata |
| Game design time | `POST /leaderboards/config/create` | Create a leaderboard configuration (one per game mode / metric combination) |
| Query existing configs | `GET /leaderboards/configs` | List all configured leaderboards for your game |
| Update a leaderboard | `PUT /leaderboards/config/update` | Change scoring strategy, bounds, expiry, or other settings |
| After a multiplayer match ends | `POST /leaderboards/stats/batch` | Submit all players' scores and full raw game reports from the match in one request (default limit: 1,000 reports, configurable via `MAX_ITEMS_PER_REQUEST` env var — increase alongside Lambda memory and timeout for larger batches) |
| Periodic maintenance | `POST /leaderboards/admin/reset` | Clear leaderboard scores (e.g., weekly reset for seasonal boards). Backs up scores before clearing. |
| After config/strategy change, or data recovery | `POST /leaderboards/admin/rebuild` | Rebuild leaderboard from stored stats (e.g., apply a new scoring strategy, or recover from leaderboard corruption using raw stats in DynamoDB as source of truth) |
| Remove a leaderboard | `DELETE /leaderboards/config/delete` | Delete leaderboard configuration and its sorted set |

The batch endpoint (`/leaderboards/stats/batch`) is the primary ingestion path for multiplayer games. Your game server collects each player's score and full raw game report (arbitrary JSON containing match stats, events, metadata) at match end and submits them in a single request. Each player can appear only once per batch — duplicates are rejected with HTTP 423. The raw game reports are persisted in DynamoDB and serve as the source of truth for rebuilds and player history queries.

**Your Game Client / Player Device (uses player auth token)**

These endpoints are called from the game client or on behalf of a player. They require player authentication — a token your game's identity system issues to authenticated players.

| When | API Call | Purpose |
|------|----------|---------|
| After a solo game session ends | `POST /leaderboards/stats` | Submit the player's score and full raw game report for a specific leaderboard |
| Viewing a leaderboard screen | `POST /leaderboards/scores` | Query leaderboard: top N, score range, around-player, or specific player |
| Viewing player profile / history | `POST /leaderboards/player/stats` | Get the player's stored game reports (filterable by time range, game mode, leaderboard) |
| Showing rank badge / position | `POST /leaderboards/player/standing` | Get the player's rank, percentile, score, and neighbouring players |

**Choosing between client-side and server-side score submission:**

| Game Type | Score Submission Path | Reason |
|-----------|----------------------|--------|
| Multiplayer with authoritative server | `/leaderboards/stats/batch` from game server | Server validates match results; prevents client-side score falsification |
| Single-player (no game server) | `/leaderboards/stats` from game client | No server to relay through; client submits directly |
| Single-player with backend validation | `/leaderboards/stats/batch` from game server | Game server validates replay/report before submission for anti-cheat |

**Quick examples:**

Batch submission from game server (after a multiplayer match):

```bash
curl -X POST "$API_ENDPOINT/leaderboards/stats/batch" \
  -H "Authorization: Bearer $STUDIO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "batchGameReportBody": {
      "gameReports": [
        {
          "playerID": "player-abc-123",
          "gameID": "my-game",
          "gameMode": "deathmatch",
          "playerScore": 2750,
          "leaderboardName": "deathmatch-highscore",
          "fullRawGameReport": { "kills": 14, "deaths": 3, "assists": 7, "matchDuration": 482 }
        },
        {
          "playerID": "player-xyz-789",
          "gameID": "my-game",
          "gameMode": "deathmatch",
          "playerScore": 1890,
          "leaderboardName": "deathmatch-highscore",
          "fullRawGameReport": { "kills": 9, "deaths": 5, "assists": 4, "matchDuration": 482 }
        }
      ]
    }
  }'
```

Individual score submission from game client (single-player):

```bash
curl -X POST "$API_ENDPOINT/leaderboards/stats" \
  -H "Authorization: Bearer $PLAYER_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "gameReportBody": {
      "playerID": "player-abc-123",
      "gameID": "my-game",
      "gameMode": "time-trial",
      "playerScore": "1:42.385",
      "leaderboardName": "track-a-fastest-lap",
      "fullRawGameReport": { "lapTimes": [105.2, 102.385, 108.7], "vehicle": "sports-car", "track": "coastal-highway" }
    }
  }'
```

Querying the leaderboard (top 10) from game client:

```bash
curl -X POST "$API_ENDPOINT/leaderboards/scores" \
  -H "Authorization: Bearer $PLAYER_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "leaderboardScoresRequest": {
      "leaderboardName": "deathmatch-highscore",
      "queryType": "top",
      "pageSize": 10
    }
  }'
```

For complete request/response schemas, all query types, and additional examples, see [API Reference](docs/api_reference.md).

**Typical integration flow:**

1. **Deploy** — Run `./deploy.sh` on an EC2 instance or local machine. Note the API endpoint and StudioAPI Key from the output.
2. **Configure leaderboards** — Call `POST /leaderboards/config/create` from your game backend for each leaderboard your game needs (e.g., "level-1-highscore", "weekly-kills", "fastest-lap-trackA").
3. **Integrate player authentication** — Edit `auth/playerAuthorizer.py` to validate your game's player tokens (JWT, OAuth, session ID, platform token, etc.). Deploy the update via `cdk deploy`.
4. **Wire score submission** — For multiplayer: call `/leaderboards/stats/batch` from your game server after each match. For single-player: call `/leaderboards/stats` from the game client after each session.
5. **Wire leaderboard UI** — Call `/leaderboards/scores` (top players, nearby, ranges) and `/leaderboards/player/standing` (current player's rank) from your game client to display leaderboard screens.
6. **Wire player stats UI** — Call `/leaderboards/player/stats` to show the player's match history on their profile screen.
7. **Test end-to-end** — Run the integration test suite (`testing/test_StatsAndLeaderboards.py`) against your deployment to verify all score types, strategies, and query patterns work correctly.

### Before You Go to Production — Integration Points

Backend (developer) APIs work after deployment without additional configuration. Player-facing APIs require you to integrate your own player authentication before they will accept requests.

**Required integration (player auth):**

| File | What to do |
|------|-----------|
| `auth/playerAuthorizer.py` | Add your player token validation logic. The function receives the `Authorization` header value and must return an IAM policy with `studioId`, `gameId`, `permissions`, and optionally `playerId` in the authorizer context. |
| `app.py` | (Optional) Replace the built-in player authorizer Lambda with your own function if you already have a separate authorizer |

**No changes needed:**

| File | Why |
|------|-----|
| `player/*.py` | All four player Lambda functions read from `event['requestContext']['authorizer']` — they work with any authorizer that provides the required context fields |
| `backend/*.py` | Backend functions use the StudioAPI Key flow which works after deployment |
| `auth/backendAuthorizer.py` | Backend auth is fully functional via SSM Parameter Store |

Until player auth is integrated, all player API routes return HTTP `401` with a response body explaining what to configure. Search for `INTEGRATION POINT` across the codebase to find every location that needs attention.

For step-by-step integration instructions with code examples (JWT, OAuth), see [API Reference — Section 1.5](docs/api_reference.md#15-integrating-player-authentication).

### API Reference

For the complete API documentation including authentication details, all endpoint request/response schemas, data models, leaderboard concepts, environment variables, and error codes, see:

**[API Reference (docs/api_reference.md)](docs/api_reference.md)**

---

## Appendices

## Appendix A: Introduction to Leaderboards and Game Statistics

### What are Leaderboards, that are typically used in games?

A leaderboard is a ranked list of players ordered by a specific metric (score, time, distance, etc.). Leaderboards are a common feature in games, giving players a way to compare performance and track progression.

**Common leaderboard use cases:**
- **High score boards** -- ranking players by their best or cumulative scores in a game mode
- **Speedrun/time trial boards** -- ranking players by fastest completion times (lower is better)
- **Seasonal/event boards** -- temporary leaderboards for limited-time events, often with expiry dates
- **Ranked/ELO boards** -- tracking player skill ratings that change after each match

### How Leaderboard Scoring Works

When a player completes a match or game session, their result is submitted as a **game report** containing a score and arbitrary game-specific statistics. The system then:

1. **Stores the full game report** in a persistent stats database (DynamoDB) for historical record
2. **Updates the leaderboard** in a fast in-memory data store (MemoryDB for Valkey) based on the configured **score strategy**:
   - **best** -- only update the leaderboard if the new score is better than the player's existing score
   - **cumulative** -- add the new score to the player's running total
   - **replace** -- always overwrite the player's score with the latest submission

### What are Game Statistics?

Game statistics are the raw game play data from each player session. Unlike leaderboard scores _(which track a single metric per player per leaderboard)_, game stats preserve the complete game report -- every match, every session, with all associated data _(kills, deaths, assists, items collected, time played, etc)_. Game statistics are the data source used to build and update leaderboards.

This historical data enables:
- Player progression tracking over time
- Per-match breakdowns and analytics
- Leaderboard rebuilds from historical data (e.g., recalculating a leaderboard with a different strategy)

### Ascending vs. Descending Leaderboards

| Direction | Ranking | Use Case | Example |
|-----------|---------|----------|---------|
| **Descending** (`DESCENDING_LB`) | Highest score ranks first | Points, XP, kills | "Top Scorers" |
| **Ascending** (`ASCENDING_LB`) | Lowest score ranks first | Race times, golf scores | "Fastest Laps" |

For ascending leaderboards, the system stores scores as negative values internally so that the same sorted set operations produce the correct ordering.

---

## Appendix B: Load Testing Framework

The system includes a distributed load testing framework (`testing/load-stress-testing/test_LoadAndStressTests.py`) for testing system behavior under sustained load. The framework uses concurrent worker threads across multiple test instances to simulate realistic multiplayer game traffic.

### Framework Overview

- **Architecture**: Distributed test runner with configurable player/batch worker threads per instance
- **Test scenarios**: Player score submission, leaderboard queries, batch operations, mixed workloads
- **Data models**: Configurable player pools, score distributions, and game report structures
- **Metrics collection**: Response time percentiles, throughput measurement, error rate tracking, CloudWatch integration
- **Reports**: HTML, JSON, CSV, and Markdown summary reports generated automatically in `testing/load-stress-testing/reports/`

### Reference Load Test Results

The following results were captured during a ~108-minute sustained load test against the default development infrastructure. This is intended as a reference baseline -- your results will vary based on infrastructure sizing, region, and traffic patterns.

**Test Configuration:**

| Setting | Value |
|---------|-------|
| Duration | 108 minutes |
| Test instances | 31 (distributed) |
| Worker threads | 1,100 player + 153 batch |
| Peak concurrent players | 168 |
| Region | us-west-2 |

**Throughput:**

| Metric | Value |
|--------|-------|
| Peak RPS (CloudWatch) | **5,095 req/sec** |
| Total API requests | **17.6 million** |
| Total Lambda invocations | **17.5 million** |
| Success rate | 99.99% (29 failures out of 338,328 test-tracked requests) |

**Latency:**

| Metric | Value |
|--------|-------|
| API Gateway avg latency | 69.3 ms |
| API Gateway P99 latency | 474 ms (avg); 2,948 ms peak during cold-start ramp-up |
| Store stats avg | 58.2 ms |
| Get leaderboard scores avg | 55.1 ms |
| Get player standing avg | 32.4 ms |
| Get player stats avg | 33.5 ms |
| Batch store avg | 632.0 ms |

**Lambda Invocations and Concurrency:**

| Function | Invocations | Errors | Throttles | Avg Duration | Peak Concurrency |
|----------|-------------|--------|-----------|-------------|-----------------|
| store-stats | 7,595,181 | 0 | 0 | 58 ms | 378 |
| get-leaderboard-scores | 5,986,175 | 0 | 0 | 55 ms | 385 |
| get-player-lb-standing | 3,038,730 | 0 | 0 | 32 ms | 210 |
| get-player-stats | 756,324 | 0 | 0 | 34 ms | 23 |
| batch-store-stats | 168,782 | 0 | 0 | 632 ms | 111 |
| backend-authorizer | 4,749 | 0 | 0 | 218 ms | 860 |

**DynamoDB (On-Demand):**

| Table | Items | Size | Write Capacity (total) | Read Capacity (total) | Throttled |
|-------|-------|------|----------------------|---------------------|-----------|
| Stats | 2,222,143 | 1.15 GB | 11,848,674 WCU | 809,504 RCU | 0 |
| Config | 6 | ~3 KB | 7 WCU | 4,804 RCU | 0 |

**Infrastructure Under Test (default dev sizing, except API Gateway rate limits increased for load test):**

| Resource | Configuration |
|----------|--------------|
| Lambda (config, player queries) | 512 MB, 15s timeout, Python 3.13 |
| Lambda (player store) | 512 MB, 30s timeout, Python 3.13 |
| Lambda (batch, reset, rebuild) | 512 MB, 60s timeout, Python 3.13 |
| MemoryDB for Valkey | db.r6g.large, 1 shard, 1 replica |
| DynamoDB | On-Demand (PAY_PER_REQUEST) |
| API Gateway | 10,000 req/sec rate, 5,000 burst |
| Lambda concurrency | Unreserved (AWS account default) |

**Key Observations:**

- Zero Lambda errors and zero throttles across 17.5 million invocations
- Zero DynamoDB throttled requests despite on-demand billing
- The API Gateway 4XX/5XX errors (18,844 + 40,501) were from the authorizer caching warm-up period and concurrent test instance ramp-up, not from the core Lambda functions
- Read-heavy queries (leaderboard scores, player standings) sustained sub-100ms average latency at 5,095 peak RPS
- MemoryDB handled all sorted set operations with zero errors at the default single-shard configuration

> **Scaling Tip:** For production deployments expecting sustained traffic above 5,000 RPS, consider placing a CloudFront distribution in front of the read-heavy player query endpoints (`/leaderboards/scores`, `/leaderboards/player/standing`) with a short TTL (5-10 seconds). This can absorb repeated leaderboard page views while keeping data fresh within the TTL window, reducing Lambda invocations and MemoryDB load proportionally to the cache hit rate.

> **Note:** These results reflect the default development sizing. Production deployments should adjust infrastructure per the [Production Infrastructure Sizing](#36-production-infrastructure-sizing) section and run their own load tests with representative data patterns. Use `testing/load-stress-testing/test_LoadAndStressTests.py` to execute your own load tests and generate comparable reports.

---

*Note: Documentation generated from source code analysis of the Stats and Leaderboards codebase. Please report any mistakes or exclusions, or suggest improvements.*
