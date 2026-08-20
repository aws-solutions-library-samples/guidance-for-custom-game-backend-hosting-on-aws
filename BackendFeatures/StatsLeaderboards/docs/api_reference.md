# API Reference - Game Stats and Leaderboards System

> 📖 Back to [Main Documentation](../readme.md)

## Table of Contents

1. [Authentication](#1-authentication)
2. [Backend (Developer) APIs](#2-backend-developer-apis)
   - [Developer Registration](#21-developer-registration)
   - [Leaderboard Configuration](#22-leaderboard-configuration)
   - [Batch Store Stats and Scores](#23-batch-store-stats-and-scores)
   - [Reset Leaderboard](#24-reset-leaderboard)
   - [Rebuild Leaderboard](#25-rebuild-leaderboard)
3. [Player APIs](#3-player-apis)
   - [Store Player Stats and Scores](#31-store-player-stats-and-scores)
   - [Get Leaderboard Scores](#32-get-leaderboard-scores)
   - [Get Player Stats and Scores](#33-get-player-stats-and-scores)
   - [Get Player Leaderboard Standing](#34-get-player-leaderboard-standing)
4. [Data Models & Storage](#4-data-models--storage)
5. [Leaderboard Concepts](#5-leaderboard-concepts)
6. [Environment Variables Reference](#6-environment-variables-reference)
7. [Error Reference](#7-error-reference)

---


## 1. Authentication

### 1.1 Authentication Flow

All API endpoints (except developer registration) require authentication via an API Gateway Lambda authorizer. The flow below traces the **backend authorizer** (StudioAPI Key) path used by developer APIs; player-facing APIs use a **separate** authorizer with a different context, described in [Section 1.5](#15-integrating-player-authentication).

> **Backend vs. Player Authentication:** The StudioAPI Key is for backend (developer) APIs only. Player-facing APIs are authenticated separately. By default they use access tokens issued by the AWS Game Backend Custom Identity Component. See [Section 1.5](#15-integrating-player-authentication).

```
Client Request
    |
    +-- Header: Authorization: Bearer <api_key>
    |
    v
Lambda Authorizer
    |
    +-- Extract API key from headers
    +-- Check per-API-key cache (5 min TTL)
    +-- If miss: check per-container SSM cache (60 sec TTL)
    +-- If miss: load from SSM Parameter Store
    +-- Validate key with hmac.compare_digest (timing-attack resistant)
    +-- Return Allow/Deny policy with context
    |
    v
Target Lambda receives authorizer context:
  {
    "authType": "api_key",
    "studioId": "...",
    "gameId": "...",
    "studioName": "...",
    "gameTitle": "...",
    "contactEmail": "...",
    "permissions": "read,write",
    "rateLimit": "1000",
    "environment": "dev",
    "userId": "{studioId}_{gameId}",
    "keyStatus": "active"
  }
```

### 1.2 Supported Authentication Header Formats

The authorizer supports multiple header formats, checked in priority order:

| Priority | Format | Example |
|----------|--------|---------|
| 1 (highest) | Bearer token | `Authorization: Bearer abc123-def456` |
| 2 | ApiKey prefix | `Authorization: ApiKey abc123-def456` |
| 3 | Direct/legacy | `Authorization: abc123-def456` |

**X-Api-Key Header (Backward Compatibility):** The `X-Api-Key` header is supported for backward compatibility with CORS-constrained clients. It is **not** a standalone authentication method. It requires the `Authorization` header to be present (albeit blank/empty) for the authorizer to read the `X-Api-Key` value. Example:

```
Authorization:
X-Api-Key: abc123-def456
```

Without the `Authorization` header present, the `X-Api-Key` header alone will not authenticate the request.

### 1.3 Caching Layers

| Cache | Scope | TTL | Purpose |
|-------|-------|-----|---------|
| Per-container SSM cache | All keys from SSM | 60 seconds | Eliminates SSM throttling during bursts |
| Per-API-key cache | Individual key | 5 minutes (300s) | Fast lookup for repeated requests |

### 1.4 Permissions

The authorizer context includes a `permissions` field (comma-separated string). All Lambda functions enforce these permissions via a `validate_authenticated_context()` function that checks the required permission before processing:

| Permission | Required For |
|-----------|-------------|
| `read` | All GET/query operations (getLeaderboardScores, getPlayerStats, getPlayerLBStanding, config reads) |
| `write` | All mutating operations (storeStats, batchStore, config create/update/delete, reset, rebuild) |
| `admin` | Currently unused; accepted by the authorizer but not enforced by any endpoint |

> **Note:** Since this system is single-tenanted (one deployment per game), both `read` and `write` permissions are granted by default during registration. The permissions framework exists for future extensibility (e.g., read-only API keys for analytics dashboards).

### 1.5 Integrating Player Authentication

The system uses **two separate API Gateway authorizers** to cleanly separate backend and player authentication:

```
                          +---------------------------+
                          |       API Gateway         |
                          +---------------------------+
                                      |
                   +------------------+------------------+
                   |                                     |
          Backend Routes                        Player Routes
          /developer/*                          /leaderboards/stats
          /leaderboards/config/*                /leaderboards/scores
          /leaderboards/configs                 /leaderboards/player/*
          /leaderboards/admin/*
          /leaderboards/stats/batch
                   |                                     |
                   v                                     v
     backendAuthorizer.py               playerAuthorizer.py
     (StudioAPI Key via SSM)               (Custom Identity Component tokens)
     Functional after deployment            identity mode by default
                   |                                     |
                   v                                     v
          Backend Lambdas                       Player Lambdas
```

- **Backend routes** use `auth/backendAuthorizer.py`, which validates the StudioAPI Key from SSM Parameter Store. Functional after deployment.
- **Player routes** use `auth/playerAuthorizer.py`, which runs in one of two modes set by the `PLAYER_AUTH_MODE` environment variable.

Each authorizer has its own API Gateway cache, so backend and player auth stay isolated.

**identity mode (default).** Player requests carry an access token issued by the AWS Game Backend Custom Identity Component:

```
Authorization: Bearer <player_access_token>
```

The authorizer verifies the token's RS256 signature against the issuer's public keys (JWKS), checks the audience (`gamebackend`) and issuer, and confirms it has not expired. On success it passes the player id, the granted permissions, and this deployment's studio and game ids to the target Lambda. You set the issuer URL at deploy time and no code changes are needed.

> **Token expiry is not instant.** API Gateway caches each authorizer decision for 5 minutes (`results_cache_ttl`), keyed on the `Authorization` header. A token validated once is therefore accepted from cache for up to 5 minutes even after it expires. If your access tokens are short-lived and you need tighter enforcement, lower the player authorizer's `results_cache_ttl` in `app.py`.

Set the issuer URL before deploying:

```
export ISSUER_ENDPOINT_URL=https://xxxxxxxx.cloudfront.net
```

This is the `IssuerEndpointUrl` output of the CustomIdentityComponentStack. `deploy.sh` requires it in identity mode and stops with guidance if it is missing.

The token's `scope` claim maps to permissions:

| scope | permissions |
|-------|-------------|
| `guest` | read, write |
| `authenticated` | read, write |

Both scopes get read and write by default. To let guests read only, change the `guest` entry to `read` in the `SCOPE_PERMISSIONS` table at the top of `auth/playerAuthorizer.py`. An unrecognized scope is denied.

**custom mode.** For standalone deployments that do not use the Custom Identity Component. Set `PLAYER_AUTH_MODE=custom` and add your own token validation in `_authorize_custom()` in `auth/playerAuthorizer.py`. It denies every request until you do. See "Custom mode" below.

#### Player-identity enforcement

On top of token validation and permission scope, the player Lambdas enforce that a caller acts only as themselves. Each handler compares the request's `playerID` against the authenticated `playerId` in the authorizer context and rejects a mismatch with HTTP 403 (`error: "Forbidden"`), logging a `PLAYER_ID_MISMATCH` warning for abuse monitoring.

In identity mode the authorizer always sets `playerId` (from the token's `sub`), so this is always active. In custom mode it applies only when your authorizer sets `playerId` (enforce-when-present), so an authorizer that omits it stays backward compatible, though without this protection.

| Endpoint | Default enforcement | Notes |
|----------|--------------------|-------|
| `POST /leaderboards/stats` (store score) | Enforced | A player may only submit under their own `playerID` (anti-spoofing). |
| `POST /leaderboards/player/stats` | Enforced | A player may only read their own stats history (privacy). |
| `POST /leaderboards/player/standing` | Enforced by default | A player may only view their own standing. Set `ALLOW_VIEWING_OTHER_PLAYERS_STANDING = True` in `getPlayerLBStanding.py` to allow viewing others. |
| `POST /leaderboards/scores` | Not enforced (public) | Leaderboard scores are public ranking data. Optional: set `RESTRICT_PLAYER_QUERIES_TO_SELF = True` in `getLeaderboardScores.py` to restrict the player-scoped query types to the caller. |

The backend batch path (`POST /leaderboards/stats/batch`, StudioAPI-key authenticated) is exempt: a trusted game server submits many players' scores in one call.

Together, the two authorizers and this check form three layers: token validation, then permission scope, then player identity.

#### Backend API Authentication (StudioAPI Key)

The `StudioAPI Key` is generated during deployment and stored in SSM Parameter Store. It authenticates all backend (developer) APIs — leaderboard configuration, batch submissions, reset, and rebuild operations.

**This key is intended for server-side use only.** It grants full `read` and `write` access to all of your game's stats and leaderboard data. Do not embed it in game client builds, mobile apps, or any code distributed to end users.

#### How Player Authentication Works

In identity mode (the default), the AWS Game Backend Custom Identity Component authenticates players and issues each one an access token. The game client sends that token to this component on every request, and the player authorizer validates it — RS256 signature against the issuer's JWKS, audience, issuer, and expiry — before the request reaches a player Lambda. In custom mode, your own identity provider takes the place of the Custom Identity Component and `_authorize_custom()` performs the validation.

```
    Player launches game
           |
           v
    Custom Identity Component            (identity mode, default)
    — or your own identity provider      (custom mode)
    (authenticates the player, issues an access token)
           |
           v
    Game Client receives token
           |
    +------+------+
    |             |
    v             v
  Your Game    Stats & Leaderboards API
  Services     Authorization: Bearer <player_access_token>
                      |
                      v
               playerAuthorizer.py
               (identity mode: verify RS256 signature via JWKS;
                custom mode: your own validation)
                      |
                      v
               Player API Lambda
               (processes request for the authenticated player)
```

---

#### Custom Mode: Bring Your Own Player Auth

For standalone deployments that do not use the Custom Identity Component, set `PLAYER_AUTH_MODE=custom` and implement `_authorize_custom()` in `auth/playerAuthorizer.py`. It denies every request until you do.

On success, return an Allow via `generate_policy(...)` with these context fields, which the player Lambdas read from `event['requestContext']['authorizer']`:

| Field | Required | Description |
|-------|----------|-------------|
| `studioId` | Yes | Your studio identifier |
| `gameId` | Yes | Your game identifier |
| `permissions` | Yes | Comma-separated: `read`, `write`, or `read,write` |
| `playerId` | Recommended | The authenticated player's identity. Used for audit logging and as a security control (see "Player-identity enforcement"). |

The API Gateway routing and the player Lambdas are already wired, so no changes are needed in `app.py` or the player functions.

> **Important:** The StudioAPI Key grants full read/write access to all of your game's stats and leaderboard data, including destructive operations like reset and delete. It must never be used as a player credential. If a player obtains the StudioAPI Key, they can manipulate all stats and leaderboard data for all players.

---

## 2. Backend (Developer) APIs

### Invocation Flow

Backend APIs are intended to be called **exclusively from the game developer's backend infrastructure** (game servers, admin tools, CI/CD pipelines). They should never be invoked from the game client or player-facing code. The typical flow is:

```
Game Server / Admin Tool / CI Pipeline
    |
    +-- Authorization: Bearer <StudioAPI Key>   (server-side only!)
    |
    v
API Gateway --> Lambda Authorizer --> Backend Lambda
    |                                      |
    |                                      +-> Configure leaderboards
    |                                      +-> Submit batch game reports
    |                                      +-> Reset / Rebuild leaderboards
    |                                      +-> Manage API keys
    v
    X  NEVER from game client or player device
```

1. The studio deploys the system and receives the API endpoint and StudioAPI Key.
2. The studio's game backend (e.g., multiplayer game servers, match coordinators) calls these APIs using the StudioAPI Key to configure leaderboards, submit batch game reports, and manage leaderboard lifecycle operations.
3. The StudioAPI Key must be stored securely on the server side and never embedded in client builds.

> **Security Warning:** The StudioAPI Key grants full `read` and `write` access to all of your game's stats and leaderboard data, including the ability to delete leaderboard configurations and reset scores. **Do not embed it in game client builds, mobile apps, or any distributed code.** If the key is compromised, an attacker can manipulate all stats and leaderboard data for all players. See [Section 1.5](#15-integrating-player-authentication) for how to integrate your own player authentication.

All backend APIs require authentication with the StudioAPI Key and `write` permission (except developer info which needs `read`).

---

### 2.1 Developer Registration

#### POST /developer/register - Register Studio & Game

Creates a new studio/game registration and generates an API key.

**Request:**

```http
POST /developer/register
Content-Type: application/json
```

**Request Template (all parameters):**

```json
{
  "devRegRequest": {
    "studioName": "<string>",
    "contactEmail": "<string>",
    "gameTitle": "<string>",
    "gameGenre": "<string>"
  }
}
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `studioName` | string | Yes | Studio display name. Letters, numbers, spaces, hyphens, underscores, periods, `()!&@` |
| `contactEmail` | string | Yes | Valid email address |
| `gameTitle` | string | Yes | Game title. Same character rules as studioName |
| `gameGenre` | string | Yes | Game genre. Same character rules as studioName (e.g., `racing`, `space-rpg`, `action`) |

**Example Request:**

```json
{
  "devRegRequest": {
    "studioName": "Cosmic Games",
    "contactEmail": "contact@cosmicgames.com",
    "gameTitle": "Stellar Odyssey",
    "gameGenre": "space-rpg"
  }
}
```

**Success Response (201):**

```json
{
  "devRegResponse": {
    "success": true,
    "message": "Developer registration completed successfully",
    "registration": {
      "studioId": "cosmicgames-a1b2c3d4",
      "gameId": "stellarodyssey-e5f6g7h8",
      "studioName": "Cosmic Games",
      "gameTitle": "Stellar Odyssey",
      "permissions": ["read", "write"],
      "rateLimit": 1000,
      "environment": "dev",
      "registrationDate": "2026-02-19T00:00:00Z"
    },
    "usage": {
      "apiEndpoint": "https://abc123.execute-api.us-west-2.amazonaws.com/dev",
      "documentation": "https://your-docs-url.com/dev",
      "supportEmail": "support@your-domain.com"
    },
    "changes": {
      "previousRegistration": "oldstudioid/oldgameid",
      "newRegistration": "cosmicgames-a1b2c3d4/stellarodyssey-e5f6g7h8",
      "parameterUpdated": "/game-statsleaderboards-dev/api-keys/cosmicgames-a1b2c3d4-stellarodyssey-e5f6g7h8",
      "oldParameterDeleted": null
    },
    "note": "Registration completely refreshed. Use the NEW API key for all subsequent requests. Old API key is no longer valid."
  }
}
```

> **Security Note:** The API key is deliberately **not** returned in the registration response. Retrieve it from AWS SSM Parameter Store using the `studioId` and `gameId` from the response. This prevents API keys from appearing in logs, network traces, or client-side storage.

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 400 | Missing or invalid fields |
| 409 | Studio/game combination already registered (SSM ParameterAlreadyExists) |
| 429 | AWS throttling |
| 500 | Internal error (including configuration errors) |

---

#### POST /developer/regenerate-key - Regenerate API Key

Generates a new API key, invalidating the previous one.

**Request Template (all parameters):**

```json
{
  "devRegRequest": {
    "studioId": "<string>",
    "gameId": "<string>",
    "contactEmail": "<string>"
  }
}
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `studioId` | string | Yes | Studio identifier (from registration) |
| `gameId` | string | Yes | Game identifier (from registration) |
| `contactEmail` | string | Yes | Must match the registered email |

**Example Request:**

```json
{
  "devRegRequest": {
    "studioId": "cosmicgames-a1b2c3d4",
    "gameId": "stellarodyssey-e5f6g7h8",
    "contactEmail": "contact@cosmicgames.com"
  }
}
```

**Success Response (200):**

```json
{
  "devRegResponse": {
    "success": true,
    "message": "API key regenerated successfully",
    "environment": "dev",
    "regenerationDate": "2026-02-19T00:00:00Z",
    "note": "New API key stored in SSM Parameter Store. Retrieve using studioId and gameId."
  }
}
```

> **Note:** The new API key is NOT returned in the response for security. Retrieve it from SSM Parameter Store.

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 400 | Missing required fields or invalid input |
| 401 | Authentication failed (invalid or expired API key) |
| 403 | Contact email does not match registration; or revoked key |
| 404 | Developer registration not found |
| 500 | Internal server error |

---

#### GET /developer/info - Get Developer Information

This endpoint also serves as the recommended health check for the system.

**Request Template:**

```http
GET /developer/info?studioId=<string>&gameId=<string>
Authorization: Bearer <api_key>
```

| Query Parameter | Type | Required | Description |
|-----------------|------|----------|-------------|
| `studioId` | string | Yes | Studio identifier (from registration) |
| `gameId` | string | Yes | Game identifier (from registration) |

**Example Request:**

```http
GET /developer/info?studioId=cosmicgames-a1b2c3d4&gameId=stellarodyssey-e5f6g7h8
Authorization: Bearer <api_key>
```

**Success Response (200):**

```json
{
  "devRegResponse": {
    "studioId": "cosmicgames-a1b2c3d4",
    "gameId": "stellarodyssey-e5f6g7h8",
    "studioName": "Cosmic Games",
    "gameTitle": "Stellar Odyssey",
    "gameGenre": "space-rpg",
    "permissions": ["read", "write"],
    "rateLimit": 1000,
    "status": "active",
    "registrationDate": "2026-02-19T00:00:00Z",
    "lastKeyRotation": "2026-02-19T00:00:00Z",
    "environment": "dev"
  }
}
```

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 400 | Missing `studioId` or `gameId` query parameters; or invalid input |
| 401 | Authentication failed (invalid or expired API key) |
| 404 | Developer registration not found |
| 500 | Internal server error |

---

#### PUT /developer/revoke - Revoke API Key

**Request Template (all parameters):**

```json
{
  "devRegRequest": {
    "studioId": "<string>",
    "gameId": "<string>",
    "contactEmail": "<string>"
  }
}
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `studioId` | string | Yes | Studio identifier (from registration) |
| `gameId` | string | Yes | Game identifier (from registration) |
| `contactEmail` | string | Yes | Must match the registered email |

**Example Request:**

```json
{
  "devRegRequest": {
    "studioId": "cosmicgames-a1b2c3d4",
    "gameId": "stellarodyssey-e5f6g7h8",
    "contactEmail": "contact@cosmicgames.com"
  }
}
```

**Success Response (200):**

```json
{
  "devRegResponse": {
    "success": true,
    "message": "API key revoked successfully",
    "environment": "dev",
    "revocationDate": "2026-02-19T00:00:00Z"
  }
}
```

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 400 | Missing required fields or invalid input |
| 401 | Authentication failed (invalid or expired API key) |
| 403 | Contact email does not match; or revoked key |
| 404 | Registration not found |
| 500 | Internal server error |

---

### 2.2 Leaderboard Configuration

All configuration responses are wrapped in `gameLeaderboardConfigResponse`.

#### POST /leaderboards/config/create - Create Leaderboard

**Request:**

```http
POST /leaderboards/config/create
Content-Type: application/json
Authorization: <api_key>
```

**Request Template (all parameters):**

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "<string>",
    "gameMode": "<string>",
    "leaderboardName": "<string>",
    "statAttributeForLeaderboard": "<string>",
    "leaderboardType": "<string>",
    "scoreStrategy": "<string>",
    "scoreType": "<string>",                        // Optional, default: "score"
    "sortOrder": "<string>",                        // Optional: "asc" or "desc"
    "timePrecision": "<int>",                       // Optional, default: 3 (time/rank types only)
    "timeFormat": "<string>",                       // Optional, default: "seconds" (time/rank types only)
    "minValidTimeInSeconds": "<float>",             // Optional (time/rank types only)
    "maxValidTimeInSeconds": "<float>",             // Optional (time/rank types only)
    "minValidScore": "<number>",                    // Optional (non-time types only)
    "maxValidScore": "<number>",                    // Optional (non-time types only)
    "maxEntries": "<int>",                          // Optional
    "optionalLBExpiryDateTimeStamp": "<string>",    // Optional, ISO-8601
    "optionalLBReadOnlyOnExpiry": "<bool>",         // Optional, default: true (when expiry set)
    "description": "<string>",                      // Optional
    "tags": {}                                      // Optional, key-value pairs
  }
}
```

**Parameter Reference:**

Core properties:

| Parameter | Type | Required | Default |
|-----------|------|----------|---------|
| `gameID` | string | Yes | -- |
| `gameMode` | string | Yes | -- |
| `leaderboardName` | string | Yes | -- |
| `statAttributeForLeaderboard` | string | Yes | -- |
| `leaderboardType` | string | Yes | -- |
| `scoreStrategy` | string | Yes | -- |
| `scoreType` | string | No | `score` |
| `sortOrder` | string | No | -- |
| `timePrecision` | int | No | 3 |
| `timeFormat` | string | No | `seconds` |
| `minValidTimeInSeconds` | float | No | 0.001 |
| `maxValidTimeInSeconds` | float | No | 86400.0 |
| `minValidScore` | number | No | -- |
| `maxValidScore` | number | No | -- |
| `maxEntries` | int | No | -- |
| `optionalLBExpiryDateTimeStamp` | string | No | -- |
| `optionalLBReadOnlyOnExpiry` | bool | No | `true` (when expiry set) |
| `description` | string | No | -- |
| `tags` | object | No | -- |

Validation rules:

| Parameter | Valid Values | Constraints |
|-----------|-------------|-------------|
| `gameID` | -- | Non-empty, max 255 chars,<br>pattern: `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` |
| `gameMode` | -- | Non-empty |
| `leaderboardName` | -- | 3-64 chars, pattern: `^[a-zA-Z0-9][a-zA-Z0-9_-]*$`,<br>cannot start with `aws` or `amazon` |
| `statAttributeForLeaderboard` | -- | Non-empty |
| `leaderboardType` | `DESCENDING_LB`, `ASCENDING_LB` | -- |
| `scoreStrategy` | `replace`, `best`, `cumulative` | -- |
| `scoreType` | `score`, `time`, `distance`,<br>`points`, `rank`, `level` | -- |
| `sortOrder` | `asc`, `desc` | -- |
| `timePrecision` | 0-6 | Only for `time`/`rank` score types |
| `timeFormat` | `seconds`, `milliseconds`,<br>`minutes_seconds`,<br>`hours_minutes_seconds` | Only for `time`/`rank` score types |
| `minValidTimeInSeconds` | 0.0 - 86400.0 | Only for `time`/`rank`; must be >= 0 |
| `maxValidTimeInSeconds` | > minValidTimeInSeconds,<br><= 86400.0 | Only for `time`/`rank` |
| `minValidScore` | rank: >= 1, <= 1B;<br>others: >= 0, <= 1T | Non-time types only |
| `maxValidScore` | Must be > minValidScore;<br>rank: <= 1B; others: <= 1T | Non-time types only |
| `maxEntries` | positive, <= 1,000,000 | Maximum entries in the leaderboard |
| `optionalLBExpiryDateTimeStamp` | ISO-8601 datetime | Must be in the future |
| `optionalLBReadOnlyOnExpiry` | `true`, `false` | Behavior when leaderboard expires |
| `description` | -- | Free-text description |
| `tags` | -- | Key-value tags |

**Success Response (201):**

```json
{
  "gameLeaderboardConfigResponse": {
    "message": "Leaderboard configuration created successfully",
    "leaderboardConfig": {
      "leaderboardName": "HighScores",
      "gameID": "stellarodyssey",
      "gameMode": "campaign",
      "statAttributeForLeaderboard": "score",
      "leaderboardType": "DESCENDING_LB",
      "scoreStrategy": "best",
      "sortedListName": "stellarodyssey:campaign:HighScores",
      "createdAt": 1739923200,
      "createdAtISO": "2026-02-19T00:00:00Z",
      "studioId": "cosmicgames-a1b2c3d4",
      "gameId": "stellarodyssey-e5f6g7h8",
      "studioName": "Cosmic Games",
      "gameTitle": "Stellar Odyssey",
      "createdBy": "contact@cosmicgames.com",
      "scoreType": "score"
    },
    "potentialIssues": [],           // Only present when warnings exist
    "success": true,
    "processingTimeMs": 123
  }
}
```

> **Note:** Optional fields (`sortOrder`, `timePrecision`, `timeFormat`, `minValidScore`, `maxValidScore`, `minValidTimeInSeconds`, `maxValidTimeInSeconds`, `maxEntries`, `description`, `tags`, `optionalLBExpiryDateTimeStamp`, `optionalLBReadOnlyOnExpiry`) appear in the response only when explicitly provided in the request.

**Expiry Scheduling (conditional):** When `optionalLBExpiryDateTimeStamp` is provided, the response includes an `expiryScheduling` object:

```json
{
  "expiryScheduling": {
    "expiryDateTime": "2026-03-01T00:00:00+00:00",
    "readOnlyOnExpiry": true,
    "resetScheduled": false,
    "memoryDbTtlSet": false,
    "behavior": "read-only preservation",
    "explanation": {
      "memoryDbBehavior": "Data will be preserved in MemoryDB for continued read-only access after expiry",
      "applicationBehavior": "Write operations will be rejected after expiry, but leaderboard remains queryable",
      "dataRetention": "Historical data preserved in DynamoDB for analytics"
    }
  }
}
```

When `optionalLBReadOnlyOnExpiry` is `false`, `resetScheduled` and `memoryDbTtlSet` are `true`, `behavior` is `"auto-deletion on expiry"`, and explanation fields reflect that data will be removed from MemoryDB on expiry.

**Response Headers:**

| Header | Value |
|--------|-------|
| `Content-Type` | `application/json` |
| `Cache-Control` | `no-cache` (write ops) / `max-age=300` (GET 200) |
| `ETag` | hash (GET 200 only) |

**Example Request (minimal, required fields only):**

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "stellarodyssey",
    "gameMode": "campaign",
    "leaderboardName": "HighScores",
    "statAttributeForLeaderboard": "score",
    "leaderboardType": "DESCENDING_LB",
    "scoreStrategy": "best"
  }
}
```

**Configuration Samples by Game Type:**

<details>
<summary>High Score Leaderboard (Descending, Best)</summary>

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "stellarodyssey",
    "gameMode": "campaign",
    "leaderboardName": "CampaignHighScores",
    "statAttributeForLeaderboard": "score",
    "leaderboardType": "DESCENDING_LB",
    "scoreStrategy": "best",
    "scoreType": "score",
    "minValidScore": 0,
    "maxValidScore": 999999999,
    "description": "All-time highest campaign scores"
  }
}
```
</details>

<details>
<summary>Racing Time Leaderboard (Ascending, Best)</summary>

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "speedinators",
    "gameMode": "timeattack",
    "leaderboardName": "FastestLaps",
    "statAttributeForLeaderboard": "lapTime",
    "leaderboardType": "ASCENDING_LB",
    "scoreStrategy": "best",
    "scoreType": "time",
    "sortOrder": "asc",
    "timeFormat": "minutes_seconds",
    "timePrecision": 3,
    "minValidTimeInSeconds": 10.0,
    "maxValidTimeInSeconds": 600.0,
    "description": "Fastest lap times (lower is better)"
  }
}
```
</details>

<details>
<summary>Cumulative Points Leaderboard</summary>

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "stellarodyssey",
    "gameMode": "multiplayer",
    "leaderboardName": "TotalXP",
    "statAttributeForLeaderboard": "xpEarned",
    "leaderboardType": "DESCENDING_LB",
    "scoreStrategy": "cumulative",
    "scoreType": "points",
    "minValidScore": 0,
    "maxValidScore": 1000000000,
    "description": "Cumulative XP across all matches"
  }
}
```
</details>

<details>
<summary>Weekly Event Leaderboard with Expiry</summary>

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "stellarodyssey",
    "gameMode": "weekly-event",
    "leaderboardName": "WeeklyChallenge-2026-W08",
    "statAttributeForLeaderboard": "eventScore",
    "leaderboardType": "DESCENDING_LB",
    "scoreStrategy": "best",
    "scoreType": "score",
    "optionalLBExpiryDateTimeStamp": "2026-02-24T23:59:59Z",
    "optionalLBReadOnlyOnExpiry": true,
    "description": "Weekly challenge - Week 8, 2026"
  }
}
```
</details>

<details>
<summary>Replace-Strategy Leaderboard (Latest Score)</summary>

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "stellarodyssey",
    "gameMode": "daily",
    "leaderboardName": "DailyChallenge",
    "statAttributeForLeaderboard": "dailyScore",
    "leaderboardType": "DESCENDING_LB",
    "scoreStrategy": "replace",
    "scoreType": "score",
    "description": "Latest score always replaces the previous one"
  }
}
```
</details>

<details>
<summary>Distance Leaderboard (Descending, Best)</summary>

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "infiniterunner",
    "gameMode": "endless",
    "leaderboardName": "FarthestDistance",
    "statAttributeForLeaderboard": "distance",
    "leaderboardType": "DESCENDING_LB",
    "scoreStrategy": "best",
    "scoreType": "distance",
    "minValidScore": 0,
    "maxValidScore": 1000000,
    "description": "Farthest distance reached in endless mode"
  }
}
```
</details>

<details>
<summary>Rank-Based Leaderboard (Ascending)</summary>

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "chessgame",
    "gameMode": "ranked",
    "leaderboardName": "EloRatings",
    "statAttributeForLeaderboard": "eloRating",
    "leaderboardType": "ASCENDING_LB",
    "scoreStrategy": "replace",
    "scoreType": "rank",
    "sortOrder": "asc",
    "minValidScore": 1,
    "maxValidScore": 3000,
    "description": "ELO ratings - lower rank number is better"
  }
}
```
</details>

<details>
<summary>Level-Based Leaderboard (Descending, Best)</summary>

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "mmorpg-world",
    "gameMode": "adventure",
    "leaderboardName": "HighestLevel",
    "statAttributeForLeaderboard": "characterLevel",
    "leaderboardType": "DESCENDING_LB",
    "scoreStrategy": "best",
    "scoreType": "level",
    "minValidScore": 1,
    "maxValidScore": 100,
    "description": "Highest character level achieved"
  }
}
```
</details>

---

#### POST /leaderboards/config/get or GET /leaderboards/config - Get Single Configuration

**Request Template (POST):**

```json
{
  "gameLeaderboardConfigRequest": {
    "leaderboardName": "<string>"
  }
}
```

| Parameter | Type | Required | Constraints |
|-----------|------|----------|-------------|
| `leaderboardName` | string | Yes | Max 255 chars, pattern: `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` |

> **Note:** This endpoint requires the `leaderboardName` in the request body (via `gameLeaderboardConfigRequest`), not as a query parameter. Use `POST /leaderboards/config/get` or include a JSON body with the GET request.

**Example Request:**

```json
{
  "gameLeaderboardConfigRequest": {
    "leaderboardName": "HighScores"
  }
}
```

**Success Response (200):**

```json
{
  "gameLeaderboardConfigResponse": {
    "leaderboardConfig": {
      "leaderboardName": "HighScores",
      "gameID": "stellarodyssey",
      "gameMode": "campaign",
      "statAttributeForLeaderboard": "score",
      "leaderboardType": "DESCENDING_LB",
      "scoreStrategy": "best",
      "sortedListName": "stellarodyssey:campaign:HighScores",
      "createdAt": 1739923200,
      "createdAtISO": "2026-02-19T00:00:00Z",
      "scoreType": "score",
      "sortOrder": "desc"
    },
    "valkeyValidation": {
      "validated": true,
      "exists": true,
      "sortedListName": "stellarodyssey:campaign:HighScores",
      "entryCount": 1000,
      "minScore": 0.0,
      "maxScore": 99999.0
    },
    "metadata": {
      "leaderboardName": "HighScores",
      "valkeyValidated": true,
      "includeLeaderboardData": true,
      "timestamp": "2026-02-19T00:00:00Z"
    },
    "success": true
  }
}
```

---

#### GET/POST /leaderboards/configs or /leaderboards/config/all - Get All Configurations

**Request Template (POST):**

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "<string>",
    "gameMode": "<string>"       // Optional - omit to return all modes
  }
}
```

**Request Template (GET):**

```http
GET /leaderboards/configs?gameID=<string>&gameMode=<string>
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `gameID` | string | Yes | Game identifier |
| `gameMode` | string | No | Game mode filter; returns all modes if omitted |

**Example Requests:**

```json
{
  "gameLeaderboardConfigRequest": {
    "gameID": "stellarodyssey",
    "gameMode": "campaign"
  }
}
```

```http
GET /leaderboards/configs?gameID=stellarodyssey&gameMode=campaign
```

**Success Response (200):**

```json
{
  "gameLeaderboardConfigResponse": {
    "leaderboardConfigs": [
      {
        "leaderboardName": "HighScores",
        "gameID": "stellarodyssey",
        "gameMode": "campaign",
        "leaderboardType": "DESCENDING_LB",
        "scoreStrategy": "best",
        "valkeyExists": true,
        "valkeyMetadata": {
          "entryCount": 500,
          "keyExists": true,
          "maxPlayer": "player123",
          "maxScore": 99999.0,
          "minPlayer": "player456",
          "minScore": 10.0,
          "ttlSeconds": 86400,
          "isEmpty": false
        }
      }
    ],
    "metadata": {
      "count": 1,
      "gameID": "stellarodyssey",
      "gameMode": "campaign",
      "valkeyValidated": true,
      "includeLeaderboardData": true,
      "timestamp": "2026-02-19T00:00:00Z"
    },
    "success": true
  }
}
```

**Response Header:**

| Header | Value |
|--------|-------|
| `X-Total-Count` | Number of configs returned |

---

#### PUT /leaderboards/config/update - Update Configuration

Same request body and template as [POST /leaderboards/config/create](#post-leaderboardsconfigcreate---create-leaderboard). All required fields for create are also required for update.

If `gameID` or `gameMode` changes, a Valkey sorted set migration is performed (with backup and rollback support). Expired leaderboards cannot be updated.

**Success Response (200):**

```json
{
  "gameLeaderboardConfigResponse": {
    "message": "Leaderboard configuration updated successfully",
    "leaderboardConfig": { "...full updated config from DynamoDB..." },
    "updateResults": {
      "migrationRequired": false,
      "migrationPerformed": false,
      "backupCreated": false,
      "configUpdated": true,
      "rollbackPerformed": false
    },
    "metadata": {
      "leaderboardName": "weekly-kills",
      "oldSortedListName": "my-game:deathmatch:weekly-kills",
      "newSortedListName": "my-game:deathmatch:weekly-kills",
      "timestamp": "2026-02-19T14:35:22+00:00"
    },
    "success": true
  }
}
```

When `gameID` or `gameMode` changes (migration), additional conditional fields may appear:

- `migrationStats` — present when migration was performed: `{ "startTime", "oldListName", "newListName", "elementsToMigrate", "elementsMigrated", "batchesMigrated", "migrationDurationMs" }`
- `backup` — present when backup was created: `{ "created": true, "entryCount": <int>, "backupTimestamp": "<ISO datetime>" }`
- `expiryScheduling` — present when expiry is set (same structure as create response)

---

#### DELETE /leaderboards/config/delete - Delete Configuration

**Request Template (all parameters):**

```json
{
  "gameLeaderboardConfigRequest": {
    "leaderboardName": "<string>"
  }
}
```

| Parameter | Type | Required | Constraints |
|-----------|------|----------|-------------|
| `leaderboardName` | string | Yes | Max 255 chars, pattern: `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` |

**Example Request:**

```json
{
  "gameLeaderboardConfigRequest": {
    "leaderboardName": "HighScores"
  }
}
```

**Success Response (200):**

```json
{
  "gameLeaderboardConfigResponse": {
    "message": "Leaderboard configuration deleted successfully",
    "leaderboardConfig": { "...deleted config..." },
    "backup": {
      "created": true,
      "entryCount": 100,
      "backupTimestamp": "2026-02-19T00:00:00Z"
    },
    "deletedConfiguration": {
      "gameID": "stellarodyssey",
      "gameMode": "campaign",
      "leaderboardType": "DESCENDING_LB",
      "createdAt": "2026-02-19T00:00:00Z"
    },
    "success": true
  }
}
```

---

#### Leaderboard Config Error Responses (All Operations)

| Status | Error | Condition |
|--------|-------|-----------|
| 400 | Bad Request | Missing/invalid fields, duplicate name, expired leaderboard update |
| 401 | Unauthorized | Auth failure, gameID mismatch |
| 403 | AccessDeniedException | IAM permission |
| 404 | Not Found | Leaderboard not found |
| 409 | Conflict | Leaderboard name already exists (ConditionalCheckFailedException), lock contention |
| 429 | Throttling | DynamoDB throughput exceeded |
| 503 | Service Unavailable | Valkey connection error |
| 500 | Internal Server Error | Unexpected errors |

---

### 2.3 Batch Store Stats and Scores

For multiplayer game servers to submit multiple player reports in one request. This is typically called from the game server at the end of a match to submit all participating players' results in a single API call.

#### POST (Batch Store)

**Request:**

```http
POST /leaderboards/stats/batch
Content-Type: application/json
Authorization: Bearer <api_key>
```

**Request Template (all parameters per report item):**

```json
{
  "batchGameReportBody": {
    "gameReports": [
      {
        "playerID": "<string>",
        "gameID": "<string>",
        "gameMode": "<string>",
        "playerScore": "<number or time string>",
        "leaderboardName": "<string>",
        "fullRawGameReport": {}
      }
    ]
  }
}
```

**Parameter Reference:**

| Parameter | Type | Required | Constraints |
|-----------|------|----------|-------------|
| `gameReports` | array | Yes | Non-empty,<br>default max 1000 items (configurable via `MAX_ITEMS_PER_REQUEST` env var; increase alongside Lambda memory and timeout for larger batches) |
| `gameReports[].playerID` | string | Yes | Non-empty,<br>pattern: `^[a-zA-Z0-9_-]+$` |
| `gameReports[].gameID` | string | Yes | Must match<br>leaderboard config |
| `gameReports[].gameMode` | string | Yes | Must match<br>leaderboard config |
| `gameReports[].playerScore` | number/string | Yes | Validated against<br>leaderboard config bounds |
| `gameReports[].leaderboardName` | string | Yes | Non-empty,<br>pattern: `^[a-zA-Z0-9_-]+$` |
| `gameReports[].fullRawGameReport` | object | Yes | Must be a JSON object |

**Critical Rules:**
- Each `playerID` can appear **ONLY ONCE** per batch (regardless of leaderboard/game mode). Duplicates cause immediate rejection with HTTP 423.
- `scoreStrategy` in individual reports is **IGNORED** -- always uses the leaderboard config value.
- `gameID` and `gameMode` must match the leaderboard configuration.

**Example Request (multiple players):**

```json
{
  "batchGameReportBody": {
    "gameReports": [
      {
        "playerID": "player001",
        "gameID": "stellarodyssey",
        "gameMode": "campaign",
        "playerScore": 15000,
        "leaderboardName": "HighScores",
        "fullRawGameReport": {
          "kills": 42,
          "deaths": 3,
          "assists": 15,
          "matchDuration": 1200
        }
      },
      {
        "playerID": "player002",
        "gameID": "stellarodyssey",
        "gameMode": "campaign",
        "playerScore": 12500,
        "leaderboardName": "HighScores",
        "fullRawGameReport": {
          "kills": 35,
          "deaths": 5,
          "assists": 20,
          "matchDuration": 1200
        }
      }
    ]
  }
}
```

**Time Score Formats Accepted:**

| Format | Example | Interpretation |
|--------|---------|---------------|
| Numeric (int/float) | `125.456` | Seconds (or milliseconds if config `timeFormat` = `milliseconds`) |
| String `MM:SS.mmm` | `"2:05.456"` | 2 minutes, 5.456 seconds = 125.456s |
| String `HH:MM:SS.mmm` | `"1:02:05.456"` | 1 hour, 2 minutes, 5.456 seconds |
| Plain numeric string | `"125.456"` | Parsed as float |

**Success Response (200):**

```json
{
  "batchGameReportResponse": {
    "message": "Batch processing completed",
    "jobId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "summary": {
      "totalItems": 2,
      "processedItems": 2,
      "validationErrors": 0,
      "statsStorage": {
        "successful": 2,
        "failed": 0
      },
      "leaderboardUpdates": {
        "successful": 2,
        "failed": 0,
        "timeBasedUpdates": 0
      }
    },
    "performance": {
      "processingTimeMs": 250,
      "itemsPerSecond": 8.0
    },
    "metadata": {
      "studioId": "cosmicgames-a1b2c3d4",
      "gameId": "stellarodyssey-e5f6g7h8",
      "requestId": "req-abc123",
      "timestamp": "2026-02-19T00:00:00+00:00"
    },
    "errors": [],                              // Only present when validation errors occur
    "success": true
  }
}
```

**Batch Time Score Sample:**

```json
{
  "batchGameReportBody": {
    "gameReports": [
      {
        "playerID": "racer001",
        "gameID": "speedinators",
        "gameMode": "timeattack",
        "playerScore": "1:23.456",
        "leaderboardName": "FastestLaps",
        "fullRawGameReport": {
          "track": "moonbase-circuit",
          "vehicle": "hyperion-mk3",
          "laps": 3
        }
      }
    ]
  }
}
```

**Error Responses:**

| Status | Error | Condition |
|--------|-------|-----------|
| 400 | Bad Request | Missing body, invalid JSON, missing fields, empty array, exceeded max items, validation failures |
| 401 | Unauthorized | Auth failure |
| 404 | Not Found | Leaderboard config not found |
| 423 | LEADERBOARD_EXPIRED_READONLY | Expired leaderboard in read-only mode |
| 423 | LEADERBOARD_EXPIRED_AUTODELETE | Expired leaderboard scheduled for deletion |
| 423 | DUPLICATE_PLAYERS_IN_BATCH | Same playerID appears more than once |
| 429 | Throttling | DynamoDB throughput exceeded |
| 503 | Service Unavailable | Valkey connection error |
| 500 | Internal Server Error | Unexpected errors |

**Example Error Response (423 -- Duplicate Players):**

```json
{
  "batchGameReportResponse": {
    "error": "Duplicate Players Detected",
    "errorCode": "DUPLICATE_PLAYERS_IN_BATCH",
    "message": "Batch request contains duplicate player IDs",
    "details": "Duplicate playerID 'player001' detected in batch at index 3. Each player can only appear once per batch request, regardless of leaderboard or game mode.",
    "timestamp": "2026-02-19T00:00:00Z"
  }
}
```

---

### 2.4 Reset Leaderboard

Resets (clears) all scores from a leaderboard's live rankings while preserving the leaderboard configuration and all submitted player stats. Only the Valkey sorted set (the ranked leaderboard) is emptied -- the DynamoDB config table and player stats table are untouched. This means you can rebuild the leaderboard from the historical stats at any time after a reset.

> **Warning:** This is a destructive operation that removes all player rankings from the live leaderboard. A backup of the sorted set is created by default (stored in Valkey with a 24-hour TTL for manual resets, 30 days for scheduled expiry resets), but the backup is not automatically restorable through the API. Ensure you have confirmed the operation is intentional before setting `confirmReset: true`. For large leaderboards, the operation may span multiple Lambda invocations via self-invoke relay.

#### POST /leaderboards/admin/reset

**Request:**

```http
POST /leaderboards/admin/reset
Content-Type: application/json
Authorization: Bearer <api_key>
```

**Request Template (all parameters):**

```json
{
  "resetLeaderboardRequest": {
    "leaderboardName": "<string>",
    "confirmReset": "<bool>",       // Optional, default: false. Must be true to proceed.
    "createBackup": "<bool>"        // Optional, default: true
  }
}
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `leaderboardName` | string | Yes | -- | Pattern: `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` |
| `confirmReset` | bool | No | `false` | Must be `true` to proceed |
| `createBackup` | bool | No | `true` | Whether to backup before reset |

**Example Request:**

```json
{
  "resetLeaderboardRequest": {
    "leaderboardName": "HighScores",
    "confirmReset": true,
    "createBackup": true
  }
}
```

**Without Confirmation (400):**

```json
{
  "resetLeaderboardResponse": {
    "error": "Confirmation Required",
    "message": "Please set confirmReset to true to confirm this operation",
    "warning": "This will clear all scores from the leaderboard",
    "leaderboardName": "HighScores"
  }
}
```

**Success Response (200):**

```json
{
  "resetLeaderboardResponse": {
    "message": "Leaderboard reset completed successfully",
    "resetResults": {
      "leaderboardName": "HighScores",
      "operationId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "sortedListName": "stellarodyssey:campaign:HighScores",
      "scoreType": "score",
      "isScheduledExpiry": false,
      "isContinuation": false,
      "lockAcquired": true,
      "backupCreated": true,
      "resetCompleted": true,
      "backupData": {
        "sortedListName": "stellarodyssey:campaign:HighScores",
        "entryCount": 500,
        "backupTimestamp": "2026-02-19T00:00:00Z",
        "backupType": "manual_reset"
      },
      "resetInfo": {
        "entriesRemoved": 500,
        "resetTimestampISO": "2026-02-19T00:00:00Z",
        "scoreType": "score",
        "initPlaceholder": "_init_topscore_",
        "initPlaceholderValue": -999999.0
      }
    },
    "metadata": {
      "timestamp": "2026-02-19T14:35:22+00:00",
      "requestId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "processingTimeMs": 150,
      "executionType": "manual_reset",
      "studioId": "cosmic-games-a1b2c3",
      "gameId": "stellarodyssey-e5f6g7h8"
    },
    "success": true
  }
}
```

**In-Progress (202)** -- for large leaderboards (Lambda relay):

```json
{
  "resetLeaderboardResponse": {
    "message": "Reset operation in progress",
    "operationId": "a1b2c3d4-...",
    "phase": "reset_in_progress",
    "processedEntries": 5000,
    "totalEntries": 20000,
    "isComplete": false,
    "continuationTriggered": true
  }
}
```

**Init Placeholder Values by Score Type:**

| Score Type | Placeholder Key | Placeholder Value |
|-----------|----------------|-------------------|
| `score` | `_init_topscore_` | -999999.0 |
| `time` | `_init_time_` | 999999999.0 |
| `distance` | `_init_distance_` | -1.0 |
| `points` | `_init_points_` | -999999.0 |
| `rank` | `_init_rank_` | 999999999.0 |
| `level` | `_init_level_` | -1.0 |

**Backup Retention:**

| Backup Type | Default TTL |
|-------------|-------------|
| Manual reset | 24 hours (`BACKUP_TTL_HOURS`) |
| Scheduled expiry | 30 days (`SCHEDULED_BACKUP_TTL_DAYS`) |

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 400 | Missing fields, `confirmReset` not set |
| 401 | Auth failure |
| 404 | Leaderboard not found |
| 405 | Non-POST method (returns `Allow: POST` header) |
| 409 | Another reset already in progress |
| 503 | Valkey connection error |

---

### 2.5 Rebuild Leaderboard

Rebuilds the leaderboard by clearing the current Valkey sorted set and repopulating it from the player stats stored in DynamoDB. The existing leaderboard is backed up before clearing. You can optionally filter by time range and override the score strategy during rebuild.

> **Warning:** Rebuild replaces the entire live leaderboard. During the rebuild, score updates are paused (via a distributed lock) to prevent data inconsistency. For large stat histories (>100K items), the operation may take several minutes and span multiple Lambda invocations via self-invoke relay. A backup of the pre-rebuild leaderboard is created automatically. Use the optional `startTimestamp`/`endTimestamp` filters to rebuild from a specific time window, or `scoreStrategy` to recalculate with a different strategy (e.g., rebuild a "best" leaderboard as "cumulative").

#### POST /leaderboards/admin/rebuild

**Request:**

```http
POST /leaderboards/admin/rebuild
Content-Type: application/json
Authorization: Bearer <api_key>
```

**Request Template (all parameters):**

```json
{
  "rebuildLeaderboardRequest": {
    "leaderboardName": "<string>",
    "confirmRebuild": "<bool>",          // Optional, default: false. Must be true to proceed.
    "scoreStrategy": "<string>",         // Optional, default: from leaderboard config
    "startTimestamp": "<string or int>",  // Optional, ISO-8601 or Unix timestamp
    "endTimestamp": "<string or int>"     // Optional, ISO-8601 or Unix timestamp
  }
}
```

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `leaderboardName` | string | Yes | -- | Pattern: `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` |
| `confirmRebuild` | bool | No | `false` | Must be `true` to proceed |
| `scoreStrategy` | string | No | From config | Override: `replace`, `best`, `cumulative` |
| `startTimestamp` | string/int | No | -- | ISO-8601 or Unix timestamp; filter stats from this time |
| `endTimestamp` | string/int | No | -- | ISO-8601 or Unix timestamp; must be > startTimestamp |

**Example Request:**

```json
{
  "rebuildLeaderboardRequest": {
    "leaderboardName": "HighScores",
    "confirmRebuild": true,
    "scoreStrategy": "best",
    "startTimestamp": "2026-01-01T00:00:00Z",
    "endTimestamp": "2026-02-19T00:00:00Z"
  }
}
```

**Success Response (200):**

```json
{
  "rebuildLeaderboardResponse": {
    "message": "Leaderboard rebuild completed successfully",
    "rebuildResults": {
      "leaderboardName": "HighScores",
      "jobId": "a1b2c3d4-...",
      "sortedListName": "stellarodyssey:campaign:HighScores",
      "leaderboardType": "DESCENDING_LB",
      "scoreType": "score",
      "scoreStrategy": "best",
      "lockAcquired": true,
      "updatesPaused": true,
      "backupCreated": true,
      "backupEntryCount": 500,
      "leaderboardReset": true,
      "entriesRemoved": 500,
      "processedItems": 2500,
      "batchesProcessed": 5,
      "finalLeaderboardSize": 2500,
      "errors": [],
      "errorCount": 0,
      "phase": "completed",
      "success": true
    },
    "metadata": {
      "timestamp": "2026-02-19T14:35:22+00:00",
      "requestId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "processingTimeMs": 5000,
      "studioId": "cosmic-games-a1b2c3",
      "gameId": "stellarodyssey-e5f6g7h8"
    },
    "success": true
  }
}
```

**In-Progress Response (202):**

For large leaderboards, the rebuild may span multiple Lambda invocations via self-invoke relay. The initial request returns 202 while processing continues asynchronously:

```json
{
  "rebuildLeaderboardResponse": {
    "message": "Leaderboard rebuild in progress",
    "leaderboardName": "weekly-kills",
    "jobId": "rebuild-abc123",
    "processedItems": 5000,
    "batchesProcessed": 10,
    "isComplete": false,
    "phase": "processing",
    "executionTimeSeconds": 45
  }
}
```

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 400 | Missing fields, `confirmRebuild` not set, invalid timestamps |
| 401 | Auth failure, game ID mismatch |
| 404 | Leaderboard not found |
| 405 | Non-POST method (returns `Allow: POST` header) |
| 409 | Another rebuild already in progress |
| 423 | LEADERBOARD_EXPIRED_READONLY — expired leaderboard in read-only mode |
| 423 | LEADERBOARD_EXPIRED_AUTODELETE — expired leaderboard scheduled for deletion |
| 503 | Valkey connection error |

> **Note:** Expired leaderboards cannot be rebuilt. If a leaderboard has passed its `optionalLBExpiryDateTimeStamp`, the rebuild will be rejected with HTTP 423. This prevents accidentally repopulating a leaderboard that has been intentionally expired. If you need to rebuild an expired leaderboard, first remove or extend the expiry via a config update, then retry the rebuild.

---

## 3. Player APIs

### Invocation Flow

Player APIs are designed to be called from the game client or game server on behalf of individual players. These APIs handle individual score submissions and player-specific queries.

> **Authentication Note:** Player routes are authenticated by the player authorizer. In identity mode (the default) it validates Custom Identity Component access tokens; in custom mode you supply your own validation. See [Section 1.5](#15-integrating-player-authentication) for details.

All player APIs currently require authentication. Store operations require `write` permission; query operations require `read` permission.

```
Player / Game Client
    |
    +-- Authorization: Bearer <player_access_token>   (Custom Identity Component token; or your own in custom mode)
    |
    v
API Gateway --> Lambda Authorizer --> Player Lambda
                                          |
                +-------------------------+-------------------------+
                |                         |                         |
   storePlayerStatsAndScores   getLeaderboardScores      getPlayerLBStanding
                |                         |                         |
                v                         v                         v
     +-------------------+      +------------------+     +------------------+
     | 1. Validate input |      | Query Valkey     |     | Query Valkey     |
     | 2. Store to DDB   |      | sorted set       |     | rank + score     |
     | 3. Update Valkey  |      | (top/range/      |     | + percentile     |
     |    sorted set     |      |  around/player)  |     | + neighbours     |
     +-------------------+      +------------------+     +------------------+
                |
                v
     getPlayerStatsAndScores
                |
                v
     +-------------------+
     | Query DynamoDB    |
     | stats history     |
     | (with filters)    |
     +-------------------+
```

---

### 3.1 Store Player Stats and Scores

Submits a single player's game report.

#### POST (Store Stats)

**Request:**

```http
POST /leaderboards/stats
Content-Type: application/json
Authorization: Bearer <player_access_token>
```

**Request Template (all parameters):**

```json
{
  "gameReportBody": {
    "playerID": "<string>",
    "gameID": "<string>",
    "gameMode": "<string>",
    "playerScore": "<number or time string>",
    "leaderboardName": "<string>",
    "fullRawGameReport": {}
  }
}
```

| Parameter | Type | Required | Constraints |
|-----------|------|----------|-------------|
| `playerID` | string | Yes | Non-empty, pattern: `^[a-zA-Z0-9_-]+$` |
| `gameID` | string | Yes | Non-empty, must match leaderboard config |
| `gameMode` | string | Yes | Non-empty, must match leaderboard config |
| `playerScore` | number/string | Yes | Non-negative; validated against config bounds |
| `leaderboardName` | string | Yes | Non-empty, pattern: `^[a-zA-Z0-9_-]+$` |
| `fullRawGameReport` | object | Yes | Must be a JSON object (game-specific data, stored as-is) |
| `scoreStrategy` | any | Ignored | Deleted from report; strategy comes from leaderboard config |

**Example Request:**

```json
{
  "gameReportBody": {
    "playerID": "player001",
    "gameID": "stellarodyssey",
    "gameMode": "campaign",
    "playerScore": 15000,
    "leaderboardName": "HighScores",
    "fullRawGameReport": {
      "kills": 42,
      "deaths": 3,
      "assists": 15,
      "matchDuration": 1200,
      "level": "moon-base-alpha"
    }
  }
}
```

**Example Request (time-based leaderboard):**

```json
{
  "gameReportBody": {
    "playerID": "racer001",
    "gameID": "speedinators",
    "gameMode": "timeattack",
    "playerScore": "1:23.456",
    "leaderboardName": "FastestLaps",
    "fullRawGameReport": {
      "track": "moonbase-circuit",
      "vehicle": "hyperion-mk3",
      "laps": 3,
      "bestSector": 24.8
    }
  }
}
```

**Score Validation:**

- Non-time scores: must be >= 0; must be within `minValidScore`..`maxValidScore` if configured
- Time scores: must be within `minValidTimeInSeconds`..`maxValidTimeInSeconds` (defaults 0.001..86400.0)
- Time string formats: `MM:SS.mmm`, `HH:MM:SS.mmm`, or plain numeric

**Success Response (200):**

```json
{
  "gameReportResponse": {
    "message": "Game stats stored and leaderboard updated successfully",
    "leaderboardName": "HighScores",
    "playerID": "player001",
    "playerScore": 15000.0,
    "metadata": {
      "studioId": "cosmicgames-a1b2c3d4",
      "gameId": "stellarodyssey-e5f6g7h8",
      "requestId": "req-abc123",
      "timestamp": "2026-02-19T00:00:00Z"
    },
    "performance": {
      "processingTimeMs": 45
    },
    "success": true,
    "sortKey": "my-game#campaign#2026-02-19T14:35:22.157+00:00",
    "timestamp": "2026-02-19T00:00:00Z",
    "leaderboardType": "DESCENDING_LB",
    "scoreStrategy": "best",
    "sortedListName": "stellarodyssey:campaign:HighScores"
  }
}
```

**Time Score Success Response (200):**

```json
{
  "gameReportResponse": {
    "message": "Game stats stored and leaderboard updated successfully",
    "leaderboardName": "FastestLaps",
    "playerID": "racer001",
    "playerScore": 83.456,
    "success": true,
    "leaderboardType": "ASCENDING_LB",
    "scoreStrategy": "best",
    "timeFormatted": "83.456s",
    "scoreType": "time",
    "timePrecision": 3,
    "timeFormat": "seconds"
  }
}
```

**Error Responses:**

| Status | Error | Condition |
|--------|-------|-----------|
| 400 | Bad Request | Missing body, invalid JSON, missing fields, invalid characters, invalid score, score out of range, gameID/gameMode mismatch |
| 401 | Unauthorized | No authorizer context, missing studioId/gameId, no `write` permission |
| 403 | Forbidden | Request `playerID` does not match the authenticated player (`PLAYER_ID_MISMATCH`) |
| 403 | AccessDeniedException | IAM permission error |
| 404 | Not Found | Leaderboard config not found |
| 423 | LEADERBOARD_EXPIRED_READONLY | Expired leaderboard in read-only mode |
| 423 | LEADERBOARD_EXPIRED_AUTODELETE | Expired leaderboard scheduled for deletion |
| 429 | Throttling | DynamoDB throughput exceeded |
| 500 | Internal Server Error | Unexpected error |
| 503 | Service Unavailable | Valkey-GLIDE not available or connection error |

---

### 3.2 Get Leaderboard Scores

Queries leaderboard data with four query types.

#### POST (Query Scores)

**Request:**

```http
POST /leaderboards/scores
Content-Type: application/json
Authorization: Bearer <player_access_token>
```

---

##### Query Type: `top` (default)

Returns the top-ranked players.

**Request Template (all parameters):**

```json
{
  "leaderboardScoresRequest": {
    "leaderboardName": "<string>",
    "queryType": "top",
    "pageSize": "<int>",          // Optional, default: 10, max: 500
    "limit": "<int>",             // Optional, alias for pageSize
    "nextToken": "<string>",      // Optional, base64-encoded pagination token
    "offset": "<int>"             // Optional, alternative to nextToken
  }
}
```

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `leaderboardName` | string | Yes | -- | Pattern: `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` |
| `queryType` | string | No | `"top"` | `top`, `range`, `aroundPlayer`, `playerScore` |
| `pageSize` | int | No | 10 | 1-500 |
| `limit` | int | No | 10 | Alias for `pageSize` |
| `nextToken` | string | No | -- | Base64-encoded JSON for pagination |
| `offset` | int | No | -- | Non-negative; alternative to nextToken for `top` queries |

**Example Request:**

```json
{
  "leaderboardScoresRequest": {
    "leaderboardName": "HighScores",
    "queryType": "top",
    "pageSize": 10
  }
}
```

**Response (200):**

```json
{
  "leaderboardScoresResponse": {
    "leaderboardName": "HighScores",
    "leaderboardType": "DESCENDING_LB",
    "queryType": "top",
    "scores": [
      { "playerID": "player042", "score": 99500, "rank": 1 },
      { "playerID": "player017", "score": 98200, "rank": 2 },
      { "playerID": "player001", "score": 95000, "rank": 3 }
    ],
    "scoreType": "score",                   // Only present when leaderboard config has scoreType explicitly set
    "metadata": {
      "totalPlayers": 5000,
      "scoresCount": 3,
      "pageSize": 10,
      "hasMoreResults": true,
      "nextToken": "eyJzdGFydEluZGV4IjogMTB9"
    },
    "success": true,
    "processingTimeMs": 12
  }
}
```

> **Time-based leaderboards:** When `scoreType` is `"time"`, the response also includes `timeFormat` and `timePrecision` fields alongside `scoreType`, and score values are formatted according to the leaderboard's `timeFormat` configuration (see [Time-Based Score Display](#time-based-leaderboard-score-display)). These three fields (`scoreType`, `timeFormat`, `timePrecision`) appear in responses for all query types (`top`, `range`, `aroundPlayer`, `playerScore`).

**Response Headers:**

| Header | Value |
|--------|-------|
| `Content-Type` | `application/json` |
| `Cache-Control` | `max-age=5` |
| `X-Request-Id` | Lambda request ID |
| `X-Total-Players` | Total player count (present for `top`, `aroundPlayer`, and `playerScore` queries; not included for `range` queries) |

---

##### Query Type: `range`

Returns players within a score range.

**Request Template (all parameters):**

```json
{
  "leaderboardScoresRequest": {
    "leaderboardName": "<string>",
    "queryType": "range",
    "minScore": "<number>",
    "maxScore": "<number>",
    "inclusive": "<bool>",        // Optional, default: true
    "pageSize": "<int>",          // Optional, default: 10, max: 500
    "nextToken": "<string>"       // Optional, base64-encoded pagination token
  }
}
```

| Parameter | Type | Required | Constraints |
|-----------|------|----------|-------------|
| `minScore` | number | Yes (for range) | abs value <= 1e15; must be <= maxScore |
| `maxScore` | number | Yes (for range) | abs value <= 1e15 |
| `inclusive` | bool | No | Default: `true` |

**Example Request:**

```json
{
  "leaderboardScoresRequest": {
    "leaderboardName": "HighScores",
    "queryType": "range",
    "minScore": 5000,
    "maxScore": 10000,
    "inclusive": true,
    "pageSize": 20
  }
}
```

**Response (200):**

```json
{
  "leaderboardScoresResponse": {
    "leaderboardName": "HighScores",
    "leaderboardType": "DESCENDING_LB",
    "queryType": "range",
    "queryParameters": {
      "minScore": 5000.0,
      "maxScore": 10000.0,
      "inclusive": true
    },
    "scores": [
      { "playerID": "player088", "score": 9800, "rank": 52 },
      { "playerID": "player033", "score": 8500, "rank": 53 }
    ],
    "metadata": {
      "totalInRange": 150,
      "scoresCount": 2,
      "pageSize": 20,
      "hasMoreResults": true,
      "nextToken": "eyJvZmZzZXQiOiAyMH0="
    },
    "success": true,
    "processingTimeMs": 15
  }
}
```

---

##### Query Type: `aroundPlayer`

Returns players around a specific player's position.

**Request Template (all parameters):**

```json
{
  "leaderboardScoresRequest": {
    "leaderboardName": "<string>",
    "queryType": "aroundPlayer",
    "playerID": "<string>",
    "countBefore": "<int>",       // Optional, default: 5
    "countAfter": "<int>"         // Optional, default: 5
  }
}
```

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `playerID` | string | Yes (for aroundPlayer) | -- | Pattern: `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` |
| `countBefore` | int | No | 5 | Non-negative; `countBefore + countAfter + 1` <= 500 |
| `countAfter` | int | No | 5 | Non-negative |

**Example Request:**

```json
{
  "leaderboardScoresRequest": {
    "leaderboardName": "HighScores",
    "queryType": "aroundPlayer",
    "playerID": "player001",
    "countBefore": 5,
    "countAfter": 5
  }
}
```

**Response (200):**

```json
{
  "leaderboardScoresResponse": {
    "leaderboardName": "HighScores",
    "leaderboardType": "DESCENDING_LB",
    "queryType": "aroundPlayer",
    "targetPlayer": {
      "playerID": "player001",
      "rank": 150,
      "score": 15000
    },
    "scores": [
      { "playerID": "player088", "score": 15200, "rank": 148 },
      { "playerID": "player033", "score": 15100, "rank": 149 },
      { "playerID": "player001", "score": 15000, "rank": 150 },
      { "playerID": "player055", "score": 14900, "rank": 151 },
      { "playerID": "player077", "score": 14800, "rank": 152 }
    ],
    "metadata": {
      "totalPlayers": 5000,
      "scoresCount": 5,
      "countBefore": 5,
      "countAfter": 5
    },
    "success": true,
    "processingTimeMs": 18
  }
}
```

---

##### Query Type: `playerScore`

Returns a single player's score, rank, and percentile.

**Request Template (all parameters):**

```json
{
  "leaderboardScoresRequest": {
    "leaderboardName": "<string>",
    "queryType": "playerScore",
    "playerID": "<string>"
  }
}
```

| Parameter | Type | Required |
|-----------|------|----------|
| `playerID` | string | Yes (for playerScore) |

**Example Request:**

```json
{
  "leaderboardScoresRequest": {
    "leaderboardName": "HighScores",
    "queryType": "playerScore",
    "playerID": "player001"
  }
}
```

**Response (200):**

```json
{
  "leaderboardScoresResponse": {
    "leaderboardName": "HighScores",
    "leaderboardType": "DESCENDING_LB",
    "queryType": "playerScore",
    "playerData": {
      "playerID": "player001",
      "score": 15000,
      "rank": 150,
      "totalPlayers": 5000,
      "percentile": 97.0
    },
    "metadata": {
      "totalPlayers": 5000
    },
    "success": true,
    "processingTimeMs": 8
  }
}
```

**Percentile formula:** `round((1 - (rank / total_count)) * 100, 2)` where rank is 0-based.

---

##### Time-Based Leaderboard Score Display

For leaderboards with `scoreType: "time"`, scores are formatted based on `timeFormat`:

| `timeFormat` | Raw Score | Displayed Score |
|-------------|-----------|----------------|
| `seconds` | 83.456 | `83.456` |
| `milliseconds` | 83.456 | `83456.0` (multiplied by 1000) |
| `minutes_seconds` | 83.456 | `"1:23.456"` |
| `hours_minutes_seconds` | 3723.456 | `"1:02:03.456"` |

---

##### Pagination

| Query Type | Pagination Support | nextToken Format |
|-----------|-------------------|-----------------|
| `top` | Yes | `{"startIndex": <int>}` (base64) |
| `range` | Yes | `{"offset": <int>}` (base64) |
| `aroundPlayer` | No | -- |
| `playerScore` | No | -- |

For `top` queries, the `offset` parameter is an alternative to `nextToken`.

**Pagination Example -- Fetching Page 2 with `nextToken`:**

Use the `nextToken` value from the page 1 response to request the next page:

```json
{
  "leaderboardScoresRequest": {
    "leaderboardName": "HighScores",
    "queryType": "top",
    "pageSize": 10,
    "nextToken": "eyJzdGFydEluZGV4IjogMTB9"
  }
}
```

**Pagination Example -- Using `offset` (alternative for `top` queries only):**

Instead of `nextToken`, you can use `offset` to jump to a specific position:

```json
{
  "leaderboardScoresRequest": {
    "leaderboardName": "HighScores",
    "queryType": "top",
    "pageSize": 10,
    "offset": 20
  }
}
```

This returns players ranked 21-30 (offset is 0-based).

---

##### Empty Leaderboard Response

```json
{
  "leaderboardScoresResponse": {
    "leaderboardName": "HighScores",
    "leaderboardType": "DESCENDING_LB",
    "queryType": "top",
    "scores": [],
    "metadata": {
      "totalPlayers": 0,
      "scoresCount": 0,
      "message": "Leaderboard is empty"
    },
    "success": true
  }
}
```

---

##### Error Responses

| Status | Condition |
|--------|-----------|
| 400 | Missing body, invalid queryType, invalid pageSize, missing params for range/aroundPlayer/playerScore, player not found |
| 401 | Auth failure, no `read` permission |
| 404 | Leaderboard config not found |
| 429 | Throttling |
| 500 | Internal error |
| 503 | Valkey unavailable |

---

### 3.3 Get Player Stats and Scores

Retrieves a player's game stats history from DynamoDB.

#### POST (Query Stats)

**Request:**

```http
POST /leaderboards/player/stats
Content-Type: application/json
Authorization: Bearer <player_access_token>
```

**Request Template (all parameters):**

```json
{
  "playerStatsAndScoresRequest": {
    "playerID": "<string>",
    "gameID": "<string>",
    "gameMode": "<string>",
    "startTimestamp": "<string or int>",    // Optional, ISO-8601 or Unix timestamp
    "endTimestamp": "<string or int>",      // Optional, must be > startTimestamp
    "leaderboardName": "<string>",         // Optional, filter to specific leaderboard
    "limit": "<int>",                      // Optional, default: 50, max: 100
    "lastEvaluatedKey": {}                 // Optional, DynamoDB pagination key
  }
}
```

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `playerID` | string | Yes | -- | Pattern: `^[a-zA-Z0-9_-]+$` |
| `gameID` | string | Yes | -- | Pattern: `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` |
| `gameMode` | string | Yes | -- | Pattern: `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` |
| `startTimestamp` | int/string | No | -- | ISO-8601 or Unix timestamp |
| `endTimestamp` | int/string | No | -- | Must be > startTimestamp |
| `leaderboardName` | string | No | -- | Filter to specific leaderboard |
| `limit` | int | No | 50 | Max: 100 |
| `lastEvaluatedKey` | object | No | -- | DynamoDB pagination key |

**Example Request (basic):**

```json
{
  "playerStatsAndScoresRequest": {
    "playerID": "player001",
    "gameID": "stellarodyssey",
    "gameMode": "campaign",
    "limit": 50
  }
}
```

**Example Request (with time filter):**

```json
{
  "playerStatsAndScoresRequest": {
    "playerID": "player001",
    "gameID": "stellarodyssey",
    "gameMode": "campaign",
    "startTimestamp": "2026-02-01T00:00:00Z",
    "endTimestamp": "2026-02-19T00:00:00Z",
    "leaderboardName": "HighScores",
    "limit": 25
  }
}
```

**Success Response (200):**

```json
{
  "playerStatsAndScoresResponse": {
    "playerID": "player001",
    "gameID": "stellarodyssey",
    "gameMode": "campaign",
    "filters": {
      "startTimestamp": 1738368000,
      "endTimestamp": 1739923200,
      "startTimestampISO": "2026-02-01T00:00:00Z",
      "endTimestampISO": "2026-02-19T00:00:00Z",
      "leaderboardName": "HighScores",
      "limit": 25
    },
    "results": {
      "totalRecords": 3,
      "scannedRecords": 3,
      "playerStats": [
        {
          "playerID": "player001",
          "sortKey": "stellarodyssey#campaign#2026-02-18T12:00:00Z",
          "gameID": "stellarodyssey",
          "gameMode": "campaign",
          "playerScore": 15000,
          "leaderboardName": "HighScores",
          "fullRawGameReport": {
            "kills": 42,
            "deaths": 3,
            "assists": 15,
            "matchDuration": 1200
          },
          "timestamp": 1739880000,
          "timestampISO": "2026-02-18T12:00:00Z",
          "scoreStrategy": "best"
        }
      ],
      "statsSummary": {
        "totalRecords": 3,
        "dateRange": {
          "earliest": 1738500000.0,
          "latest": 1739880000.0,
          "earliestISO": "2026-02-02T12:00:00Z",
          "latestISO": "2026-02-18T12:00:00Z"
        },
        "scoreRange": {
          "minimum": 8000.0,
          "maximum": 15000.0,
          "average": 12000.0
        },
        "leaderboards": ["HighScores"]
      },
      "hasMoreResults": false
    },
    "pagination": {
      "lastEvaluatedKey": {
        "playerID": "player001",
        "sortKey": "stellarodyssey#campaign#2026-02-15T08:30:00Z"
      },
      "hasMoreResults": true
    }
  },
  "metadata": {
    "timestamp": "2026-02-19T00:00:00Z",
    "requestId": "req-abc123",
    "processingTimeMs": 120,
    "dataSource": "DynamoDB"
  },
  "success": true
}
```

> **Pagination:** The `pagination` object is present only when more results are available. When `hasMoreResults` is `true`, pass the entire `lastEvaluatedKey` object back in your next request to retrieve the next page. When no more results exist, the `pagination` object is omitted from the response entirely.

**Response Headers:**

| Header | Value |
|--------|-------|
| `Content-Type` | `application/json` |
| `Cache-Control` | `max-age=60` |
| `ETag` | Content hash |
| `X-Total-Records` | Total records count |

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 400 | Missing fields, invalid formats, invalid timestamps |
| 401 | Auth failure |
| 403 | Request `playerID` does not match the authenticated player (`PLAYER_ID_MISMATCH`) |
| 404 | Leaderboard not found (if filtering by leaderboard) |
| 405 | Non-POST method (returns `Allow: POST` header) |
| 429 | Throttling |
| 500 | Internal error |

**Pagination Example -- Fetching the Next Page:**

When the response includes a `pagination` object with `hasMoreResults: true`, pass the `lastEvaluatedKey` back in your next request:

```json
{
  "playerStatsAndScoresRequest": {
    "playerID": "player001",
    "gameID": "stellarodyssey",
    "gameMode": "campaign",
    "limit": 25,
    "lastEvaluatedKey": {
      "playerID": "player001",
      "sortKey": "stellarodyssey#campaign#2026-02-15T08:30:00Z"
    }
  }
}
```

This resumes the query from where the previous page left off. Continue requesting with the returned `lastEvaluatedKey` until the `pagination` object is absent from the response.

---

### 3.4 Get Player Leaderboard Standing

Returns a player's rank, score, percentile, and surrounding players.

#### POST (Query Standing)

**Request:**

```http
POST /leaderboards/player/standing
Content-Type: application/json
Authorization: Bearer <player_access_token>
```

**Request Template (all parameters):**

```json
{
  "playerLBStandingRequest": {
    "playerID": "<string>",
    "leaderboardName": "<string>",
    "includePercentile": "<bool>",       // Optional, default: false
    "includeNeighbours": "<bool>",       // Optional, default: false (also accepts "includeNeighbors")
    "neighboursCount": "<int>"           // Optional, default: 3, max: 50 (also accepts "neighborsCount")
  }
}
```

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `playerID` | string | Yes | -- | Max 255 chars,<br>pattern: `^[a-zA-Z0-9_-]+$` |
| `leaderboardName` | string | Yes | -- | Max 255 chars,<br>pattern: `^[a-zA-Z0-9_-]+$` |
| `includePercentile` | bool | No | `false` | Must be boolean |
| `includeNeighbours` | bool | No | `false` | British spelling.<br>Also accepts `includeNeighbors` (American).<br>British takes precedence. |
| `neighboursCount` | int | No | 3 | Positive, max 50.<br>Also accepts `neighborsCount`.<br>British takes precedence.<br>Only validated when `includeNeighbours=true`. |

**Example Request (basic):**

```json
{
  "playerLBStandingRequest": {
    "playerID": "player001",
    "leaderboardName": "HighScores"
  }
}
```

**Example Request (full, with all options):**

```json
{
  "playerLBStandingRequest": {
    "playerID": "player001",
    "leaderboardName": "HighScores",
    "includePercentile": true,
    "includeNeighbours": true,
    "neighboursCount": 5
  }
}
```

**Basic Response (200):**

```json
{
  "playerLBStandingResponse": {
    "leaderboardName": "HighScores",
    "leaderboardType": "DESCENDING_LB",
    "playerLBStandingInfo": {
      "playerID": "player001",
      "rank": 150,
      "score": 15000
    },
    "metadata": {
      "processingTimeMs": 8,
      "timestamp": "2026-02-19T00:00:00Z",
      "requestId": "req-abc123",
      "totalProcessingTimeMs": 8
    },
    "success": true
  }
}
```

**Full Response with Percentile and Neighbours (200):**

```json
{
  "playerLBStandingResponse": {
    "leaderboardName": "HighScores",
    "leaderboardType": "DESCENDING_LB",
    "playerLBStandingInfo": {
      "playerID": "player001",
      "rank": 150,
      "score": 15000,
      "percentile": 97.0,
      "totalPlayers": 5000,
      "neighbours": [
        { "rank": 148, "playerID": "player088", "score": 15200, "isTarget": false },
        { "rank": 149, "playerID": "player033", "score": 15100, "isTarget": false },
        { "rank": 150, "playerID": "player001", "score": 15000, "isTarget": true },
        { "rank": 151, "playerID": "player055", "score": 14900, "isTarget": false },
        { "rank": 152, "playerID": "player077", "score": 14800, "isTarget": false }
      ]
    },
    "metadata": {
      "processingTimeMs": 15,
      "timestamp": "2026-02-19T00:00:00Z",
      "requestId": "req-abc123",
      "totalProcessingTimeMs": 15
    },
    "success": true
  }
}
```

**Percentile Formula:** `100 - ((player_rank - 1) / actual_total * 100)` where `actual_total = total_players - 1` if the init placeholder exists, otherwise `total_players`. Clamped to 0..100, rounded to 2 decimal places.

> **Note:** This formula is mathematically equivalent to the one used by the `playerScore` query type in [Section 6.2](#query-type-playerscore), which uses 0-based rank: `(1 - (rank / total_count)) * 100`. Both produce the same percentile for the same player. Both endpoints handle the init placeholder identically -- by design, each leaderboard has at most one placeholder entry (added at creation to prevent MemoryDB auto-deletion of empty sorted sets), which is removed when the first real score is written. The placeholder check is a safety net for empty leaderboards.

**Response Headers:**

| Header | Value |
|--------|-------|
| `Content-Type` | `application/json` |
| `Cache-Control` | `max-age=60` |
| `ETag` | Content hash |

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 400 | Missing fields, invalid formats, neighboursCount > 50 |
| 401 | Auth failure |
| 403 | Request `playerID` does not match the authenticated player (`PLAYER_ID_MISMATCH`); default-enforced, relaxable via `ALLOW_VIEWING_OTHER_PLAYERS_STANDING` |
| 404 | Player not found in leaderboard, leaderboard config not found, sorted list not in Valkey |
| 405 | Non-POST method |
| 429 | Throttling |
| 500 | Internal error |
| 503 | Valkey unavailable |

---



## 4. Data Models & Storage

### 4.1 DynamoDB Tables

#### Leaderboard Config Table

| Key | Type | Description |
|-----|------|-------------|
| `leaderboardName` (PK) | string | Unique leaderboard name |

Fields stored: `gameID`, `gameMode`, `statAttributeForLeaderboard`, `leaderboardType`, `scoreStrategy`, `sortedListName`, `scoreType`, `sortOrder`, `timePrecision`, `timeFormat`, `minValidScore`/`maxValidScore`, `minValidTimeInSeconds`/`maxValidTimeInSeconds`, `maxEntries`, `optionalLBExpiryDateTimeStamp`, `optionalLBReadOnlyOnExpiry`, `createdAt`, `createdAtISO`, `studioId`, `gameId`, `studioName`, `gameTitle`, `createdBy`, `description`, `tags`

#### Player Stats Table

| Key | Type | Description |
|-----|------|-------------|
| `playerID` (PK) | string | Player identifier |
| Sort Key (SK) | string | `gameID#gameMode#ISO-timestamp` |

Fields stored: `gameID`, `gameMode`, `playerScore`, `leaderboardName`, `fullRawGameReport`, `timestamp`, `timestampISO`, `scoreStrategy`, `studioId`, `gameId`

### 4.2 MemoryDB for Valkey

Leaderboards are stored as Valkey sorted sets:

| Key Pattern | Members | Scores |
|-------------|---------|--------|
| `gameID:gameMode:leaderboardName` | Player IDs | Player scores |

**ASCENDING_LB implementation:** Scores are stored as negative values in Valkey (`-score`) so that `ZREVRANGE` returns the lowest actual scores first.

**Init placeholders:** Each leaderboard is created with exactly one placeholder entry (matching the `scoreType` -- e.g., `_init_topscore_` for score, `_init_time_` for time) to prevent MemoryDB from auto-deleting empty sorted sets. This placeholder is removed automatically when the first real score is written. All query operations detect and exclude the placeholder from results, total player counts, and rank calculations as a safety net.

---

## 5. Leaderboard Concepts

### Leaderboard Lifecycle

```
  CREATE                    POPULATE                    QUERY
  /config/create  --->  storePlayerStats  --->  getLeaderboardScores
       |                batchStoreStats         getPlayerLBStanding
       |                     |                  getPlayerStatsAndScores
       v                     v                         |
  +----------+        +-------------+                  |
  | DynamoDB |        | DynamoDB    |                  |
  | (config) |        | (stats)     |                  |
  +----------+        +-------------+                  |
       |                     |                         |
       v                     v                         v
  +------------------------------------------------------------------+
  |                    MemoryDB for Valkey                            |
  |              (sorted sets: gameID:gameMode:lbName)                |
  +------------------------------------------------------------------+
       |                                          |
       v                                          v
    RESET                                      EXPIRE
    /leaderboards/admin/reset           (optionalLBExpiryDateTimeStamp)
       |                                          |
       +-- Backup scores                    +-----+------+
       +-- Clear sorted set                 |            |
       +-- Re-initialize placeholder    read-only    auto-delete
       |                                (HTTP 423     (scheduled
       v                                 on write)    cleanup)
    REBUILD
    /leaderboards/admin/rebuild
       |
       +-- Backup current scores
       +-- Scan DynamoDB stats
       +-- Re-populate sorted set
       +-- (with optional time filter
            and strategy override)
```

### 5.1 Leaderboard Types

| Type | Sort | Use Case | Valkey Storage |
|------|------|----------|---------------|
| `DESCENDING_LB` | Highest first | High scores, XP, kills | Scores stored as-is |
| `ASCENDING_LB` | Lowest first | Race times, golf scores | Scores stored as negative |

### 5.2 Score Strategies

| Strategy | DESCENDING_LB Behavior | ASCENDING_LB Behavior |
|----------|----------------------|---------------------|
| `replace` | Always overwrite score | Always overwrite score (stored as negative) |
| `best` | Update only if new > current | Update only if new < current (lower is better) |
| `cumulative` | Add scores together (ZINCRBY) | Add times together (cumulative time) |

```
New score submitted
    |
    v
Which strategy?
    |
    +-- "replace" --> Always overwrite --> ZADD (set score)
    |
    +-- "best" ----> Compare with current score
    |                   |
    |                   +-- DESCENDING_LB: new > current? --> ZADD
    |                   +-- ASCENDING_LB:  new < current? --> ZADD (as -score)
    |                   +-- Otherwise: skip (keep existing)
    |
    +-- "cumulative" -> Add to existing --> ZINCRBY (increment score)
```

### 5.3 Score Types

| Type | Description | Typical Use |
|------|-------------|-------------|
| `score` | Generic numeric score | Points, kills, XP |
| `time` | Time duration | Race times, speedruns |
| `distance` | Distance measurement | Endless runners, flight distance |
| `points` | Point accumulation | Cumulative scoring |
| `rank` | Ranking value | ELO, ladder position |
| `level` | Level/tier value | Character level, tier |

### 5.4 Event Leaderboards (Expiry)

Leaderboards with `optionalLBExpiryDateTimeStamp` set:

| Setting | On Expiry |
|---------|-----------|
| `optionalLBReadOnlyOnExpiry: true` | Writes rejected with HTTP 423<br>(LEADERBOARD_EXPIRED_READONLY);<br>reads still work |
| `optionalLBReadOnlyOnExpiry: false` | Writes rejected with HTTP 423<br>(LEADERBOARD_EXPIRED_AUTODELETE);<br>scheduled for deletion |

When reset or rebuild operations on expired leaderboards exceed the ~15 minute Lambda timeout, the Lambda function asynchronously self-invokes with continuation state to resume processing.

### 5.5 Sorted List Name Convention

All Valkey sorted set keys follow the pattern: `{gameID}:{gameMode}:{leaderboardName}`

Example: `stellarodyssey:campaign:HighScores`

---

## 6. Environment Variables Reference

> **Warning:** These values are tuned and tested for the deployed configuration. Do not modify them unless you understand the downstream effects — incorrect values can cause connection failures, timeouts, data inconsistency, or silent performance degradation that may only surface under load.

### Common (All Lambda Functions)

These are set by CDK during deployment. Variables marked "(CDK-managed)" are automatically populated and do not need manual configuration.

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `dev` | Deployment environment (dev/staging/prod) — CDK-managed |
| `gameLeaderboardsConfigTablename` | (CDK-managed) | DynamoDB config table name |
| `gameStatsAndScoresTablename` | (CDK-managed) | DynamoDB stats table name |
| `gameLeaderboardsMemoryDBName` | (CDK-managed) | MemoryDB cluster name |
| `MEMORYDB_CLUSTER_ENDPOINT` | (CDK-managed) | MemoryDB endpoint URL |
| `MEMORYDB_PORT` | `6379` | MemoryDB port |
| `MEMORYDB_SECRET_ARN` | (CDK-managed) | Secrets Manager ARN for MemoryDB auth |
| `SSM_PARAMETER_PREFIX` | `/{resource-prefix}` | SSM path prefix for all parameters |
| `LOG_LEVEL` | `DEBUG` (dev), `INFO` (staging/prod) | Logging level |
| `VALIDATE_VALKEY` | `true` | Validate MemoryDB connectivity on cold start |
| `INCLUDE_LEADERBOARD_DATA` | `true` | Include MemoryDB metadata in config GET responses |
| `VALKEY_USE_TLS` | `true` | Use TLS for Valkey connection |
| `VALKEY_CLUSTER_MODE` | `true` | Enable cluster mode |
| `MAX_CONCURRENT_OPERATIONS` | `10` | Max concurrent async operations (code default) |
| `GLIDE_CONNECTION_TIMEOUT_MS` | `3000` | Valkey connection timeout (ms) |
| `GLIDE_REQUEST_TIMEOUT_MS` | `5000` | Valkey request timeout (ms) |
| `GLIDE_CONNECTION_POOL_SIZE` | `1000` (dev), `500` (staging/prod) | Connection pool size |
| `GLIDE_CIRCUIT_BREAKER_ENABLED` | `true` | Circuit breaker for Valkey connections |
| `GLIDE_CIRCUIT_BREAKER_THRESHOLD` | `5` | Failures before circuit opens |
| `GLIDE_CIRCUIT_BREAKER_TIMEOUT_MS` | `60000` | Circuit breaker recovery timeout (ms) |

### Backend Authorizer (additional)

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY_PARAMETER_NAMES` | (CDK-managed) | JSON array of SSM parameter names (pre-loaded at startup, bypasses per-request SSM API calls) |

### Player Authorizer (additional)

| Variable | Default | Description |
|----------|---------|-------------|
| `PLAYER_AUTH_MODE` | `identity` | `identity` validates Custom Identity Component access tokens; `custom` routes to your own `_authorize_custom()`. Set via CDK context `player_auth_mode`. |
| `ISSUER_URL` | (empty) | Custom Identity Component issuer URL, used in identity mode to fetch the JWKS and verify the token issuer. From the `IssuerEndpointUrl` deploy parameter (the `ISSUER_ENDPOINT_URL` export). Required in identity mode; empty denies all requests. |
| `TOKEN_AUDIENCE` | `gamebackend` | Expected `aud` claim on player access tokens (identity mode). |

### Player-Specific

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIG_CACHE_TTL` | `300` | Leaderboard config cache TTL (seconds) |
| `MAX_PAGE_SIZE` | `500` | Max page size for leaderboard queries |
| `DEFAULT_PAGE_SIZE` | `10` | Default page size |
| `MAX_QUERY_LIMIT` | `100` | Max results for stats queries |
| `MAX_NEIGHBOURS_COUNT` | `50` | Max neighbours per side |
| `INCLUDE_PERCENTILE_DEFAULT` | `false` | Default percentile inclusion |
| `INCLUDE_NEIGHBOURS_DEFAULT` | `false` | Default neighbours inclusion |

### Backend-Specific (Batch Store)

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_ITEMS_PER_REQUEST` | `1000` | Max items in a single batch request |
| `MAX_BATCH_SIZE` | `25` | DynamoDB batch write size |
| `MAX_VALKEY_PIPELINE_SIZE` | `100` | MemoryDB pipeline operations per batch |
| `MAX_CONCURRENT_OPERATIONS` | `50` | Max concurrent workers (overrides the common default of 10) |
| `CONFIG_CACHE_TTL` | `300` | Leaderboard config cache TTL (seconds) |

### Backend-Specific (Leaderboard Config)

| Variable | Default | Description |
|----------|---------|-------------|
| `FORCE_DELETE` | `false` | Skip backup when deleting a leaderboard |
| `BACKUP_BEFORE_DELETE` | `true` | Create Valkey backup before deleting leaderboard config |
| `BACKUP_BEFORE_MIGRATION` | `true` | Create Valkey backup before gameID/gameMode migration |
| `ENABLE_ROLLBACK` | `true` | Automatic rollback on migration failure |
| `MIGRATION_TIMEOUT` | `300` | Timeout (seconds) for Valkey sorted set migration operations |
| `MIGRATION_BATCH_SIZE` | `1000` | Entries processed per batch during migration |

### Backend-Specific (Reset)

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKUP_BEFORE_RESET` | `true` | Create backup before reset |
| `BACKUP_TTL_HOURS` | `24` | Manual backup retention (hours) |
| `SCHEDULED_BACKUP_TTL_DAYS` | `30` | Scheduled expiry backup retention (days) |
| `ENABLE_RESET_LOCK` | `true` | Enable concurrent reset prevention via distributed lock |
| `RESET_BATCH_SIZE` | `1000` | Entries processed per batch during large resets |
| `MAX_EXECUTION_TIME` | `780` | Max Lambda execution seconds before relay continuation |
| `TIME_BUFFER` | `120` | Safety buffer (seconds) before triggering relay |

### Backend-Specific (Rebuild)

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKUP_BEFORE_REBUILD` | `true` | Create backup before rebuild |
| `REBUILD_BATCH_SIZE` | `500` | DynamoDB items processed per batch during rebuild |
| `ENABLE_PAUSE_UPDATES` | `true` | Pause score updates during rebuild (distributed lock) |
| `MAX_EXECUTION_TIME` | `780` | Max Lambda execution seconds before relay continuation |
| `TIME_BUFFER` | `60` | Safety buffer (seconds) before triggering relay |

---

## 7. Error Reference

### Standard Error Response Format

All API errors follow a consistent pattern:

```json
{
  "<responseWrapper>": {
    "error": "<error type>",
    "message": "<human-readable description>",
    "timestamp": "<ISO-8601>"
  }
}
```

Where `<responseWrapper>` is:
- `devRegResponse` for developer endpoints
- `gameLeaderboardConfigResponse` for config endpoints
- `batchGameReportResponse` for batch store
- `resetLeaderboardResponse` for reset
- `rebuildLeaderboardResponse` for rebuild
- `gameReportResponse` for player store
- `leaderboardScoresResponse` for leaderboard queries
- `playerStatsAndScoresResponse` for stats queries
- `playerLBStandingResponse` for standing queries

### HTTP Status Code Reference

| Status | Meaning | Retryable |
|--------|---------|-----------|
| 200 | Success | N/A |
| 201 | Created (config create, registration) | N/A |
| 202 | Accepted (long-running operation in progress) | Check back later |
| 400 | Bad Request (invalid input) | No |
| 401 | Unauthorized (auth failure) | No |
| 403 | Forbidden (IAM permission) | No |
| 404 | Not Found (resource not found) | No |
| 405 | Method Not Allowed | No |
| 409 | Conflict (duplicate, lock contention) | Maybe (retry after delay) |
| 423 | Locked (expired leaderboard, duplicate players) | No |
| 429 | Too Many Requests (throttling) | Yes (with backoff) |
| 500 | Internal Server Error | Yes (with backoff) |
| 503 | Service Unavailable (Valkey down) | Yes (with backoff) |

### CORS Headers (On Error Responses)

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Headers: Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token
Access-Control-Allow-Methods: POST,OPTIONS
```

Config endpoints include: `GET,POST,PUT,DELETE,OPTIONS`

---


---

> 📖 Back to [Main Documentation](../readme.md)
