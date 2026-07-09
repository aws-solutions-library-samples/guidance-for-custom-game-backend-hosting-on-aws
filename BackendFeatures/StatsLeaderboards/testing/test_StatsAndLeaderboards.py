#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Comprehensive Integration Test Suite — Game Stats and Leaderboards
==================================================================
Unit and integration tests for the Game Stats and Leaderboards system. Auto-discovers
configuration from CloudFormation stack outputs and SSM Parameter Store. Logs every
request/response to a session log file. Cleans up all created leaderboards after each
run (unless --retain is passed).

TODO / IMPORTANT (player-identity enforcement):
    This suite submits and queries MANY distinct player IDs against the player
    endpoints. Those endpoints now enforce that a request's playerID matches the
    authenticated player (auth_context['playerId']) — store/get-player-stats
    always, get-player-standing by default. So this suite only passes end-to-end
    when your integrated auth/playerAuthorizer.py does NOT pin a single fixed
    'playerId' (i.e. it echoes the request's player, or omits 'playerId' so the
    enforce-when-present checks are skipped). A production authorizer SHOULD pin
    the real player's id from the validated token; that is correct for a live game
    but is intentionally not what a multi-player test harness needs.

Phase numbers are for reference only (not sequential due to iterative development).

Test Phases:
  Phase 12  Developer endpoints, authentication, and input validation
            - Developer info / health check (GET /developer/info)
            - Invalid API key rejection (HTTP 403)
            - Missing required fields rejection (HTTP 400)
            - Invalid playerID characters rejection (HTTP 400)
            - Duplicate leaderboard config name rejection (HTTP 400/409)
            - Config update (PUT /leaderboards/config/update) — strategy change
            - Config get-all (POST /leaderboards/configs)
            - Batch duplicate playerID rejection (HTTP 423, DUPLICATE_PLAYERS_IN_BATCH)
            - Create expiring leaderboard (90s TTL, read-only on expiry)
            - Submit 5 scores while leaderboard is still active (within 90s window)

  Phase 1   Create 9 leaderboards covering all score types and strategies
            - score/DESCENDING/best, time/ASCENDING/best (seconds, minutes_seconds,
              hours_minutes_seconds), distance/DESCENDING/best, points/DESCENDING/cumulative,
              rank/ASCENDING/replace, level/DESCENDING/best, score/DESCENDING/replace

  Phase 2   Validate all 9 leaderboard configs via GET
            - Field-by-field comparison: gameID, gameMode, leaderboardType, scoreStrategy, scoreType

  Phase 3   Submit scores via single-store (POST /leaderboards/stats)
            - 5 players per leaderboard, all 9 leaderboards = 45 score submissions
            - Covers numeric scores, time strings (MM:SS, HH:MM:SS), all score types

  Phase 3b  Cumulative score strategy verification
            - 3 submissions to same player, verify sum matches (100+200+300=600)

  Phase 3c  Batch-populate leaderboards to 100+ entries each
            - 100 additional players per leaderboard via batch store (chunks of 25)
            - Random scores within each leaderboard's configured bounds
            - Each of the 9 main leaderboards ends up with 105 players

  Phase 4   Leaderboard ordering validation (top query, all 105+ entries)
            - Verify monotonic sort order (descending or ascending per LB type)
            - Verify contiguous ranks [1,2,3,...] with no gaps
            - Verify totalPlayers count excludes placeholder
            - Verify no placeholder entry leaks into results

  Phase 5   Player standing for all 9 leaderboard types
            - Rank, score, percentile (0-100), totalPlayers
            - Neighbours list with isTarget flag
            - No placeholder in neighbours
            - Contiguous neighbour ranks

  Phase 6   Batch store (POST /leaderboards/stats/batch) — small batch
            - 5 players in single batch, verify each via playerScore query

  Phase 7   Player stats query (POST /leaderboards/player/stats)
            - DynamoDB query with required fields validation
            - Stats summary (totalRecords, dateRange, scoreRange)

  Phase 8   Range query and aroundPlayer query
            - Score range filter with inclusive boundaries
            - aroundPlayer with countBefore/countAfter, contiguous ranks

  Phase 9   Deep time-based leaderboard tests (10 sub-tests)
            - 9a: Milliseconds format — numeric input, display in ms, ascending order
            - 9b: Best strategy — worse time rejected, better time accepted
            - 9c: Replace strategy — always overwrites regardless of better/worse
            - 9d: Cumulative strategy — adds lap times (30.5+45.25+22.75=98.5s)
            - 9e: Mixed input formats — numeric, MM:SS, HH:MM:SS, plain string to same LB
            - 9f: Time precision variations — timePrecision 0, 1, 6
            - 9g: Boundary validation — accept at min/max, reject below/above, string format
            - 9h: Batch store with mixed time formats in single batch
            - 9i: Player standing on time LB — rank, MM:SS display, percentile, neighbours
            - 9j: Range query on time LB — score range filter in seconds

  Phase 10  Placeholder/init-entry edge cases (4 sub-tests)
            - 10a: DESCENDING_LB — query empty LB (placeholder only), add 1 score, verify
                   rank=1, totalPlayers=1, percentile=100%, no placeholder in neighbours
            - 10b: ASCENDING_LB (time) — placeholder at position 0 in zrevrange, same checks
            - 10c: ASCENDING_LB (rank) — 3 players, top/aroundPlayer/standing all clean
            - 10d: ASCENDING_LB (distance) — negative placeholder value edge case

  Phase 12b Expired leaderboard lifecycle verification
            - Verify scores submitted earlier are still readable after expiry (read-only mode)
            - Verify new single-store submission rejected with HTTP 423 LEADERBOARD_EXPIRED_READONLY
            - Verify batch submission rejected with HTTP 423
            - Verify rebuild rejected with HTTP 423 LEADERBOARD_EXPIRED_READONLY
            - Verify player standing query still works (read operations unaffected)

  Phase 11  Large batch store stress test
            - 11a: 16 players x 1800-param reports (~800 KB payload)
            - 11b: 32 players x 1800-param reports (~1.6 MB payload)
            - 11c: 64 players x 1800-param reports (~3.2 MB payload)
            - 11d: Validate 112 total players, contiguous ranks 1-112, no placeholder
            - 11e: Verify 1800-field fullRawGameReport stored in DynamoDB

  Phase 13  Capture full leaderboard state (pre-rebuild snapshot)
            - Top query (pageSize=500) on every created leaderboard
            - Store scores, playerIDs, ranks, totalPlayers for post-rebuild comparison

  Phase 14  Rebuild every leaderboard and validate (POST /leaderboards/admin/rebuild)
            - Rebuilds from DynamoDB stats records for all score types
            - Skips expired leaderboards (tested separately in Phase 12b)
            - Post-rebuild comparison with pre-rebuild snapshot:
              totalPlayers match, score count match, same player set,
              contiguous ranks, no placeholder leak, per-player score values match
            - Covers: score, time (seconds/minutes_seconds/hours_minutes_seconds/milliseconds),
              distance, points (cumulative), rank, level leaderboard types
            - Validates rebuild with large payloads (112 players x 1800-param reports)

  Phase 15  Reset leaderboards and validate (POST /leaderboards/admin/reset)
            - Reset with backup (confirmReset=true, createBackup=true)
            - Verify post-reset: 0 scores returned, totalPlayers=0, placeholder excluded
            - Verify leaderboard config preserved after reset (GET config returns 200)
            - When --retain: resets only 2 sample LBs (one DESCENDING, one ASCENDING),
              leaving the rest with data intact for manual inspection

  Phase 16  Cleanup — delete all created leaderboard configs
            - Skipped when --retain flag is used (leaderboards listed for reference)

Output:
  - Console: per-test PASS/FAIL with brief context for each test
  - Log file: test_comprehensive_<session>.log with full request/response bodies

Usage:
  python3 test_StatsAndLeaderboards.py
  python3 test_StatsAndLeaderboards.py --profile myprofile
  python3 test_StatsAndLeaderboards.py --profile prod-account --region eu-west-1 --stack-name MyStack
  python3 test_StatsAndLeaderboards.py --retain    # skip cleanup, keep leaderboards for inspection
"""

import json
import time
import uuid
import sys
import random
import string
import argparse
import requests
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Dict

# =============================================================================
# CONFIGURATION — auto-discovered from AWS
# =============================================================================
STACK_NAME = ""   # Set by CLI --stack-name (default: GameStatsLeaderboardsStack)
AWS_PROFILE = ""  # Set by CLI --profile (default: none / uses environment default)
AWS_REGION = ""   # Set by CLI --region (default: us-west-2)

# These are populated by discover_configuration()
API_ENDPOINT = ""
API_KEY = ""
STUDIO_ID = ""
GAME_ID = ""
HEADERS: Dict[str, str] = {}


def discover_configuration():
    """Read API endpoint from CloudFormation and active API key from SSM Parameter Store."""
    global API_ENDPOINT, API_KEY, STUDIO_ID, GAME_ID, HEADERS

    import boto3

    printlog(f"  Discovering configuration from AWS...")
    printlog(f"    Stack:   {STACK_NAME}")
    printlog(f"    Profile: {AWS_PROFILE or '(default credential chain)'}")
    printlog(f"    Region:  {AWS_REGION}")

    try:
        import boto3
    except ImportError:
        print("\n  ERROR: boto3 is not installed. Install it with: pip install boto3")
        sys.exit(1)

    # AWS_PROFILE=None uses the default credential chain (env vars, instance role, etc.)
    try:
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    except Exception as e:
        profile_hint = f"--profile {AWS_PROFILE}" if AWS_PROFILE else "(default credentials)"
        print(f"\n  ERROR: Failed to create AWS session with {profile_hint}.")
        print(f"    {type(e).__name__}: {e}")
        print(f"\n  Ensure your AWS credentials are configured. Examples:")
        print(f"    python3 test_StatsAndLeaderboards.py --profile your-profile-name")
        print(f"    export AWS_PROFILE=your-profile-name && python3 test_StatsAndLeaderboards.py")
        sys.exit(1)

    # --- CloudFormation: get API endpoint ---
    try:
        cf = session.client("cloudformation")
        stacks = cf.describe_stacks(StackName=STACK_NAME)["Stacks"]
        if not stacks:
            raise RuntimeError(f"Stack '{STACK_NAME}' not found")
    except Exception as e:
        err_name = type(e).__name__
        err_msg = str(e)

        print(f"\n  ERROR: Cannot access CloudFormation stack.")
        print(f"    {err_name}: {err_msg}")
        print(f"\n  Attempted with:")
        print(f"    --stack-name {STACK_NAME}")
        print(f"    --region     {AWS_REGION}")
        print(f"    --profile    {AWS_PROFILE or '(default credential chain)'}")

        if "does not exist" in err_msg or "not found" in err_msg.lower():
            print(f"\n  The stack '{STACK_NAME}' was not found in region '{AWS_REGION}'.")
            print(f"  Ensure the system is deployed first, and specify the correct stack name/region.")
            print(f"  Example: python3 test_StatsAndLeaderboards.py --stack-name YourStackName --region your-region")
        elif "credential" in err_msg.lower() or "expired" in err_msg.lower() or "token" in err_msg.lower():
            print(f"\n  Your AWS credentials may be missing, expired, or invalid.")
            print(f"  Try: aws sts get-caller-identity --profile {AWS_PROFILE or 'default'}")
            print(f"  Example: python3 test_StatsAndLeaderboards.py --profile your-profile")
        elif "denied" in err_msg.lower() or "authorized" in err_msg.lower():
            print(f"\n  Your AWS credentials don't have permission to access CloudFormation.")
            print(f"  Ensure your IAM role/user has cloudformation:DescribeStacks permission.")

        sys.exit(1)

    outputs = {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}

    API_ENDPOINT = outputs.get("ApiEndpoint", "")
    if not API_ENDPOINT:
        print(f"\n  ERROR: 'ApiEndpoint' not found in stack '{STACK_NAME}' outputs.")
        print(f"    Available outputs: {list(outputs.keys())}")
        print(f"    The stack may not have deployed successfully. Check CloudFormation console.")
        sys.exit(1)
    if not API_ENDPOINT.endswith("/"):
        API_ENDPOINT += "/"

    ssm_prefix = outputs.get("SSMParameterPrefix", "/game-statsleaderboards-dev")

    printlog(f"    API:     {API_ENDPOINT}")
    printlog(f"    SSM:     {ssm_prefix}")

    # --- SSM Parameter Store: find active API key ---
    # TODO / TEST INSTRUMENTATION ONLY: this suite authenticates ALL requests
    # (including the player-facing endpoints) with the backend Studio API key
    # retrieved below. That is a deliberate test convenience so the suite can run
    # without standing up a real player-identity provider. A real game does NOT
    # do this: player endpoints must be called with a player token that your
    # integrated auth/playerAuthorizer.py validates. Do not copy this pattern
    # into client code.
    ssm = session.client("ssm")
    api_keys_path = f"{ssm_prefix.rstrip('/')}/api-keys"

    # Use describe_parameters first since path might vary
    paginator = ssm.get_paginator("get_parameters_by_path")
    found = False
    for page in paginator.paginate(Path=api_keys_path, Recursive=True, WithDecryption=True):
        for param in page.get("Parameters", []):
            try:
                data = json.loads(param["Value"])
                if data.get("status") == "active":
                    API_KEY = data["apiKey"]
                    STUDIO_ID = data["studioId"]
                    GAME_ID = data["gameId"]
                    found = True
                    break
            except (json.JSONDecodeError, KeyError):
                continue
        if found:
            break

    if not found:
        # Fallback: try the older SSM prefix pattern
        alt_path = "/game-statsleaderboards-dev/api-keys"
        if alt_path != api_keys_path:
            printlog(f"    Trying fallback SSM path: {alt_path}")
            for page in paginator.paginate(Path=alt_path, Recursive=True, WithDecryption=True):
                for param in page.get("Parameters", []):
                    try:
                        data = json.loads(param["Value"])
                        if data.get("status") == "active":
                            API_KEY = data["apiKey"]
                            STUDIO_ID = data["studioId"]
                            GAME_ID = data["gameId"]
                            found = True
                            break
                    except (json.JSONDecodeError, KeyError):
                        continue
                if found:
                    break

    if not found:
        print(f"\n  ERROR: No active API key found in SSM Parameter Store.")
        print(f"    Searched paths: '{api_keys_path}'")
        print(f"    This usually means the developer registration has not been completed,")
        print(f"    or the API key has been revoked. Run the deployment script or register")
        print(f"    a studio/game via POST /developer/register first.")
        sys.exit(1)

    HEADERS = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }

    printlog(f"    Studio:  {STUDIO_ID}")
    printlog(f"    Game:    {GAME_ID}")
    printlog(f"    Key:     {API_KEY[:20]}...")
    printlog(f"  Configuration discovered successfully.\n")

# Unique session ID to avoid collisions across test runs
SESSION = uuid.uuid4().hex[:8]
LOG_FILE = f"test_comprehensive_{SESSION}.log"

# =============================================================================
# TEST TRACKING
# =============================================================================
total_tests = 0
passed_tests = 0
failed_tests = 0
failures = []
created_leaderboards = []  # for cleanup
request_counter = 0


def log(msg: str):
    """Append a line to the log file."""
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")


def printlog(msg: str):
    """Print to console AND append to log file."""
    print(msg)
    log(msg)


def ok(test_name: str, detail: str = ""):
    global total_tests, passed_tests
    total_tests += 1
    passed_tests += 1
    suffix = f" -- {detail}" if detail else ""
    printlog(f"  [PASS] {test_name}{suffix}")


def fail(test_name: str, detail: str):
    global total_tests, failed_tests
    total_tests += 1
    failed_tests += 1
    failures.append((test_name, detail))
    printlog(f"  [FAIL] {test_name} -- {detail}")


def section(title: str):
    printlog(f"\n{'='*80}")
    printlog(f"  {title}")
    printlog(f"{'='*80}")


def subsection(title: str):
    printlog(f"\n  --- {title} ---")


# Test context — brief description printed before each logical test
_next_request_desc = ""


def test_desc(desc: str):
    """Set a brief description for the next API call. Printed in both console and log."""
    global _next_request_desc
    _next_request_desc = desc
    printlog(f"    >> {desc}")


# =============================================================================
# API HELPERS (with full request/response logging)
# =============================================================================
def _log_request_response(method: str, url: str, req_body: Optional[dict],
                          status: int, resp_body: dict, elapsed_ms: int):
    """Write full request + response detail to the log file."""
    global request_counter, _next_request_desc
    request_counter += 1
    log(f"\n--- REQUEST #{request_counter} ---")
    if _next_request_desc:
        log(f"  Context: {_next_request_desc}")
        _next_request_desc = ""
    log(f"  {method} {url}")
    log(f"  Headers: Authorization: {API_KEY[:15]}...  Content-Type: application/json")
    if req_body is not None:
        log(f"  Request Body:\n{json.dumps(req_body, indent=2, default=str)}")
    else:
        log(f"  Request Body: (none)")
    log(f"  Response Status: {status}  ({elapsed_ms} ms)")
    log(f"  Response Body:\n{json.dumps(resp_body, indent=2, default=str)}")
    log(f"--- END #{request_counter} ---")


def api_post(path: str, body: dict, expected_status: int = 200) -> dict:
    url = f"{API_ENDPOINT}{path.lstrip('/')}"
    t0 = time.perf_counter()
    resp = requests.post(url, headers=HEADERS, json=body, timeout=30)
    elapsed = int((time.perf_counter() - t0) * 1000)
    data = resp.json() if resp.text else {}
    _log_request_response("POST", url, body, resp.status_code, data, elapsed)
    return {"status": resp.status_code, "body": data, "ok": resp.status_code == expected_status}


def api_get(path: str, expected_status: int = 200) -> dict:
    url = f"{API_ENDPOINT}{path.lstrip('/')}"
    t0 = time.perf_counter()
    resp = requests.get(url, headers=HEADERS, timeout=30)
    elapsed = int((time.perf_counter() - t0) * 1000)
    data = resp.json() if resp.text else {}
    _log_request_response("GET", url, None, resp.status_code, data, elapsed)
    return {"status": resp.status_code, "body": data, "ok": resp.status_code == expected_status}


def api_delete(path: str, body: dict) -> dict:
    url = f"{API_ENDPOINT}{path.lstrip('/')}"
    t0 = time.perf_counter()
    resp = requests.delete(url, headers=HEADERS, json=body, timeout=30)
    elapsed = int((time.perf_counter() - t0) * 1000)
    data = resp.json() if resp.text else {}
    _log_request_response("DELETE", url, body, resp.status_code, data, elapsed)
    return {"status": resp.status_code, "body": data}


# =============================================================================
# LEADERBOARD DEFINITIONS — one per score type + key combos
# =============================================================================
def build_leaderboard_defs():
    """Build leaderboard configs covering all score types and critical combos."""
    defs = []

    # 1. score / DESCENDING / best
    defs.append({
        "tag": "score-desc-best",
        "config": {
            "gameID": GAME_ID,
            "gameMode": "campaign",
            "leaderboardName": f"tscore-desc-{SESSION}",
            "statAttributeForLeaderboard": "score",
            "leaderboardType": "DESCENDING_LB",
            "scoreStrategy": "best",
            "scoreType": "score",
            "minValidScore": 0,
            "maxValidScore": 999999
        },
        "scores": [5000, 8000, 3000, 12000, 7500],
        "expected_top_order": [12000, 8000, 7500, 5000, 3000],
    })

    # 2. time / ASCENDING / best / seconds format
    defs.append({
        "tag": "time-asc-best-sec",
        "config": {
            "gameID": GAME_ID,
            "gameMode": "timeattack",
            "leaderboardName": f"ttime-sec-{SESSION}",
            "statAttributeForLeaderboard": "lapTime",
            "leaderboardType": "ASCENDING_LB",
            "scoreStrategy": "best",
            "scoreType": "time",
            "sortOrder": "asc",
            "timeFormat": "seconds",
            "timePrecision": 3,
            "minValidTimeInSeconds": 1.0,
            "maxValidTimeInSeconds": 600.0
        },
        "scores": [83.456, 90.123, 75.001, 120.500, 60.999],
        "expected_top_order": [60.999, 75.001, 83.456, 90.123, 120.5],
    })

    # 3. time / ASCENDING / best / minutes_seconds format
    defs.append({
        "tag": "time-asc-best-minsec",
        "config": {
            "gameID": GAME_ID,
            "gameMode": "timeattack-ms",
            "leaderboardName": f"ttime-ms-{SESSION}",
            "statAttributeForLeaderboard": "lapTime",
            "leaderboardType": "ASCENDING_LB",
            "scoreStrategy": "best",
            "scoreType": "time",
            "sortOrder": "asc",
            "timeFormat": "minutes_seconds",
            "timePrecision": 3,
            "minValidTimeInSeconds": 10.0,
            "maxValidTimeInSeconds": 600.0
        },
        # Submit as MM:SS.mmm strings
        "scores": ["1:23.456", "2:05.100", "0:55.999", "1:45.000", "3:10.250"],
        # After parsing: 83.456, 125.1, 55.999, 105.0, 190.25 → ascending order
        "expected_top_order": [55.999, 83.456, 105.0, 125.1, 190.25],
    })

    # 4. distance / DESCENDING / best
    defs.append({
        "tag": "distance-desc-best",
        "config": {
            "gameID": GAME_ID,
            "gameMode": "endless",
            "leaderboardName": f"tdist-desc-{SESSION}",
            "statAttributeForLeaderboard": "distance",
            "leaderboardType": "DESCENDING_LB",
            "scoreStrategy": "best",
            "scoreType": "distance",
            "minValidScore": 0,
            "maxValidScore": 1000000
        },
        "scores": [1500.5, 3200.0, 800.25, 5000.75, 2100.0],
        "expected_top_order": [5000.75, 3200.0, 2100.0, 1500.5, 800.25],
    })

    # 5. points / DESCENDING / cumulative
    defs.append({
        "tag": "points-desc-cumul",
        "config": {
            "gameID": GAME_ID,
            "gameMode": "multiplayer",
            "leaderboardName": f"tpts-cumul-{SESSION}",
            "statAttributeForLeaderboard": "xpEarned",
            "leaderboardType": "DESCENDING_LB",
            "scoreStrategy": "cumulative",
            "scoreType": "points",
            "minValidScore": 0,
            "maxValidScore": 1000000000
        },
        # For cumulative, we submit multiple scores per player and verify they add up
        "scores": [100, 200, 300, 400, 500],
        "expected_top_order": None,  # cumulative needs special handling
    })

    # 6. rank / ASCENDING / replace (e.g., ELO)
    defs.append({
        "tag": "rank-asc-replace",
        "config": {
            "gameID": GAME_ID,
            "gameMode": "ranked",
            "leaderboardName": f"trank-asc-{SESSION}",
            "statAttributeForLeaderboard": "eloRating",
            "leaderboardType": "ASCENDING_LB",
            "scoreStrategy": "replace",
            "scoreType": "rank",
            "sortOrder": "asc",
            "minValidScore": 1,
            "maxValidScore": 3000
        },
        "scores": [1500, 1200, 1800, 1050, 2200],
        "expected_top_order": [1050, 1200, 1500, 1800, 2200],
    })

    # 7. level / DESCENDING / best
    defs.append({
        "tag": "level-desc-best",
        "config": {
            "gameID": GAME_ID,
            "gameMode": "adventure",
            "leaderboardName": f"tlevel-desc-{SESSION}",
            "statAttributeForLeaderboard": "characterLevel",
            "leaderboardType": "DESCENDING_LB",
            "scoreStrategy": "best",
            "scoreType": "level",
            "minValidScore": 1,
            "maxValidScore": 100
        },
        "scores": [25, 50, 10, 75, 42],
        "expected_top_order": [75, 50, 42, 25, 10],
    })

    # 8. score / DESCENDING / replace
    defs.append({
        "tag": "score-desc-replace",
        "config": {
            "gameID": GAME_ID,
            "gameMode": "daily",
            "leaderboardName": f"tscore-repl-{SESSION}",
            "statAttributeForLeaderboard": "dailyScore",
            "leaderboardType": "DESCENDING_LB",
            "scoreStrategy": "replace",
            "scoreType": "score",
            "minValidScore": 0,
            "maxValidScore": 999999
        },
        "scores": [5000, 8000, 3000, 12000, 7500],
        "expected_top_order": [12000, 8000, 7500, 5000, 3000],
    })

    # 9. time / ASCENDING / best / HH:MM:SS format
    defs.append({
        "tag": "time-asc-best-hms",
        "config": {
            "gameID": GAME_ID,
            "gameMode": "marathon",
            "leaderboardName": f"ttime-hms-{SESSION}",
            "statAttributeForLeaderboard": "finishTime",
            "leaderboardType": "ASCENDING_LB",
            "scoreStrategy": "best",
            "scoreType": "time",
            "sortOrder": "asc",
            "timeFormat": "hours_minutes_seconds",
            "timePrecision": 2,
            "minValidTimeInSeconds": 60.0,
            "maxValidTimeInSeconds": 86400.0
        },
        # Submit as HH:MM:SS.mm strings
        "scores": ["1:02:03.45", "0:55:30.00", "2:15:00.10", "0:48:22.99", "1:30:00.00"],
        # After parsing: 3723.45, 3330.0, 8100.1, 2902.99, 5400.0 → ascending order
        "expected_top_order": [2902.99, 3330.0, 3723.45, 5400.0, 8100.1],
    })

    return defs


# =============================================================================
# PHASE 1: CREATE LEADERBOARDS
# =============================================================================
def phase_create_leaderboards(lb_defs):
    section("PHASE 1: CREATE LEADERBOARDS")

    for d in lb_defs:
        tag = d["tag"]
        config = d["config"]
        lb_name = config["leaderboardName"]

        r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": config}, expected_status=201)
        if r["ok"]:
            resp = r["body"].get("gameLeaderboardConfigResponse", {})
            if resp.get("success"):
                ok(f"Create {tag}", f"'{lb_name}' created")
                created_leaderboards.append(lb_name)
            else:
                fail(f"Create {tag}", f"success=false: {json.dumps(resp, default=str)[:200]}")
        else:
            fail(f"Create {tag}", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")


# =============================================================================
# PHASE 2: GET/QUERY EACH LEADERBOARD CONFIG
# =============================================================================
def phase_validate_configs(lb_defs):
    section("PHASE 2: VALIDATE LEADERBOARD CONFIGS (GET)")

    for d in lb_defs:
        tag = d["tag"]
        config = d["config"]
        lb_name = config["leaderboardName"]

        r = api_post("leaderboards/config/get", {"gameLeaderboardConfigRequest": {"leaderboardName": lb_name}})
        if not r["ok"]:
            fail(f"GetConfig {tag}", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")
            continue

        resp = r["body"].get("gameLeaderboardConfigResponse", {})
        lb_cfg = resp.get("leaderboardConfig", {})

        # Validate key fields match what we sent
        checks = [
            ("gameID", config["gameID"], lb_cfg.get("gameID")),
            ("gameMode", config["gameMode"], lb_cfg.get("gameMode")),
            ("leaderboardType", config["leaderboardType"], lb_cfg.get("leaderboardType")),
            ("scoreStrategy", config["scoreStrategy"], lb_cfg.get("scoreStrategy")),
            ("scoreType", config.get("scoreType", "score"), lb_cfg.get("scoreType", "score")),
        ]

        all_good = True
        for field, expected, actual in checks:
            if str(expected) != str(actual):
                fail(f"GetConfig {tag}.{field}", f"expected={expected}, got={actual}")
                all_good = False

        if all_good:
            ok(f"GetConfig {tag}", f"all fields match")


# =============================================================================
# PHASE 3: SUBMIT SCORES (single store)
# =============================================================================
def phase_submit_single_scores(lb_defs):
    section("PHASE 3: SUBMIT SCORES (single store per player)")

    for d in lb_defs:
        tag = d["tag"]
        config = d["config"]
        lb_name = config["leaderboardName"]
        scores = d["scores"]

        subsection(f"{tag}: submitting {len(scores)} scores to '{lb_name}'")

        for idx, score_val in enumerate(scores):
            player_id = f"p{tag[:6]}-{SESSION}-{idx+1}"
            body = {
                "gameReportBody": {
                    "playerID": player_id,
                    "gameID": config["gameID"],
                    "gameMode": config["gameMode"],
                    "playerScore": score_val,
                    "leaderboardName": lb_name,
                    "fullRawGameReport": {
                        "testRun": SESSION,
                        "scoreSubmitted": str(score_val),
                        "matchId": f"match-{SESSION}-{idx}"
                    }
                }
            }
            r = api_post("leaderboards/stats", body)
            if r["ok"]:
                resp = r["body"].get("gameReportResponse", {})
                if resp.get("success"):
                    ok(f"Store {tag} player {idx+1}", f"score={score_val}")
                else:
                    fail(f"Store {tag} player {idx+1}", f"success=false: {json.dumps(resp, default=str)[:200]}")
            else:
                fail(f"Store {tag} player {idx+1}", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")

        # Small delay to let Valkey propagate
        time.sleep(0.3)


# =============================================================================
# PHASE 3b: SUBMIT CUMULATIVE SCORES (special case for points-desc-cumul)
# =============================================================================
def phase_submit_cumulative_scores(lb_defs):
    section("PHASE 3b: CUMULATIVE SCORE STRATEGY TEST")

    cumul_def = next((d for d in lb_defs if d["tag"] == "points-desc-cumul"), None)
    if not cumul_def:
        printlog("  (skipped -- no cumulative leaderboard defined)")
        return

    config = cumul_def["config"]
    lb_name = config["leaderboardName"]
    player_id = f"pcumul-{SESSION}-1"

    # Submit 3 scores for the same player: 100, 200, 300 → expect cumulative 600
    cumulative_scores = [100, 200, 300]
    for i, s in enumerate(cumulative_scores):
        body = {
            "gameReportBody": {
                "playerID": player_id,
                "gameID": config["gameID"],
                "gameMode": config["gameMode"],
                "playerScore": s,
                "leaderboardName": lb_name,
                "fullRawGameReport": {"testRun": SESSION, "submission": i+1}
            }
        }
        r = api_post("leaderboards/stats", body)
        if r["ok"] and r["body"].get("gameReportResponse", {}).get("success"):
            ok(f"Cumulative submit #{i+1}", f"score={s}")
        else:
            fail(f"Cumulative submit #{i+1}", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")

    time.sleep(0.5)

    # Query player score and verify cumulative total
    r = api_post("leaderboards/scores", {
        "leaderboardScoresRequest": {
            "leaderboardName": lb_name,
            "queryType": "playerScore",
            "playerID": player_id
        }
    })
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        player_data = resp.get("playerData", {})
        actual_score = player_data.get("score")
        expected_total = sum(cumulative_scores)
        if actual_score is not None and abs(float(actual_score) - expected_total) < 0.01:
            ok(f"Cumulative total", f"expected={expected_total}, got={actual_score}")
        else:
            fail(f"Cumulative total", f"expected={expected_total}, got={actual_score}")
    else:
        fail(f"Cumulative query", f"HTTP {r['status']}")


# =============================================================================
# PHASE 3c: BATCH-POPULATE LEADERBOARDS TO 100+ ENTRIES
# =============================================================================
def phase_batch_populate(lb_defs):
    section("PHASE 3c: BATCH-POPULATE LEADERBOARDS TO 100+ ENTRIES")

    BATCH_PLAYERS = 100  # additional players per leaderboard

    for d in lb_defs:
        tag = d["tag"]
        config = d["config"]
        lb_name = config["leaderboardName"]
        score_type = config.get("scoreType", "score")
        lb_type = config["leaderboardType"]

        subsection(f"Batch-populate {tag}: {BATCH_PLAYERS} players → {lb_name}")

        reports = []
        for i in range(BATCH_PLAYERS):
            pid = f"bulk-{tag[:6]}-{SESSION}-{i+1:03d}"

            # Generate score appropriate to the score type and bounds
            if score_type == "time":
                min_t = float(config.get("minValidTimeInSeconds", 10))
                max_t = float(config.get("maxValidTimeInSeconds", 600))
                score_val = round(random.uniform(min_t + 1, max_t - 1), 3)
            elif score_type == "rank":
                lo = int(config.get("minValidScore", 1))
                hi = int(config.get("maxValidScore", 3000))
                score_val = random.randint(lo, hi)
            elif score_type in ("distance", "level"):
                lo = int(config.get("minValidScore", 1))
                hi = int(config.get("maxValidScore", 1000))
                score_val = random.randint(lo, hi)
            else:
                lo = int(config.get("minValidScore", 0))
                hi = int(config.get("maxValidScore", 999999))
                score_val = random.randint(lo, hi)

            reports.append({
                "playerID": pid,
                "gameID": config["gameID"],
                "gameMode": config["gameMode"],
                "playerScore": score_val,
                "leaderboardName": lb_name,
                "fullRawGameReport": {"bulk": True, "index": i, "session": SESSION}
            })

        # Submit in batches of 25 (DynamoDB batch write limit)
        batch_size = 25
        total_stored = 0
        for chunk_start in range(0, len(reports), batch_size):
            chunk = reports[chunk_start:chunk_start + batch_size]
            r = api_post("leaderboards/stats/batch", {"batchGameReportBody": {"gameReports": chunk}})
            if r["ok"]:
                summary = r["body"].get("batchGameReportResponse", {}).get("summary", {})
                total_stored += summary.get("processedItems", 0)
            else:
                fail(f"Batch-populate {tag} chunk {chunk_start//batch_size + 1}",
                     f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")

        if total_stored == BATCH_PLAYERS:
            ok(f"Batch-populate {tag}", f"{total_stored}/{BATCH_PLAYERS} players stored")
        else:
            fail(f"Batch-populate {tag}", f"stored {total_stored}/{BATCH_PLAYERS}")

        time.sleep(0.3)


# =============================================================================
# PHASE 4: QUERY LEADERBOARD SCORES (top) AND VALIDATE ORDER
# =============================================================================
def phase_validate_leaderboard_order(lb_defs):
    section("PHASE 4: VALIDATE LEADERBOARD ORDER (top query)")

    for d in lb_defs:
        tag = d["tag"]
        config = d["config"]
        lb_name = config["leaderboardName"]
        expected_order = d.get("expected_top_order")

        if expected_order is None:
            printlog(f"  (skipping order check for {tag} -- cumulative strategy)")
            continue

        # Query enough to cover all players (original 5 + 100 bulk = 105)
        r = api_post("leaderboards/scores", {
            "leaderboardScoresRequest": {
                "leaderboardName": lb_name,
                "queryType": "top",
                "pageSize": 500
            }
        })

        if not r["ok"]:
            fail(f"TopQuery {tag}", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")
            continue

        resp = r["body"].get("leaderboardScoresResponse", {})
        scores_list = resp.get("scores", [])
        meta = resp.get("metadata", {})

        # Check no placeholder leaked into results
        for entry in scores_list:
            pid = entry.get("playerID", "")
            if pid.startswith("_init"):
                fail(f"TopQuery {tag} PLACEHOLDER LEAK", f"placeholder '{pid}' found in results!")
                break

        # Check total player count is at least the original + bulk (may vary due to cumulative)
        total_reported = meta.get("totalPlayers", -1)
        if total_reported >= len(expected_order):
            ok(f"TopQuery {tag} totalPlayers", f"{total_reported} (>= {len(expected_order)} original)")
        else:
            fail(f"TopQuery {tag} totalPlayers", f"expected>={len(expected_order)}, got={total_reported}")

        # Check rank sequence starts at 1 and is contiguous
        ranks = [e.get("rank") for e in scores_list]
        expected_ranks = list(range(1, len(scores_list) + 1))
        if ranks == expected_ranks:
            ok(f"TopQuery {tag} ranks contiguous", f"ranks={ranks}")
        else:
            fail(f"TopQuery {tag} ranks contiguous", f"expected={expected_ranks}, got={ranks}")

        # Validate monotonic ordering (descending or ascending depending on LB type)
        lb_type = config.get("leaderboardType", "DESCENDING_LB")
        actual_scores = []
        for entry in scores_list:
            s = entry.get("score")
            if isinstance(s, str) and ":" in s:
                actual_scores.append(s)  # time-formatted
            else:
                actual_scores.append(float(s) if s is not None else 0)

        if not any(isinstance(s, str) and ":" in s for s in actual_scores):
            # Numeric: verify monotonic order
            is_descending = lb_type == "DESCENDING_LB"
            order_ok = True
            for i in range(1, len(actual_scores)):
                if is_descending and actual_scores[i] > actual_scores[i-1]:
                    order_ok = False
                    break
                elif not is_descending and actual_scores[i] < actual_scores[i-1]:
                    order_ok = False
                    break
            if order_ok:
                ok(f"TopQuery {tag} monotonic order", f"{'descending' if is_descending else 'ascending'}, {len(actual_scores)} scores")
            else:
                fail(f"TopQuery {tag} monotonic order", f"not {'descending' if is_descending else 'ascending'}: first 5={actual_scores[:5]}")
        else:
            # Time-formatted strings — verify count is reasonable
            if len(actual_scores) >= len(expected_order):
                ok(f"TopQuery {tag} score count", f"{len(actual_scores)} scores returned")
            else:
                fail(f"TopQuery {tag} score count", f"expected>={len(expected_order)}, got={len(actual_scores)}")


# =============================================================================
# PHASE 5: PLAYER STANDING (rank, percentile, neighbours) FOR EVERY TYPE
# =============================================================================
def phase_validate_player_standing(lb_defs):
    section("PHASE 5: PLAYER STANDING (percentile + neighbours)")

    for d in lb_defs:
        tag = d["tag"]
        config = d["config"]
        lb_name = config["leaderboardName"]
        scores = d["scores"]
        expected_order = d.get("expected_top_order")

        if expected_order is None:
            # For cumulative, use a known player
            player_id = f"pcumul-{SESSION}-1"
        else:
            # Use the player with the best score (rank 1)
            player_id = f"p{tag[:6]}-{SESSION}-{scores.index(max(scores) if config['leaderboardType'] == 'DESCENDING_LB' else min(scores)) + 1}"
            # For time types, find the best (lowest) score
            if config.get("scoreType") in ("time", "rank"):
                numeric_scores = []
                for s in scores:
                    if isinstance(s, str) and ":" in s:
                        # Parse time string
                        parts = s.split(":")
                        if len(parts) == 2:
                            numeric_scores.append(float(parts[0]) * 60 + float(parts[1]))
                        elif len(parts) == 3:
                            numeric_scores.append(float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2]))
                    else:
                        numeric_scores.append(float(s))
                best_idx = numeric_scores.index(min(numeric_scores))
                player_id = f"p{tag[:6]}-{SESSION}-{best_idx + 1}"

        subsection(f"{tag}: standing for '{player_id}'")

        # Full request with percentile + neighbours
        r = api_post("leaderboards/player/standing", {
            "playerLBStandingRequest": {
                "playerID": player_id,
                "leaderboardName": lb_name,
                "includePercentile": True,
                "includeNeighbours": True,
                "neighboursCount": 3
            }
        })

        if not r["ok"]:
            fail(f"Standing {tag}", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")
            continue

        resp = r["body"].get("playerLBStandingResponse", {})
        info = resp.get("playerLBStandingInfo", {})

        # Check basic fields exist
        rank = info.get("rank")
        score = info.get("score")
        percentile = info.get("percentile")
        total_players = info.get("totalPlayers")
        neighbours = info.get("neighbours", [])

        if rank is None:
            fail(f"Standing {tag} rank", "rank is None")
        else:
            ok(f"Standing {tag} rank", f"rank={rank}")

        if score is None:
            fail(f"Standing {tag} score", "score is None")
        else:
            ok(f"Standing {tag} score", f"score={score}")

        if percentile is None:
            fail(f"Standing {tag} percentile", "percentile is None (was it included?)")
        else:
            if 0 <= percentile <= 100:
                ok(f"Standing {tag} percentile", f"percentile={percentile}")
            else:
                fail(f"Standing {tag} percentile", f"out of range: {percentile}")

        if total_players is not None and total_players >= 1:
            ok(f"Standing {tag} totalPlayers", f"totalPlayers={total_players}")
        else:
            fail(f"Standing {tag} totalPlayers", f"totalPlayers={total_players}")

        # Check neighbours
        if not neighbours:
            fail(f"Standing {tag} neighbours", "empty neighbours list")
        else:
            # Check no placeholder in neighbours
            placeholder_found = False
            for n in neighbours:
                if n.get("playerID", "").startswith("_init"):
                    placeholder_found = True
                    fail(f"Standing {tag} PLACEHOLDER IN NEIGHBOURS", f"found: {n}")
                    break

            if not placeholder_found:
                ok(f"Standing {tag} no placeholder in neighbours", f"{len(neighbours)} neighbours returned")

            # Check ranks are contiguous
            n_ranks = [n.get("rank") for n in neighbours]
            if n_ranks == sorted(n_ranks) and len(set(n_ranks)) == len(n_ranks):
                ok(f"Standing {tag} neighbour ranks contiguous", f"ranks={n_ranks}")
            else:
                fail(f"Standing {tag} neighbour ranks contiguous", f"ranks={n_ranks} (gaps or duplicates)")

            # Check isTarget flag
            target_entries = [n for n in neighbours if n.get("isTarget")]
            if len(target_entries) == 1:
                ok(f"Standing {tag} isTarget", f"found target player in neighbours")
            else:
                fail(f"Standing {tag} isTarget", f"expected 1 isTarget, found {len(target_entries)}")


# =============================================================================
# PHASE 6: BATCH STORE + VALIDATE
# =============================================================================
def phase_batch_store_and_validate(lb_defs):
    section("PHASE 6: BATCH STORE AND VALIDATE")

    # Use the score-desc-replace leaderboard for batch test
    replace_def = next((d for d in lb_defs if d["tag"] == "score-desc-replace"), None)
    if not replace_def:
        printlog("  (skipped -- no replace leaderboard defined)")
        return

    config = replace_def["config"]
    lb_name = config["leaderboardName"]

    batch_players = []
    reports = []
    for i in range(5):
        pid = f"batch-{SESSION}-{i+1}"
        score = (i + 1) * 1000
        batch_players.append({"pid": pid, "score": score})
        reports.append({
            "playerID": pid,
            "gameID": config["gameID"],
            "gameMode": config["gameMode"],
            "playerScore": score,
            "leaderboardName": lb_name,
            "fullRawGameReport": {"testRun": SESSION, "batch": True, "index": i}
        })

    r = api_post("leaderboards/stats/batch", {"batchGameReportBody": {"gameReports": reports}})
    if r["ok"]:
        resp = r["body"].get("batchGameReportResponse", {})
        summary = resp.get("summary", {})
        if summary.get("totalItems") == 5 and summary.get("processedItems") == 5:
            ok("Batch store 5 players", f"all processed successfully")
        else:
            fail("Batch store 5 players", f"summary={json.dumps(summary, default=str)}")
    else:
        fail("Batch store", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")
        return

    time.sleep(0.5)

    # Validate each batch player via playerScore query
    for bp in batch_players:
        r = api_post("leaderboards/scores", {
            "leaderboardScoresRequest": {
                "leaderboardName": lb_name,
                "queryType": "playerScore",
                "playerID": bp["pid"]
            }
        })
        if r["ok"]:
            resp = r["body"].get("leaderboardScoresResponse", {})
            pd = resp.get("playerData", {})
            actual = pd.get("score")
            if actual is not None and abs(float(actual) - bp["score"]) < 0.01:
                ok(f"Batch verify {bp['pid']}", f"score={actual}")
            else:
                fail(f"Batch verify {bp['pid']}", f"expected={bp['score']}, got={actual}")
        else:
            fail(f"Batch verify {bp['pid']}", f"HTTP {r['status']}")


# =============================================================================
# PHASE 7: GET PLAYER STATS AND SCORES (DynamoDB query)
# =============================================================================
def phase_validate_player_stats(lb_defs):
    section("PHASE 7: GET PLAYER STATS AND SCORES (DynamoDB)")

    # Use first leaderboard def as sample
    d = lb_defs[0]
    config = d["config"]
    lb_name = config["leaderboardName"]
    player_id = f"p{d['tag'][:6]}-{SESSION}-1"

    r = api_post("leaderboards/player/stats", {
        "playerStatsAndScoresRequest": {
            "playerID": player_id,
            "gameID": config["gameID"],
            "gameMode": config["gameMode"],
            "limit": 10
        }
    })

    if not r["ok"]:
        fail(f"PlayerStats query", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")
        return

    body = r["body"]
    ps_resp = body.get("playerStatsAndScoresResponse", {})
    results = ps_resp.get("results", {})
    stats = results.get("playerStats", [])
    summary = results.get("statsSummary", {})

    if len(stats) >= 1:
        ok(f"PlayerStats has records", f"{len(stats)} records found for {player_id}")
    else:
        fail(f"PlayerStats has records", f"0 records found for {player_id}")
        return

    # Verify the record has expected fields
    first_stat = stats[0]
    required_fields = ["playerID", "sortKey", "gameID", "gameMode", "playerScore", "leaderboardName"]
    missing = [f for f in required_fields if f not in first_stat]
    if not missing:
        ok(f"PlayerStats record fields", "all required fields present")
    else:
        fail(f"PlayerStats record fields", f"missing: {missing}")

    # Check summary
    if summary.get("totalRecords", 0) >= 1:
        ok(f"PlayerStats summary", f"totalRecords={summary['totalRecords']}")
    else:
        fail(f"PlayerStats summary", f"totalRecords={summary.get('totalRecords')}")


# =============================================================================
# PHASE 8: RANGE QUERY + aroundPlayer QUERY
# =============================================================================
def phase_validate_range_and_around(lb_defs):
    section("PHASE 8: RANGE QUERY AND aroundPlayer QUERY")

    d = lb_defs[0]  # score-desc-best
    config = d["config"]
    lb_name = config["leaderboardName"]

    # Range query
    r = api_post("leaderboards/scores", {
        "leaderboardScoresRequest": {
            "leaderboardName": lb_name,
            "queryType": "range",
            "minScore": 3000,
            "maxScore": 9000,
            "inclusive": True,
            "pageSize": 20
        }
    })
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores_in_range = resp.get("scores", [])
        # Expected scores in 3000-9000: 5000, 8000, 3000, 7500 → 4 entries
        # Check no placeholder
        placeholder_in = any(s.get("playerID", "").startswith("_init") for s in scores_in_range)
        if placeholder_in:
            fail(f"Range query placeholder leak", "placeholder found in range results")
        else:
            if len(scores_in_range) >= 1:
                ok(f"Range query", f"{len(scores_in_range)} results in range [3000, 9000]")
            else:
                fail(f"Range query", "0 results returned")
    else:
        fail(f"Range query", f"HTTP {r['status']}")

    # aroundPlayer query
    middle_player = f"p{d['tag'][:6]}-{SESSION}-3"  # player who submitted score at index 2
    r = api_post("leaderboards/scores", {
        "leaderboardScoresRequest": {
            "leaderboardName": lb_name,
            "queryType": "aroundPlayer",
            "playerID": middle_player,
            "countBefore": 2,
            "countAfter": 2
        }
    })
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        around_scores = resp.get("scores", [])

        # Check no placeholder
        placeholder_in = any(s.get("playerID", "").startswith("_init") for s in around_scores)
        if placeholder_in:
            fail(f"AroundPlayer placeholder leak", "placeholder found in around results")
        else:
            ok(f"AroundPlayer query", f"{len(around_scores)} neighbours returned")

        # Check ranks are contiguous
        ranks = [s.get("rank") for s in around_scores]
        if ranks == sorted(ranks) and len(set(ranks)) == len(ranks):
            ok(f"AroundPlayer ranks contiguous", f"ranks={ranks}")
        else:
            fail(f"AroundPlayer ranks contiguous", f"ranks={ranks}")
    else:
        fail(f"AroundPlayer query", f"HTTP {r['status']}")


# =============================================================================
# PHASE 9: DEEP TIME-BASED LEADERBOARD TESTS
# =============================================================================
def phase_deep_time_tests():
    section("PHASE 9: DEEP TIME-BASED LEADERBOARD TESTS")

    GAME = GAME_ID

    # -------------------------------------------------------------------------
    # 9a. MILLISECONDS format — numeric input divided by 1000
    # -------------------------------------------------------------------------
    subsection("9a: Milliseconds format (numeric input)")
    lb_ms = f"tdeep-millis-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "sprint-ms",
        "leaderboardName": lb_ms,
        "statAttributeForLeaderboard": "sprintTime",
        "leaderboardType": "ASCENDING_LB",
        "scoreStrategy": "best",
        "scoreType": "time", "sortOrder": "asc",
        "timeFormat": "milliseconds", "timePrecision": 0,
        "minValidTimeInSeconds": 0.5, "maxValidTimeInSeconds": 30.0
    }}, expected_status=201)
    if r["ok"]:
        ok("Create millis LB", lb_ms)
        created_leaderboards.append(lb_ms)
    else:
        fail("Create millis LB", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")
        return  # can't continue time tests without LBs

    # Submit in milliseconds: 5200ms = 5.2s, 3800ms = 3.8s, 12500ms = 12.5s
    ms_players = [
        ("msplayer-1", 5200),   # 5.2s
        ("msplayer-2", 3800),   # 3.8s
        ("msplayer-3", 12500),  # 12.5s
        ("msplayer-4", 1500),   # 1.5s
        ("msplayer-5", 8000),   # 8.0s
    ]
    for pid, ms_val in ms_players:
        pid_full = f"{pid}-{SESSION}"
        r = api_post("leaderboards/stats", {"gameReportBody": {
            "playerID": pid_full, "gameID": GAME, "gameMode": "sprint-ms",
            "playerScore": ms_val, "leaderboardName": lb_ms,
            "fullRawGameReport": {"raw_ms": ms_val}
        }})
        if r["ok"] and r["body"].get("gameReportResponse", {}).get("success"):
            ok(f"Store millis {pid}", f"{ms_val}ms")
        else:
            fail(f"Store millis {pid}", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")

    time.sleep(0.3)

    # Query top and validate ordering (ascending by seconds, displayed in ms)
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_ms, "queryType": "top", "pageSize": 10
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        displayed = [s.get("score") for s in scores]
        # With timePrecision=0, seconds are rounded to whole numbers before storage:
        # 1500ms→1.5s→round(1.5,0)=2.0s, 3800ms→3.8s→4.0s, 5200ms→5.2s→5.0s,
        # 8000ms→8.0s→8.0s, 12500ms→12.5s→12.0s
        # Displayed back as ms: 2000, 4000, 5000, 8000, 12000
        expected_ms = [2000.0, 4000.0, 5000.0, 8000.0, 12000.0]
        if len(displayed) == 5:
            order_ok = all(abs(float(d) - e) < 1.0 for d, e in zip(displayed, expected_ms))
            if order_ok:
                ok("Millis display order", f"displayed={displayed}")
            else:
                fail("Millis display order", f"expected≈{expected_ms}, got={displayed}")
        else:
            fail("Millis count", f"expected 5, got {len(displayed)}")

        # Verify scoreType/timeFormat/timePrecision in response
        st = resp.get("scoreType")
        tf = resp.get("timeFormat")
        tp = resp.get("timePrecision")
        if st == "time" and tf == "milliseconds":
            ok("Millis response meta", f"scoreType={st}, timeFormat={tf}, timePrecision={tp}")
        else:
            fail("Millis response meta", f"scoreType={st}, timeFormat={tf}, timePrecision={tp}")
    else:
        fail("Millis top query", f"HTTP {r['status']}")

    # -------------------------------------------------------------------------
    # 9b. BEST strategy — submit worse time, verify original kept
    # -------------------------------------------------------------------------
    subsection("9b: Best strategy — worse time rejected")
    lb_best = f"tdeep-best-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "lap-best",
        "leaderboardName": lb_best,
        "statAttributeForLeaderboard": "lapTime",
        "leaderboardType": "ASCENDING_LB",
        "scoreStrategy": "best",
        "scoreType": "time", "sortOrder": "asc",
        "timeFormat": "seconds", "timePrecision": 3,
        "minValidTimeInSeconds": 1.0, "maxValidTimeInSeconds": 600.0
    }}, expected_status=201)
    if r["ok"]:
        ok("Create best-time LB", lb_best)
        created_leaderboards.append(lb_best)
    else:
        fail("Create best-time LB", f"HTTP {r['status']}")

    best_pid = f"racer-best-{SESSION}"
    # Submit good time: 45.123s
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": best_pid, "gameID": GAME, "gameMode": "lap-best",
        "playerScore": 45.123, "leaderboardName": lb_best,
        "fullRawGameReport": {"lap": 1}
    }})
    if r["ok"]:
        ok("Best strategy — submit 45.123s", "stored")
    else:
        fail("Best strategy — submit 45.123s", f"HTTP {r['status']}")

    # Submit WORSE time: 90.5s — should NOT replace
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": best_pid, "gameID": GAME, "gameMode": "lap-best",
        "playerScore": 90.5, "leaderboardName": lb_best,
        "fullRawGameReport": {"lap": 2}
    }})
    if r["ok"]:
        ok("Best strategy — submit 90.5s (worse)", "accepted by API")
    else:
        fail("Best strategy — submit 90.5s", f"HTTP {r['status']}")

    time.sleep(0.3)

    # Verify score is still 45.123, not 90.5
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_best, "queryType": "playerScore", "playerID": best_pid
    }})
    if r["ok"]:
        actual = r["body"].get("leaderboardScoresResponse", {}).get("playerData", {}).get("score")
        if actual is not None and abs(float(actual) - 45.123) < 0.01:
            ok("Best strategy — kept better time", f"score={actual} (45.123 kept, 90.5 rejected)")
        else:
            fail("Best strategy — kept better time", f"expected≈45.123, got={actual}")
    else:
        fail("Best strategy query", f"HTTP {r['status']}")

    # Submit BETTER time: 38.999s — should replace
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": best_pid, "gameID": GAME, "gameMode": "lap-best",
        "playerScore": 38.999, "leaderboardName": lb_best,
        "fullRawGameReport": {"lap": 3}
    }})
    time.sleep(0.3)
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_best, "queryType": "playerScore", "playerID": best_pid
    }})
    if r["ok"]:
        actual = r["body"].get("leaderboardScoresResponse", {}).get("playerData", {}).get("score")
        if actual is not None and abs(float(actual) - 38.999) < 0.01:
            ok("Best strategy — accepted better time", f"score={actual} (38.999 replaced 45.123)")
        else:
            fail("Best strategy — accepted better time", f"expected≈38.999, got={actual}")
    else:
        fail("Best strategy update query", f"HTTP {r['status']}")

    # -------------------------------------------------------------------------
    # 9c. REPLACE strategy — always overwrites regardless of better/worse
    # -------------------------------------------------------------------------
    subsection("9c: Replace strategy — always overwrites")
    lb_repl = f"tdeep-repl-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "lap-replace",
        "leaderboardName": lb_repl,
        "statAttributeForLeaderboard": "lapTime",
        "leaderboardType": "ASCENDING_LB",
        "scoreStrategy": "replace",
        "scoreType": "time", "sortOrder": "asc",
        "timeFormat": "seconds", "timePrecision": 3,
        "minValidTimeInSeconds": 1.0, "maxValidTimeInSeconds": 600.0
    }}, expected_status=201)
    if r["ok"]:
        ok("Create replace-time LB", lb_repl)
        created_leaderboards.append(lb_repl)
    else:
        fail("Create replace-time LB", f"HTTP {r['status']}")

    repl_pid = f"racer-repl-{SESSION}"
    # Submit 50s, then 80s (worse) — replace should use 80s
    api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": repl_pid, "gameID": GAME, "gameMode": "lap-replace",
        "playerScore": 50.0, "leaderboardName": lb_repl,
        "fullRawGameReport": {"lap": 1}
    }})
    api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": repl_pid, "gameID": GAME, "gameMode": "lap-replace",
        "playerScore": 80.0, "leaderboardName": lb_repl,
        "fullRawGameReport": {"lap": 2}
    }})
    time.sleep(0.3)

    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_repl, "queryType": "playerScore", "playerID": repl_pid
    }})
    if r["ok"]:
        actual = r["body"].get("leaderboardScoresResponse", {}).get("playerData", {}).get("score")
        if actual is not None and abs(float(actual) - 80.0) < 0.01:
            ok("Replace strategy — overwrote with worse", f"score={actual} (80.0 replaced 50.0)")
        else:
            fail("Replace strategy — overwrote with worse", f"expected≈80.0, got={actual}")
    else:
        fail("Replace strategy query", f"HTTP {r['status']}")

    # -------------------------------------------------------------------------
    # 9d. CUMULATIVE strategy for time — adds lap times
    # -------------------------------------------------------------------------
    subsection("9d: Cumulative strategy — adds times together")
    lb_cumul = f"tdeep-cumul-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "endurance",
        "leaderboardName": lb_cumul,
        "statAttributeForLeaderboard": "totalTime",
        "leaderboardType": "ASCENDING_LB",
        "scoreStrategy": "cumulative",
        "scoreType": "time", "sortOrder": "asc",
        "timeFormat": "seconds", "timePrecision": 3,
        "minValidTimeInSeconds": 1.0, "maxValidTimeInSeconds": 86400.0
    }}, expected_status=201)
    if r["ok"]:
        ok("Create cumul-time LB", lb_cumul)
        created_leaderboards.append(lb_cumul)
    else:
        fail("Create cumul-time LB", f"HTTP {r['status']}")

    cumul_pid = f"racer-cumul-{SESSION}"
    cumul_times = [30.5, 45.25, 22.75]  # total = 98.5
    for i, t in enumerate(cumul_times):
        api_post("leaderboards/stats", {"gameReportBody": {
            "playerID": cumul_pid, "gameID": GAME, "gameMode": "endurance",
            "playerScore": t, "leaderboardName": lb_cumul,
            "fullRawGameReport": {"lap": i + 1}
        }})
    time.sleep(0.3)

    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_cumul, "queryType": "playerScore", "playerID": cumul_pid
    }})
    if r["ok"]:
        actual = r["body"].get("leaderboardScoresResponse", {}).get("playerData", {}).get("score")
        expected = sum(cumul_times)  # 98.5
        if actual is not None and abs(float(actual) - expected) < 0.1:
            ok("Cumulative time total", f"score={actual} (expected≈{expected})")
        else:
            fail("Cumulative time total", f"expected≈{expected}, got={actual}")
    else:
        fail("Cumulative time query", f"HTTP {r['status']}")

    # -------------------------------------------------------------------------
    # 9e. MIXED INPUT FORMATS to same leaderboard
    #     Submit numeric, string "MM:SS", string "HH:MM:SS", plain string
    # -------------------------------------------------------------------------
    subsection("9e: Mixed input formats to same leaderboard")
    lb_mix = f"tdeep-mixed-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "rally-mix",
        "leaderboardName": lb_mix,
        "statAttributeForLeaderboard": "stageTime",
        "leaderboardType": "ASCENDING_LB",
        "scoreStrategy": "best",
        "scoreType": "time", "sortOrder": "asc",
        "timeFormat": "minutes_seconds", "timePrecision": 3,
        "minValidTimeInSeconds": 10.0, "maxValidTimeInSeconds": 7200.0
    }}, expected_status=201)
    if r["ok"]:
        ok("Create mixed-input LB", lb_mix)
        created_leaderboards.append(lb_mix)
    else:
        fail("Create mixed-input LB", f"HTTP {r['status']}")

    # Each submits a different format, all should parse to seconds internally
    mixed_submissions = [
        ("mixfmt-1", 95.5,        "numeric float (95.5s)"),       # 95.5s
        ("mixfmt-2", "1:35.500",  "MM:SS string (95.5s)"),        # 95.5s — same time, different format
        ("mixfmt-3", "0:30:15.0", "HH:MM:SS string (1815.0s)"),   # 1815s
        ("mixfmt-4", "125.75",    "plain numeric string (125.75s)"), # 125.75s
        ("mixfmt-5", 3600,        "numeric int (3600s = 1hr)"),    # 3600s
    ]
    for pid_base, score_val, desc in mixed_submissions:
        pid_full = f"{pid_base}-{SESSION}"
        r = api_post("leaderboards/stats", {"gameReportBody": {
            "playerID": pid_full, "gameID": GAME, "gameMode": "rally-mix",
            "playerScore": score_val, "leaderboardName": lb_mix,
            "fullRawGameReport": {"format": desc}
        }})
        if r["ok"] and r["body"].get("gameReportResponse", {}).get("success"):
            ok(f"Mixed input {pid_base}", f"{desc}")
        else:
            fail(f"Mixed input {pid_base}", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")

    time.sleep(0.3)

    # Query and validate ordering
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_mix, "queryType": "top", "pageSize": 10
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        # mixfmt-1 and mixfmt-2 both submitted 95.5s — best strategy, so only one score per player
        # Expected ascending order by seconds: 95.5(fmt1), 95.5(fmt2), 125.75(fmt4), 1815(fmt3), 3600(fmt5)
        ranks = [s.get("rank") for s in scores]
        displayed = [s.get("score") for s in scores]

        if len(scores) == 5:
            ok("Mixed input — 5 players returned", f"displayed={displayed}")
        else:
            fail("Mixed input — player count", f"expected=5, got={len(scores)}")

        if ranks == list(range(1, len(ranks) + 1)):
            ok("Mixed input — ranks contiguous", f"ranks={ranks}")
        else:
            fail("Mixed input — ranks contiguous", f"ranks={ranks}")

        # Display should be in minutes_seconds format (e.g., "1:35.500")
        if len(scores) >= 1:
            first_score = str(scores[0].get("score", ""))
            if ":" in first_score:
                ok("Mixed input — displayed as MM:SS", f"first score: {first_score}")
            else:
                fail("Mixed input — display format", f"expected MM:SS format, got: {first_score}")
    else:
        fail("Mixed input top query", f"HTTP {r['status']}")

    # -------------------------------------------------------------------------
    # 9f. PRECISION variations — timePrecision 0, 1, 6
    # -------------------------------------------------------------------------
    subsection("9f: Time precision variations")
    for prec in [0, 1, 6]:
        lb_prec = f"tdeep-prec{prec}-{SESSION}"
        r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
            "gameID": GAME, "gameMode": f"prec{prec}",
            "leaderboardName": lb_prec,
            "statAttributeForLeaderboard": "lapTime",
            "leaderboardType": "ASCENDING_LB",
            "scoreStrategy": "best",
            "scoreType": "time", "sortOrder": "asc",
            "timeFormat": "seconds", "timePrecision": prec,
            "minValidTimeInSeconds": 1.0, "maxValidTimeInSeconds": 600.0
        }}, expected_status=201)
        if r["ok"]:
            ok(f"Create precision-{prec} LB", lb_prec)
            created_leaderboards.append(lb_prec)
        else:
            fail(f"Create precision-{prec} LB", f"HTTP {r['status']}")
            continue

        pid_prec = f"precplayer-{prec}-{SESSION}"
        # Submit 83.456789 and verify rounding
        api_post("leaderboards/stats", {"gameReportBody": {
            "playerID": pid_prec, "gameID": GAME, "gameMode": f"prec{prec}",
            "playerScore": 83.456789, "leaderboardName": lb_prec,
            "fullRawGameReport": {"precision_test": prec}
        }})
        time.sleep(0.2)

        r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
            "leaderboardName": lb_prec, "queryType": "playerScore", "playerID": pid_prec
        }})
        if r["ok"]:
            actual = r["body"].get("leaderboardScoresResponse", {}).get("playerData", {}).get("score")
            expected_rounded = round(83.456789, prec)
            if actual is not None and abs(float(actual) - expected_rounded) < (10 ** -prec if prec > 0 else 1.0):
                ok(f"Precision {prec} — display", f"got={actual}, expected≈{expected_rounded}")
            else:
                fail(f"Precision {prec} — display", f"got={actual}, expected≈{expected_rounded}")
        else:
            fail(f"Precision {prec} query", f"HTTP {r['status']}")

    # -------------------------------------------------------------------------
    # 9g. BOUNDARY VALIDATION — reject out-of-range times
    # -------------------------------------------------------------------------
    subsection("9g: Time boundary validation (reject out-of-range)")
    lb_bounds = f"tdeep-bounds-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "bounds-test",
        "leaderboardName": lb_bounds,
        "statAttributeForLeaderboard": "raceTime",
        "leaderboardType": "ASCENDING_LB",
        "scoreStrategy": "best",
        "scoreType": "time", "sortOrder": "asc",
        "timeFormat": "seconds", "timePrecision": 3,
        "minValidTimeInSeconds": 10.0, "maxValidTimeInSeconds": 300.0
    }}, expected_status=201)
    if r["ok"]:
        ok("Create bounds LB", f"{lb_bounds} (10s-300s)")
        created_leaderboards.append(lb_bounds)
    else:
        fail("Create bounds LB", f"HTTP {r['status']}")
        return

    # Valid: exactly at min boundary (10.0s)
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": f"bound-min-{SESSION}", "gameID": GAME, "gameMode": "bounds-test",
        "playerScore": 10.0, "leaderboardName": lb_bounds,
        "fullRawGameReport": {"test": "at-min"}
    }})
    if r["ok"] and r["body"].get("gameReportResponse", {}).get("success"):
        ok("Boundary — accept 10.0s (at min)", "stored")
    else:
        fail("Boundary — accept 10.0s", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:150]}")

    # Valid: exactly at max boundary (300.0s)
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": f"bound-max-{SESSION}", "gameID": GAME, "gameMode": "bounds-test",
        "playerScore": 300.0, "leaderboardName": lb_bounds,
        "fullRawGameReport": {"test": "at-max"}
    }})
    if r["ok"] and r["body"].get("gameReportResponse", {}).get("success"):
        ok("Boundary — accept 300.0s (at max)", "stored")
    else:
        fail("Boundary — accept 300.0s", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:150]}")

    # Invalid: below min (5.0s < 10.0s)
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": f"bound-under-{SESSION}", "gameID": GAME, "gameMode": "bounds-test",
        "playerScore": 5.0, "leaderboardName": lb_bounds,
        "fullRawGameReport": {"test": "below-min"}
    }})
    if r["status"] == 400:
        ok("Boundary — reject 5.0s (below min)", f"HTTP 400 as expected")
    else:
        fail("Boundary — reject 5.0s", f"expected HTTP 400, got {r['status']}: {json.dumps(r['body'], default=str)[:150]}")

    # Invalid: above max (500.0s > 300.0s)
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": f"bound-over-{SESSION}", "gameID": GAME, "gameMode": "bounds-test",
        "playerScore": 500.0, "leaderboardName": lb_bounds,
        "fullRawGameReport": {"test": "above-max"}
    }})
    if r["status"] == 400:
        ok("Boundary — reject 500.0s (above max)", f"HTTP 400 as expected")
    else:
        fail("Boundary — reject 500.0s", f"expected HTTP 400, got {r['status']}: {json.dumps(r['body'], default=str)[:150]}")

    # Invalid: above max via MM:SS string ("6:00.000" = 360s > 300s)
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": f"bound-strover-{SESSION}", "gameID": GAME, "gameMode": "bounds-test",
        "playerScore": "6:00.000", "leaderboardName": lb_bounds,
        "fullRawGameReport": {"test": "string-above-max"}
    }})
    if r["status"] == 400:
        ok("Boundary — reject '6:00.000' (360s > 300s max)", f"HTTP 400 as expected")
    else:
        fail("Boundary — reject '6:00.000'", f"expected HTTP 400, got {r['status']}: {json.dumps(r['body'], default=str)[:150]}")

    # -------------------------------------------------------------------------
    # 9h. BATCH STORE with time scores — multiple formats in one batch
    # -------------------------------------------------------------------------
    subsection("9h: Batch store with mixed time formats")
    lb_batch_time = f"tdeep-batchtm-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "rally-batch",
        "leaderboardName": lb_batch_time,
        "statAttributeForLeaderboard": "stageTime",
        "leaderboardType": "ASCENDING_LB",
        "scoreStrategy": "best",
        "scoreType": "time", "sortOrder": "asc",
        "timeFormat": "minutes_seconds", "timePrecision": 3,
        "minValidTimeInSeconds": 10.0, "maxValidTimeInSeconds": 7200.0
    }}, expected_status=201)
    if r["ok"]:
        ok("Create batch-time LB", lb_batch_time)
        created_leaderboards.append(lb_batch_time)
    else:
        fail("Create batch-time LB", f"HTTP {r['status']}")
        return

    batch_reports = [
        {"playerID": f"btracer-1-{SESSION}", "gameID": GAME, "gameMode": "rally-batch",
         "playerScore": "2:30.000", "leaderboardName": lb_batch_time,
         "fullRawGameReport": {"format": "MM:SS"}},
        {"playerID": f"btracer-2-{SESSION}", "gameID": GAME, "gameMode": "rally-batch",
         "playerScore": 180.5, "leaderboardName": lb_batch_time,
         "fullRawGameReport": {"format": "numeric"}},
        {"playerID": f"btracer-3-{SESSION}", "gameID": GAME, "gameMode": "rally-batch",
         "playerScore": "0:10:15.250", "leaderboardName": lb_batch_time,
         "fullRawGameReport": {"format": "HH:MM:SS"}},
        {"playerID": f"btracer-4-{SESSION}", "gameID": GAME, "gameMode": "rally-batch",
         "playerScore": "95.0", "leaderboardName": lb_batch_time,
         "fullRawGameReport": {"format": "numeric string"}},
    ]
    r = api_post("leaderboards/stats/batch", {"batchGameReportBody": {"gameReports": batch_reports}})
    if r["ok"]:
        summary = r["body"].get("batchGameReportResponse", {}).get("summary", {})
        if summary.get("processedItems") == 4 and summary.get("validationErrors", 0) == 0:
            ok("Batch time store", f"4/4 processed, 0 errors")
        else:
            fail("Batch time store", f"summary={json.dumps(summary, default=str)}")

        time_updates = summary.get("leaderboardUpdates", {}).get("timeBasedUpdates", 0)
        if time_updates == 4:
            ok("Batch time — timeBasedUpdates count", f"{time_updates}")
        else:
            fail("Batch time — timeBasedUpdates count", f"expected=4, got={time_updates}")
    else:
        fail("Batch time store", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")

    time.sleep(0.5)

    # Query and validate order: 95.0s, 150.0s (2:30), 180.5s, 615.25s (0:10:15.250)
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_batch_time, "queryType": "top", "pageSize": 10
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        ranks = [s.get("rank") for s in scores]
        displayed = [s.get("score") for s in scores]

        if ranks == [1, 2, 3, 4]:
            ok("Batch time order — ranks", f"ranks={ranks}")
        else:
            fail("Batch time order — ranks", f"expected=[1,2,3,4], got={ranks}")

        if len(scores) == 4:
            ok("Batch time order — count", f"{len(scores)} scores, displayed={displayed}")
        else:
            fail("Batch time order — count", f"expected=4, got={len(scores)}")
    else:
        fail("Batch time top query", f"HTTP {r['status']}")

    # -------------------------------------------------------------------------
    # 9i. PLAYER STANDING for time LB — rank, percentile, neighbours
    # -------------------------------------------------------------------------
    subsection("9i: Player standing on time leaderboard")
    # Use batch-time LB which has 4 players. Query middle player.
    mid_pid = f"btracer-2-{SESSION}"  # 180.5s → should be rank 3 of 4
    r = api_post("leaderboards/player/standing", {"playerLBStandingRequest": {
        "playerID": mid_pid, "leaderboardName": lb_batch_time,
        "includePercentile": True,
        "includeNeighbours": True, "neighboursCount": 3
    }})
    if r["ok"]:
        resp = r["body"].get("playerLBStandingResponse", {})
        info = resp.get("playerLBStandingInfo", {})

        rank = info.get("rank")
        score = info.get("score")
        percentile = info.get("percentile")
        total = info.get("totalPlayers")
        neighbours = info.get("neighbours", [])

        if rank == 3:
            ok("Time standing rank", f"rank={rank} (180.5s is 3rd of 4)")
        else:
            fail("Time standing rank", f"expected=3, got={rank}")

        if score is not None and ":" in str(score):
            ok("Time standing display format", f"score={score} (MM:SS format)")
        else:
            fail("Time standing display format", f"score={score} (expected MM:SS string)")

        if percentile is not None and 0 <= percentile <= 100:
            ok("Time standing percentile", f"percentile={percentile}")
        else:
            fail("Time standing percentile", f"percentile={percentile}")

        if total == 4:
            ok("Time standing totalPlayers", f"totalPlayers={total}")
        else:
            fail("Time standing totalPlayers", f"expected=4, got={total}")

        # Check no placeholder in neighbours
        placeholder_found = any(n.get("playerID", "").startswith("_init") for n in neighbours)
        if not placeholder_found:
            ok("Time standing — no placeholder in neighbours", f"{len(neighbours)} neighbours")
        else:
            fail("Time standing — PLACEHOLDER LEAK", "placeholder found in neighbours!")

        # Neighbours ranks contiguous
        n_ranks = [n.get("rank") for n in neighbours]
        if n_ranks == sorted(n_ranks) and len(set(n_ranks)) == len(n_ranks):
            ok("Time standing — neighbour ranks contiguous", f"ranks={n_ranks}")
        else:
            fail("Time standing — neighbour ranks", f"ranks={n_ranks}")

        # isTarget present
        targets = [n for n in neighbours if n.get("isTarget")]
        if len(targets) == 1 and targets[0].get("rank") == rank:
            ok("Time standing — isTarget correct", f"target at rank {rank}")
        else:
            fail("Time standing — isTarget", f"targets={targets}")
    else:
        fail("Time standing query", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")

    # -------------------------------------------------------------------------
    # 9j. RANGE QUERY on time leaderboard
    # -------------------------------------------------------------------------
    subsection("9j: Range query on time leaderboard")
    # Query for times between 90s and 200s — should get btracer-4 (95s) and btracer-1 (150s) and btracer-2 (180.5s)
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_batch_time, "queryType": "range",
        "minScore": 90.0, "maxScore": 200.0,
        "inclusive": True, "pageSize": 10
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        pids = [s.get("playerID") for s in scores]

        if len(scores) == 3:
            ok("Time range query", f"3 results in [90s, 200s]: {pids}")
        else:
            fail("Time range query", f"expected 3 results, got {len(scores)}: {pids}")

        placeholder_found = any(s.get("playerID", "").startswith("_init") for s in scores)
        if not placeholder_found:
            ok("Time range — no placeholder leak", "clean")
        else:
            fail("Time range — PLACEHOLDER LEAK", "placeholder in range results!")
    else:
        fail("Time range query", f"HTTP {r['status']}")


# =============================================================================
# PHASE 10: PLACEHOLDER EDGE-CASE TESTS
#
# These test the specific scenarios our code fixes address:
# - Query a just-created leaderboard BEFORE any scores (placeholder only)
# - Add 1 score, verify rank=1 with no gap from placeholder
# - Verify totalPlayers excludes the placeholder
# - Verify percentile is 100% for the only real player
# - Verify neighbours don't include the placeholder
# - Repeat for ASCENDING_LB (where placeholder is at position 0 in zrevrange)
# =============================================================================
def phase_placeholder_edge_cases():
    section("PHASE 10: PLACEHOLDER EDGE-CASE TESTS (the fixes we deployed)")

    GAME = GAME_ID

    # -------------------------------------------------------------------------
    # 10a. DESCENDING_LB — query empty (placeholder-only), then add 1 score
    # -------------------------------------------------------------------------
    subsection("10a: DESCENDING_LB — empty leaderboard then single score")
    lb_desc = f"tedge-desc-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "edge-desc",
        "leaderboardName": lb_desc,
        "statAttributeForLeaderboard": "score",
        "leaderboardType": "DESCENDING_LB",
        "scoreStrategy": "best",
        "scoreType": "score",
        "minValidScore": 0, "maxValidScore": 999999
    }}, expected_status=201)
    if r["ok"]:
        ok("Create edge DESC LB", lb_desc)
        created_leaderboards.append(lb_desc)
    else:
        fail("Create edge DESC LB", f"HTTP {r['status']}")
        return

    # Query top IMMEDIATELY (only placeholder should exist)
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_desc, "queryType": "top", "pageSize": 10
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        meta = resp.get("metadata", {})
        total = meta.get("totalPlayers", -1)

        if len(scores) == 0:
            ok("Edge DESC — empty LB returns 0 scores", "placeholder filtered")
        else:
            pids = [s.get("playerID") for s in scores]
            fail("Edge DESC — empty LB returns 0 scores", f"got {len(scores)} scores: {pids}")

        if total == 0:
            ok("Edge DESC — totalPlayers=0 (placeholder excluded)", f"totalPlayers={total}")
        else:
            fail("Edge DESC — totalPlayers=0", f"got totalPlayers={total}")
    else:
        fail("Edge DESC — empty top query", f"HTTP {r['status']}")

    # Add exactly 1 score
    pid_desc = f"edge-desc-solo-{SESSION}"
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": pid_desc, "gameID": GAME, "gameMode": "edge-desc",
        "playerScore": 5000, "leaderboardName": lb_desc,
        "fullRawGameReport": {"test": "edge-desc"}
    }})
    if r["ok"] and r["body"].get("gameReportResponse", {}).get("success"):
        ok("Edge DESC — store 1 score", "5000 stored")
    else:
        fail("Edge DESC — store 1 score", f"HTTP {r['status']}")

    time.sleep(0.3)

    # Query top — should show rank=1, totalPlayers=1
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_desc, "queryType": "top", "pageSize": 10
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        meta = resp.get("metadata", {})

        if len(scores) == 1:
            ok("Edge DESC — 1 score returned", f"playerID={scores[0].get('playerID')}")
        else:
            pids = [s.get("playerID") for s in scores]
            fail("Edge DESC — 1 score returned", f"got {len(scores)}: {pids}")

        if len(scores) == 1 and scores[0].get("rank") == 1:
            ok("Edge DESC — rank=1 (no gap from placeholder)", f"rank={scores[0].get('rank')}")
        elif len(scores) == 1:
            fail("Edge DESC — rank=1", f"got rank={scores[0].get('rank')} (placeholder rank gap!)")

        total = meta.get("totalPlayers", -1)
        if total == 1:
            ok("Edge DESC — totalPlayers=1 (placeholder excluded)", f"totalPlayers={total}")
        else:
            fail("Edge DESC — totalPlayers=1", f"got {total}")
    else:
        fail("Edge DESC — 1-score top query", f"HTTP {r['status']}")

    # playerScore query — rank=1, percentile
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_desc, "queryType": "playerScore", "playerID": pid_desc
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        pd = resp.get("playerData", {})
        if pd.get("rank") == 1:
            ok("Edge DESC playerScore — rank=1", f"rank={pd.get('rank')}")
        else:
            fail("Edge DESC playerScore — rank=1", f"got rank={pd.get('rank')}")

        if pd.get("totalPlayers") == 1:
            ok("Edge DESC playerScore — totalPlayers=1", f"totalPlayers={pd.get('totalPlayers')}")
        else:
            fail("Edge DESC playerScore — totalPlayers", f"got {pd.get('totalPlayers')}")
    else:
        fail("Edge DESC playerScore query", f"HTTP {r['status']}")

    # Player standing — rank=1, percentile=100, no placeholder in neighbours
    r = api_post("leaderboards/player/standing", {"playerLBStandingRequest": {
        "playerID": pid_desc, "leaderboardName": lb_desc,
        "includePercentile": True, "includeNeighbours": True, "neighboursCount": 3
    }})
    if r["ok"]:
        resp = r["body"].get("playerLBStandingResponse", {})
        info = resp.get("playerLBStandingInfo", {})

        if info.get("rank") == 1:
            ok("Edge DESC standing — rank=1", f"rank={info.get('rank')}")
        else:
            fail("Edge DESC standing — rank=1", f"got rank={info.get('rank')}")

        if info.get("totalPlayers") == 1:
            ok("Edge DESC standing — totalPlayers=1", f"totalPlayers={info.get('totalPlayers')}")
        else:
            fail("Edge DESC standing — totalPlayers", f"got {info.get('totalPlayers')}")

        pctl = info.get("percentile")
        if pctl is not None and abs(float(pctl) - 100.0) < 0.01:
            ok("Edge DESC standing — percentile=100%", f"percentile={pctl}")
        else:
            fail("Edge DESC standing — percentile=100%", f"got {pctl}")

        neighbours = info.get("neighbours", [])
        placeholder_found = any(n.get("playerID", "").startswith("_init") for n in neighbours)
        if not placeholder_found:
            ok("Edge DESC standing — no placeholder in neighbours", f"{len(neighbours)} neighbours")
        else:
            fail("Edge DESC standing — PLACEHOLDER LEAK", f"neighbours={neighbours}")

        # With only 1 real player, neighbours should contain just that player
        if len(neighbours) == 1 and neighbours[0].get("isTarget"):
            ok("Edge DESC standing — only target in neighbours", "correct")
        elif len(neighbours) == 0:
            ok("Edge DESC standing — empty neighbours (solo player)", "acceptable")
        else:
            n_pids = [n.get("playerID") for n in neighbours]
            fail("Edge DESC standing — unexpected neighbours", f"pids={n_pids}")
    else:
        fail("Edge DESC standing query", f"HTTP {r['status']}")

    # -------------------------------------------------------------------------
    # 10b. ASCENDING_LB (time) — this is the critical case where placeholder
    #       sits at position 0 in zrevrange (score 999999999.0 > all real negated scores)
    # -------------------------------------------------------------------------
    subsection("10b: ASCENDING_LB (time) — placeholder at position 0 edge case")
    lb_asc = f"tedge-asc-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "edge-asc",
        "leaderboardName": lb_asc,
        "statAttributeForLeaderboard": "lapTime",
        "leaderboardType": "ASCENDING_LB",
        "scoreStrategy": "best",
        "scoreType": "time", "sortOrder": "asc",
        "timeFormat": "seconds", "timePrecision": 3,
        "minValidTimeInSeconds": 1.0, "maxValidTimeInSeconds": 600.0
    }}, expected_status=201)
    if r["ok"]:
        ok("Create edge ASC LB", lb_asc)
        created_leaderboards.append(lb_asc)
    else:
        fail("Create edge ASC LB", f"HTTP {r['status']}")
        return

    # Query empty — should show 0 scores, totalPlayers=0
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_asc, "queryType": "top", "pageSize": 10
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        meta = resp.get("metadata", {})
        total = meta.get("totalPlayers", -1)

        if len(scores) == 0:
            ok("Edge ASC — empty LB returns 0 scores", "time placeholder filtered")
        else:
            pids = [s.get("playerID") for s in scores]
            fail("Edge ASC — empty LB returns 0 scores", f"got {len(scores)}: {pids}")

        if total == 0:
            ok("Edge ASC — totalPlayers=0", f"totalPlayers={total}")
        else:
            fail("Edge ASC — totalPlayers=0", f"got totalPlayers={total}")
    else:
        fail("Edge ASC — empty top query", f"HTTP {r['status']}")

    # Add 1 time score
    pid_asc = f"edge-asc-solo-{SESSION}"
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": pid_asc, "gameID": GAME, "gameMode": "edge-asc",
        "playerScore": 45.678, "leaderboardName": lb_asc,
        "fullRawGameReport": {"test": "edge-asc"}
    }})
    if r["ok"] and r["body"].get("gameReportResponse", {}).get("success"):
        ok("Edge ASC — store 1 time score", "45.678s stored")
    else:
        fail("Edge ASC — store 1 time score", f"HTTP {r['status']}")

    time.sleep(0.3)

    # Top query — rank=1, totalPlayers=1
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_asc, "queryType": "top", "pageSize": 10
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        meta = resp.get("metadata", {})

        if len(scores) == 1:
            ok("Edge ASC — 1 score returned", f"score={scores[0].get('score')}")
        else:
            pids = [s.get("playerID") for s in scores]
            fail("Edge ASC — 1 score returned", f"got {len(scores)}: {pids}")

        if len(scores) == 1 and scores[0].get("rank") == 1:
            ok("Edge ASC — rank=1 (no gap from placeholder)", f"rank={scores[0].get('rank')}")
        elif len(scores) == 1:
            fail("Edge ASC — rank=1", f"got rank={scores[0].get('rank')} (PLACEHOLDER RANK GAP!)")

        total = meta.get("totalPlayers", -1)
        if total == 1:
            ok("Edge ASC — totalPlayers=1", f"totalPlayers={total}")
        else:
            fail("Edge ASC — totalPlayers=1", f"got {total}")
    else:
        fail("Edge ASC — 1-score top query", f"HTTP {r['status']}")

    # Player standing with percentile + neighbours
    r = api_post("leaderboards/player/standing", {"playerLBStandingRequest": {
        "playerID": pid_asc, "leaderboardName": lb_asc,
        "includePercentile": True, "includeNeighbours": True, "neighboursCount": 3
    }})
    if r["ok"]:
        resp = r["body"].get("playerLBStandingResponse", {})
        info = resp.get("playerLBStandingInfo", {})

        if info.get("rank") == 1:
            ok("Edge ASC standing — rank=1", f"rank={info.get('rank')}")
        else:
            fail("Edge ASC standing — rank=1", f"got rank={info.get('rank')}")

        if info.get("totalPlayers") == 1:
            ok("Edge ASC standing — totalPlayers=1", f"totalPlayers={info.get('totalPlayers')}")
        else:
            fail("Edge ASC standing — totalPlayers", f"got {info.get('totalPlayers')}")

        pctl = info.get("percentile")
        if pctl is not None and abs(float(pctl) - 100.0) < 0.01:
            ok("Edge ASC standing — percentile=100%", f"percentile={pctl}")
        else:
            fail("Edge ASC standing — percentile=100%", f"got {pctl}")

        neighbours = info.get("neighbours", [])
        placeholder_found = any(n.get("playerID", "").startswith("_init") for n in neighbours)
        if not placeholder_found:
            ok("Edge ASC standing — no placeholder in neighbours", f"{len(neighbours)} neighbours")
        else:
            fail("Edge ASC standing — PLACEHOLDER LEAK IN NEIGHBOURS", f"neighbours={json.dumps(neighbours, default=str)}")
    else:
        fail("Edge ASC standing query", f"HTTP {r['status']}")

    # -------------------------------------------------------------------------
    # 10c. ASCENDING_LB (rank type) — same position-0 edge case with rank scoreType
    # -------------------------------------------------------------------------
    subsection("10c: ASCENDING_LB (rank type) — placeholder at position 0")
    lb_rank = f"tedge-rank-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "edge-rank",
        "leaderboardName": lb_rank,
        "statAttributeForLeaderboard": "eloRating",
        "leaderboardType": "ASCENDING_LB",
        "scoreStrategy": "replace",
        "scoreType": "rank", "sortOrder": "asc",
        "minValidScore": 1, "maxValidScore": 3000
    }}, expected_status=201)
    if r["ok"]:
        ok("Create edge RANK LB", lb_rank)
        created_leaderboards.append(lb_rank)
    else:
        fail("Create edge RANK LB", f"HTTP {r['status']}")
        return

    # Add 3 scores to test the around-player scenario with placeholder edge
    rank_players = [
        (f"edge-rank-1-{SESSION}", 1200),
        (f"edge-rank-2-{SESSION}", 1500),
        (f"edge-rank-3-{SESSION}", 900),
    ]
    for pid, score in rank_players:
        api_post("leaderboards/stats", {"gameReportBody": {
            "playerID": pid, "gameID": GAME, "gameMode": "edge-rank",
            "playerScore": score, "leaderboardName": lb_rank,
            "fullRawGameReport": {"elo": score}
        }})

    time.sleep(0.3)

    # Top query — ascending rank: 900, 1200, 1500
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_rank, "queryType": "top", "pageSize": 10
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        meta = resp.get("metadata", {})

        ranks = [s.get("rank") for s in scores]
        if ranks == [1, 2, 3]:
            ok("Edge RANK — contiguous ranks [1,2,3]", f"ranks={ranks}")
        else:
            fail("Edge RANK — contiguous ranks", f"expected [1,2,3], got {ranks}")

        total = meta.get("totalPlayers", -1)
        if total == 3:
            ok("Edge RANK — totalPlayers=3", f"totalPlayers={total}")
        else:
            fail("Edge RANK — totalPlayers=3", f"got {total}")

        actual_scores = [float(s.get("score", 0)) for s in scores]
        if actual_scores == [900.0, 1200.0, 1500.0]:
            ok("Edge RANK — ascending order correct", f"scores={actual_scores}")
        else:
            fail("Edge RANK — ascending order", f"expected [900,1200,1500], got {actual_scores}")

        # Verify no placeholder
        placeholder = any(s.get("playerID", "").startswith("_init") for s in scores)
        if not placeholder:
            ok("Edge RANK — no placeholder in top results", "clean")
        else:
            fail("Edge RANK — PLACEHOLDER LEAK", f"scores={scores}")
    else:
        fail("Edge RANK top query", f"HTTP {r['status']}")

    # aroundPlayer for middle player (rank 2, ELO 1200)
    mid_pid = f"edge-rank-1-{SESSION}"
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_rank, "queryType": "aroundPlayer",
        "playerID": mid_pid, "countBefore": 2, "countAfter": 2
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        ranks = [s.get("rank") for s in scores]

        if ranks == sorted(ranks) and len(set(ranks)) == len(ranks):
            ok("Edge RANK aroundPlayer — contiguous ranks", f"ranks={ranks}")
        else:
            fail("Edge RANK aroundPlayer — ranks", f"expected contiguous, got {ranks}")

        placeholder = any(s.get("playerID", "").startswith("_init") for s in scores)
        if not placeholder:
            ok("Edge RANK aroundPlayer — no placeholder", "clean")
        else:
            fail("Edge RANK aroundPlayer — PLACEHOLDER LEAK", f"pids={[s.get('playerID') for s in scores]}")
    else:
        fail("Edge RANK aroundPlayer query", f"HTTP {r['status']}")

    # Standing with neighbours
    r = api_post("leaderboards/player/standing", {"playerLBStandingRequest": {
        "playerID": mid_pid, "leaderboardName": lb_rank,
        "includePercentile": True, "includeNeighbours": True, "neighboursCount": 3
    }})
    if r["ok"]:
        resp = r["body"].get("playerLBStandingResponse", {})
        info = resp.get("playerLBStandingInfo", {})
        neighbours = info.get("neighbours", [])

        placeholder_found = any(n.get("playerID", "").startswith("_init") for n in neighbours)
        if not placeholder_found:
            ok("Edge RANK standing — no placeholder in neighbours", f"{len(neighbours)} neighbours")
        else:
            fail("Edge RANK standing — PLACEHOLDER IN NEIGHBOURS", f"{json.dumps(neighbours, default=str)[:300]}")

        n_ranks = [n.get("rank") for n in neighbours]
        if n_ranks == sorted(n_ranks) and len(set(n_ranks)) == len(n_ranks):
            ok("Edge RANK standing — contiguous neighbour ranks", f"ranks={n_ranks}")
        else:
            fail("Edge RANK standing — neighbour ranks", f"got {n_ranks}")

        if info.get("totalPlayers") == 3:
            ok("Edge RANK standing — totalPlayers=3", f"totalPlayers={info.get('totalPlayers')}")
        else:
            fail("Edge RANK standing — totalPlayers", f"got {info.get('totalPlayers')}")
    else:
        fail("Edge RANK standing query", f"HTTP {r['status']}")

    # -------------------------------------------------------------------------
    # 10d. ASCENDING_LB (distance) — placeholder value -1.0, which is less
    #       negative than real negated scores (-100, -200, etc.)
    #       Placeholder sits at position 0 in zrevrange for this type too.
    # -------------------------------------------------------------------------
    subsection("10d: ASCENDING_LB (distance) — negative placeholder edge case")
    lb_dist_asc = f"tedge-distasc-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "edge-distasc",
        "leaderboardName": lb_dist_asc,
        "statAttributeForLeaderboard": "golfScore",
        "leaderboardType": "ASCENDING_LB",
        "scoreStrategy": "best",
        "scoreType": "distance", "sortOrder": "asc",
        "minValidScore": 50, "maxValidScore": 500
    }}, expected_status=201)
    if r["ok"]:
        ok("Create edge DIST-ASC LB", lb_dist_asc)
        created_leaderboards.append(lb_dist_asc)
    else:
        fail("Create edge DIST-ASC LB", f"HTTP {r['status']}")
        return

    # Add 2 scores
    api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": f"edge-golf-1-{SESSION}", "gameID": GAME, "gameMode": "edge-distasc",
        "playerScore": 72, "leaderboardName": lb_dist_asc,
        "fullRawGameReport": {"holes": 18}
    }})
    api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": f"edge-golf-2-{SESSION}", "gameID": GAME, "gameMode": "edge-distasc",
        "playerScore": 68, "leaderboardName": lb_dist_asc,
        "fullRawGameReport": {"holes": 18}
    }})
    time.sleep(0.3)

    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_dist_asc, "queryType": "top", "pageSize": 10
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        ranks = [s.get("rank") for s in scores]
        actual_scores = [float(s.get("score", 0)) for s in scores]

        if ranks == [1, 2]:
            ok("Edge DIST-ASC — ranks [1,2]", f"ranks={ranks}")
        else:
            fail("Edge DIST-ASC — ranks", f"expected [1,2], got {ranks}")

        if actual_scores == [68.0, 72.0]:
            ok("Edge DIST-ASC — ascending order", f"scores={actual_scores}")
        else:
            fail("Edge DIST-ASC — order", f"expected [68,72], got {actual_scores}")

        placeholder = any(s.get("playerID", "").startswith("_init") for s in scores)
        if not placeholder:
            ok("Edge DIST-ASC — no placeholder", "clean")
        else:
            fail("Edge DIST-ASC — PLACEHOLDER LEAK", f"pids={[s.get('playerID') for s in scores]}")
    else:
        fail("Edge DIST-ASC top query", f"HTTP {r['status']}")


# =============================================================================
# PHASE 11: LARGE BATCH STORE (stress test with big payloads)
# =============================================================================
def generate_large_game_report(num_params: int = 1800) -> dict:
    """Generate a fullRawGameReport with num_params random fields."""
    report = {
        "sessionId": f"session-{uuid.uuid4().hex[:12]}",
        "clientVersion": "2.5.1",
        "platform": random.choice(["PC", "PS5", "Xbox", "Switch", "iOS", "Android"]),
        "region": random.choice(["us-east", "eu-west", "ap-northeast", "sa-east"]),
        "mapName": f"map-{random.randint(1, 50):03d}",
        "matchDuration": round(random.uniform(60, 3600), 2),
        "teamSize": random.randint(1, 6),
        "isRanked": random.choice([True, False]),
    }
    # Fill remaining with random stat fields
    stat_types = ["int", "float", "str", "bool"]
    for i in range(num_params - len(report)):
        key = f"stat_{i:04d}_{random.choice(['dmg','heal','xp','coin','dist','kill','death','assist','time','score'])}"
        st = random.choice(stat_types)
        if st == "int":
            report[key] = random.randint(0, 100000)
        elif st == "float":
            report[key] = round(random.uniform(0, 10000), 4)
        elif st == "str":
            report[key] = ''.join(random.choices(string.ascii_lowercase, k=random.randint(5, 20)))
        else:
            report[key] = random.choice([True, False])
    return report


def phase_large_batch_store():
    section("PHASE 11: LARGE BATCH STORE (stress test with big payloads)")

    GAME = GAME_ID
    lb_large = f"tlarge-batch-{SESSION}"

    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME, "gameMode": "large-batch",
        "leaderboardName": lb_large,
        "statAttributeForLeaderboard": "score",
        "leaderboardType": "DESCENDING_LB",
        "scoreStrategy": "best",
        "scoreType": "score",
        "minValidScore": 0, "maxValidScore": 999999999
    }}, expected_status=201)
    if r["ok"]:
        ok("Create large-batch LB", lb_large)
        created_leaderboards.append(lb_large)
    else:
        fail("Create large-batch LB", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")
        return

    # ---- Batch of 16 players with 1800-param reports ----
    subsection("11a: 16-player batch with 1800-param reports")
    reports_16 = []
    for i in range(16):
        reports_16.append({
            "playerID": f"bigbatch16-{SESSION}-{i+1:02d}",
            "gameID": GAME, "gameMode": "large-batch",
            "playerScore": random.randint(1000, 999999),
            "leaderboardName": lb_large,
            "fullRawGameReport": generate_large_game_report(1800)
        })

    payload_size = len(json.dumps({"batchGameReportBody": {"gameReports": reports_16}}))
    printlog(f"    Payload size: {payload_size:,} bytes ({payload_size/1024:.1f} KB)")

    r = api_post("leaderboards/stats/batch", {"batchGameReportBody": {"gameReports": reports_16}})
    if r["ok"]:
        summary = r["body"].get("batchGameReportResponse", {}).get("summary", {})
        perf = r["body"].get("batchGameReportResponse", {}).get("performance", {})
        if summary.get("processedItems") == 16 and summary.get("validationErrors", 0) == 0:
            ok("16-player large batch", f"16/16 processed, {perf.get('processingTimeMs', '?')}ms, {perf.get('itemsPerSecond', '?')} items/s")
        else:
            fail("16-player large batch", f"summary={json.dumps(summary, default=str)}")
    else:
        fail("16-player large batch", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:300]}")

    time.sleep(0.5)

    # ---- Batch of 32 players with 1800-param reports ----
    subsection("11b: 32-player batch with 1800-param reports")
    reports_32 = []
    for i in range(32):
        reports_32.append({
            "playerID": f"bigbatch32-{SESSION}-{i+1:02d}",
            "gameID": GAME, "gameMode": "large-batch",
            "playerScore": random.randint(1000, 999999),
            "leaderboardName": lb_large,
            "fullRawGameReport": generate_large_game_report(1800)
        })

    payload_size = len(json.dumps({"batchGameReportBody": {"gameReports": reports_32}}))
    printlog(f"    Payload size: {payload_size:,} bytes ({payload_size/1024:.1f} KB)")

    r = api_post("leaderboards/stats/batch", {"batchGameReportBody": {"gameReports": reports_32}})
    if r["ok"]:
        summary = r["body"].get("batchGameReportResponse", {}).get("summary", {})
        perf = r["body"].get("batchGameReportResponse", {}).get("performance", {})
        if summary.get("processedItems") == 32 and summary.get("validationErrors", 0) == 0:
            ok("32-player large batch", f"32/32 processed, {perf.get('processingTimeMs', '?')}ms, {perf.get('itemsPerSecond', '?')} items/s")
        else:
            fail("32-player large batch", f"summary={json.dumps(summary, default=str)}")
    else:
        fail("32-player large batch", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:300]}")

    time.sleep(0.5)

    # ---- Batch of 64 players with 1800-param reports ----
    subsection("11c: 64-player batch with 1800-param reports")
    reports_64 = []
    for i in range(64):
        reports_64.append({
            "playerID": f"bigbatch64-{SESSION}-{i+1:02d}",
            "gameID": GAME, "gameMode": "large-batch",
            "playerScore": random.randint(1000, 999999),
            "leaderboardName": lb_large,
            "fullRawGameReport": generate_large_game_report(1800)
        })

    payload_size = len(json.dumps({"batchGameReportBody": {"gameReports": reports_64}}))
    printlog(f"    Payload size: {payload_size:,} bytes ({payload_size/1024:.1f} KB)")

    r = api_post("leaderboards/stats/batch", {"batchGameReportBody": {"gameReports": reports_64}})
    if r["ok"]:
        summary = r["body"].get("batchGameReportResponse", {}).get("summary", {})
        perf = r["body"].get("batchGameReportResponse", {}).get("performance", {})
        if summary.get("processedItems") == 64 and summary.get("validationErrors", 0) == 0:
            ok("64-player large batch", f"64/64 processed, {perf.get('processingTimeMs', '?')}ms, {perf.get('itemsPerSecond', '?')} items/s")
        else:
            fail("64-player large batch", f"summary={json.dumps(summary, default=str)}")
    else:
        fail("64-player large batch", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:300]}")

    time.sleep(0.5)

    # ---- Validate leaderboard has all 112 unique players (16+32+64) ----
    subsection("11d: Validate all 112 players stored correctly")
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": lb_large, "queryType": "top", "pageSize": 500
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        meta = resp.get("metadata", {})
        total = meta.get("totalPlayers", 0)
        scores = resp.get("scores", [])

        if total == 112:
            ok("Large batch — 112 total players", f"totalPlayers={total}")
        else:
            fail("Large batch — 112 total players", f"expected 112, got {total}")

        if len(scores) == 112:
            ok("Large batch — 112 scores returned", f"scoresCount={len(scores)}")
        else:
            fail("Large batch — scores returned", f"expected 112, got {len(scores)}")

        # Verify ranks are contiguous 1..112
        ranks = [s.get("rank") for s in scores]
        expected_ranks = list(range(1, 113))
        if ranks == expected_ranks:
            ok("Large batch — ranks 1-112 contiguous", "verified")
        else:
            gaps = [r for r in expected_ranks if r not in ranks]
            fail("Large batch — ranks contiguous", f"missing ranks: {gaps[:10]}...")

        # No placeholder leak
        placeholder = any(s.get("playerID", "").startswith("_init") for s in scores)
        if not placeholder:
            ok("Large batch — no placeholder in results", "clean")
        else:
            fail("Large batch — PLACEHOLDER LEAK", "found in top results")
    else:
        fail("Large batch top query", f"HTTP {r['status']}")

    # ---- Verify a random player's stats in DynamoDB ----
    subsection("11e: Verify large-report player stats from DynamoDB")
    sample_pid = f"bigbatch64-{SESSION}-01"
    r = api_post("leaderboards/player/stats", {"playerStatsAndScoresRequest": {
        "playerID": sample_pid, "gameID": GAME, "gameMode": "large-batch", "limit": 1
    }})
    if r["ok"]:
        ps_resp = r["body"].get("playerStatsAndScoresResponse", {})
        results = ps_resp.get("results", {})
        stats = results.get("playerStats", [])
        if len(stats) >= 1:
            report = stats[0].get("fullRawGameReport", {})
            field_count = len(report)
            if field_count >= 1000:
                ok("Large report — stored in DynamoDB", f"{field_count} fields in fullRawGameReport")
            else:
                fail("Large report — field count", f"expected ~1800 fields, got {field_count}")
        else:
            fail("Large report — stats query", "0 records returned")
    else:
        fail("Large report stats query", f"HTTP {r['status']}")


# =============================================================================
# PHASE 12: DEVELOPER ENDPOINTS + AUTH VALIDATION + INPUT ERRORS
# =============================================================================
def phase_developer_and_validation():
    section("PHASE 12: DEVELOPER ENDPOINTS, AUTH, AND INPUT VALIDATION")

    # ---- 12a: Developer info (health check) ----
    subsection("12a: Developer info / health check")
    r = api_get(f"developer/info?studioId={STUDIO_ID}&gameId={GAME_ID}")
    if r["ok"]:
        resp = r["body"].get("devRegResponse", {})
        if resp.get("studioId") == STUDIO_ID and resp.get("gameId") == GAME_ID:
            ok("Developer info", f"studioId={STUDIO_ID}, gameId={GAME_ID}, status={resp.get('status')}")
        else:
            fail("Developer info", f"unexpected response: {json.dumps(resp, default=str)[:200]}")
    else:
        fail("Developer info", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")

    # ---- 12b: Invalid API key ----
    subsection("12b: Authentication with invalid API key")
    saved_headers = dict(HEADERS)
    bad_headers = {"Content-Type": "application/json", "Authorization": "Bearer invalid-key-12345"}
    url = f"{API_ENDPOINT}leaderboards/scores"
    try:
        resp = requests.post(url, headers=bad_headers, json={
            "leaderboardScoresRequest": {"leaderboardName": "nonexistent", "queryType": "top"}
        }, timeout=15)
        if resp.status_code == 403:
            ok("Invalid API key — rejected", f"HTTP 403")
        else:
            fail("Invalid API key — rejected", f"expected HTTP 403, got {resp.status_code}")
    except Exception as e:
        fail("Invalid API key test", str(e))

    # ---- 12c: Missing required fields ----
    subsection("12c: Input validation — missing required fields")
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": "test-missing",
        # Missing: gameID, gameMode, playerScore, leaderboardName, fullRawGameReport
    }})
    if r["status"] == 400:
        ok("Missing fields — rejected", f"HTTP 400")
    else:
        fail("Missing fields — rejected", f"expected HTTP 400, got {r['status']}")

    # ---- 12d: Invalid playerID characters ----
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": "invalid player!@#$",
        "gameID": GAME_ID, "gameMode": "test",
        "playerScore": 100, "leaderboardName": "nonexistent",
        "fullRawGameReport": {}
    }})
    if r["status"] == 400:
        ok("Invalid playerID chars — rejected", f"HTTP 400")
    else:
        fail("Invalid playerID chars — rejected", f"expected HTTP 400, got {r['status']}")

    # ---- 12e: Config create — duplicate leaderboard ----
    subsection("12e: Config create — duplicate name rejection")
    lb_dup = f"tdup-test-{SESSION}"
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME_ID, "gameMode": "duptest",
        "leaderboardName": lb_dup,
        "statAttributeForLeaderboard": "score",
        "leaderboardType": "DESCENDING_LB",
        "scoreStrategy": "best"
    }}, expected_status=201)
    if r["ok"]:
        created_leaderboards.append(lb_dup)
        # Try creating the same name again
        r2 = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
            "gameID": GAME_ID, "gameMode": "duptest",
            "leaderboardName": lb_dup,
            "statAttributeForLeaderboard": "score",
            "leaderboardType": "DESCENDING_LB",
            "scoreStrategy": "best"
        }})
        if r2["status"] in (400, 409):
            ok("Duplicate config name — rejected", f"HTTP {r2['status']} (already exists)")
        else:
            fail("Duplicate config name — rejected", f"expected HTTP 400/409, got {r2['status']}")
    else:
        fail("Duplicate test setup", f"HTTP {r['status']}")

    # ---- 12f: Config update ----
    subsection("12f: Config update")
    if lb_dup in created_leaderboards:
        r = api_post("leaderboards/config/update", {"gameLeaderboardConfigRequest": {
            "gameID": GAME_ID, "gameMode": "duptest",
            "leaderboardName": lb_dup,
            "statAttributeForLeaderboard": "score",
            "leaderboardType": "DESCENDING_LB",
            "scoreStrategy": "replace",
            "description": "Updated by test"
        }})
        # update uses PUT, let me use the right method
        url = f"{API_ENDPOINT}leaderboards/config/update"
        resp = requests.put(url, headers=HEADERS, json={"gameLeaderboardConfigRequest": {
            "gameID": GAME_ID, "gameMode": "duptest",
            "leaderboardName": lb_dup,
            "statAttributeForLeaderboard": "score",
            "leaderboardType": "DESCENDING_LB",
            "scoreStrategy": "replace",
            "description": "Updated by integration test"
        }}, timeout=30)
        _log_request_response("PUT", url, {"gameLeaderboardConfigRequest": {"leaderboardName": lb_dup, "scoreStrategy": "replace"}}, resp.status_code, resp.json() if resp.text else {}, 0)
        if resp.status_code == 200:
            cfg_resp = resp.json().get("gameLeaderboardConfigResponse", {})
            updated_cfg = cfg_resp.get("leaderboardConfig", {})
            if updated_cfg.get("scoreStrategy") == "replace":
                ok("Config update — strategy changed", "best → replace")
            else:
                fail("Config update — strategy", f"expected 'replace', got {updated_cfg.get('scoreStrategy')}")
        else:
            fail("Config update", f"HTTP {resp.status_code}")

    # ---- 12g: Config get-all ----
    subsection("12g: Config get-all")
    r = api_post("leaderboards/configs", {"gameLeaderboardConfigRequest": {
        "gameID": GAME_ID
    }})
    if r["ok"]:
        resp = r["body"].get("gameLeaderboardConfigResponse", {})
        configs = resp.get("leaderboardConfigs", [])
        count = resp.get("metadata", {}).get("count", 0)
        if count >= 1:
            ok("Config get-all", f"{count} configs returned for gameID={GAME_ID}")
        else:
            fail("Config get-all", f"expected >=1 configs, got {count}")
    else:
        fail("Config get-all", f"HTTP {r['status']}")

    # ---- 12h: Batch store — duplicate playerID rejection ----
    subsection("12h: Batch duplicate playerID rejection")
    dup_lb = next((lb for lb in created_leaderboards if lb.startswith("tscore-desc")), None)
    if dup_lb:
        r = api_post("leaderboards/stats/batch", {"batchGameReportBody": {"gameReports": [
            {"playerID": "dup-same-id", "gameID": GAME_ID, "gameMode": "campaign",
             "playerScore": 100, "leaderboardName": dup_lb, "fullRawGameReport": {}},
            {"playerID": "dup-same-id", "gameID": GAME_ID, "gameMode": "campaign",
             "playerScore": 200, "leaderboardName": dup_lb, "fullRawGameReport": {}},
        ]}})
        if r["status"] == 423:
            error_code = r["body"].get("batchGameReportResponse", {}).get("errorCode", "")
            if error_code == "DUPLICATE_PLAYERS_IN_BATCH":
                ok("Batch duplicate playerID — HTTP 423", f"errorCode={error_code}")
            else:
                fail("Batch duplicate playerID — errorCode", f"expected DUPLICATE_PLAYERS_IN_BATCH, got {error_code}")
        else:
            fail("Batch duplicate playerID", f"expected HTTP 423, got {r['status']}")
    else:
        printlog("  (skipped — no score leaderboard available)")

    # ---- 12i: Create expiring leaderboard + submit scores while active ----
    subsection("12i: Create expiring leaderboard (90s TTL) and submit scores while active")
    global expired_lb_name, expired_lb_created_at, expired_lb_players
    expired_lb_name = f"texpiry-ro-{SESSION}"
    expired_lb_players = []

    expiry_time = datetime.now(timezone.utc) + timedelta(seconds=90)
    expiry_iso = expiry_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    test_desc(f"Create leaderboard '{expired_lb_name}' with expiry at {expiry_iso} (90s from now), read-only on expiry")
    r = api_post("leaderboards/config/create", {"gameLeaderboardConfigRequest": {
        "gameID": GAME_ID, "gameMode": "expiry-test",
        "leaderboardName": expired_lb_name,
        "statAttributeForLeaderboard": "score",
        "leaderboardType": "DESCENDING_LB",
        "scoreStrategy": "best",
        "scoreType": "score",
        "minValidScore": 0, "maxValidScore": 999999,
        "optionalLBExpiryDateTimeStamp": expiry_iso,
        "optionalLBReadOnlyOnExpiry": True
    }}, expected_status=201)
    if r["ok"]:
        ok("Create expiring LB", f"'{expired_lb_name}' expires at {expiry_iso}")
        created_leaderboards.append(expired_lb_name)
        expired_lb_created_at = time.time()

        # Submit 5 scores while the leaderboard is still active
        test_desc(f"Submit 5 scores to '{expired_lb_name}' while still active (within 90s window)")
        for i in range(5):
            pid = f"expiry-player-{SESSION}-{i+1}"
            score = (i + 1) * 1000
            r2 = api_post("leaderboards/stats", {"gameReportBody": {
                "playerID": pid, "gameID": GAME_ID, "gameMode": "expiry-test",
                "playerScore": score, "leaderboardName": expired_lb_name,
                "fullRawGameReport": {"test": "expiry", "index": i}
            }})
            if r2["ok"] and r2["body"].get("gameReportResponse", {}).get("success"):
                expired_lb_players.append(pid)
                ok(f"Expiry LB submit while active #{i+1}", f"score={score}")
            else:
                fail(f"Expiry LB submit while active #{i+1}", f"HTTP {r2['status']}: {json.dumps(r2['body'], default=str)[:150]}")

        printlog(f"    Expiry LB: {len(expired_lb_players)} scores submitted. Will verify rejection after other phases complete (~4 min).")
    else:
        fail("Create expiring LB", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")
        expired_lb_created_at = 0


# =============================================================================
# PHASE 12b: VERIFY EXPIRED LEADERBOARD BEHAVIOR
# =============================================================================
expired_lb_name = ""
expired_lb_created_at = 0.0
expired_lb_players: list = []


def phase_verify_expired_leaderboard():
    section("PHASE 12b: VERIFY EXPIRED LEADERBOARD BEHAVIOR")

    if not expired_lb_name or expired_lb_created_at == 0:
        printlog("  (skipped — expiring LB was not created)")
        return

    elapsed = time.time() - expired_lb_created_at
    printlog(f"  Time since expiry LB creation: {elapsed:.0f}s (expiry was 90s)")

    if elapsed < 95:
        wait_needed = 95 - elapsed
        printlog(f"  Waiting {wait_needed:.0f}s for leaderboard to expire...")
        time.sleep(wait_needed)
        elapsed = time.time() - expired_lb_created_at
        printlog(f"  Resumed after wait. Elapsed: {elapsed:.0f}s")

    # ---- Verify scores submitted earlier are still readable ----
    subsection("12b-1: Verify scores are still readable on expired read-only LB")
    test_desc(f"Query expired LB '{expired_lb_name}' — expect 5 scores still readable (read-only mode)")
    r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
        "leaderboardName": expired_lb_name, "queryType": "top", "pageSize": 10
    }})
    if r["ok"]:
        resp = r["body"].get("leaderboardScoresResponse", {})
        scores = resp.get("scores", [])
        total = resp.get("metadata", {}).get("totalPlayers", 0)
        if total == 5:
            ok("Expired LB — reads still work", f"totalPlayers={total}, {len(scores)} scores returned")
        else:
            fail("Expired LB — reads still work", f"expected totalPlayers=5, got={total}")
    else:
        fail("Expired LB — read query", f"HTTP {r['status']}")

    # ---- Verify new score submissions are rejected with 423 ----
    subsection("12b-2: Verify new submissions rejected on expired read-only LB")
    test_desc(f"Submit new score to expired LB '{expired_lb_name}' — expect HTTP 423 LEADERBOARD_EXPIRED_READONLY")
    r = api_post("leaderboards/stats", {"gameReportBody": {
        "playerID": f"expiry-rejected-{SESSION}", "gameID": GAME_ID, "gameMode": "expiry-test",
        "playerScore": 99999, "leaderboardName": expired_lb_name,
        "fullRawGameReport": {"test": "should-be-rejected"}
    }})
    if r["status"] == 423:
        error_code = r["body"].get("gameReportResponse", {}).get("errorCode", "")
        if error_code == "LEADERBOARD_EXPIRED_READONLY":
            ok("Expired LB — submission rejected", f"HTTP 423, errorCode={error_code}")
        else:
            fail("Expired LB — submission errorCode", f"expected LEADERBOARD_EXPIRED_READONLY, got={error_code}")
    else:
        fail("Expired LB — submission rejected", f"expected HTTP 423, got {r['status']}: {json.dumps(r['body'], default=str)[:200]}")

    # ---- Verify batch submission also rejected ----
    subsection("12b-3: Verify batch submission rejected on expired read-only LB")
    test_desc(f"Batch submit to expired LB '{expired_lb_name}' — expect HTTP 423")
    r = api_post("leaderboards/stats/batch", {"batchGameReportBody": {"gameReports": [
        {"playerID": f"expiry-batch-{SESSION}", "gameID": GAME_ID, "gameMode": "expiry-test",
         "playerScore": 88888, "leaderboardName": expired_lb_name, "fullRawGameReport": {}}
    ]}})
    if r["status"] == 423:
        ok("Expired LB — batch rejected", f"HTTP 423")
    else:
        fail("Expired LB — batch rejected", f"expected HTTP 423, got {r['status']}")

    # ---- Verify rebuild rejected on expired LB ----
    subsection("12b-4: Verify rebuild rejected on expired read-only LB")
    test_desc(f"Rebuild expired LB '{expired_lb_name}' — expect HTTP 423 LEADERBOARD_EXPIRED_READONLY")
    r = api_post("leaderboards/admin/rebuild", {"rebuildLeaderboardRequest": {
        "leaderboardName": expired_lb_name, "confirmRebuild": True
    }})
    if r["status"] == 423:
        error_code = r["body"].get("rebuildLeaderboardResponse", {}).get("errorCode", "")
        if error_code == "LEADERBOARD_EXPIRED_READONLY":
            ok("Expired LB — rebuild rejected", f"HTTP 423, errorCode={error_code}")
        else:
            fail("Expired LB — rebuild errorCode", f"expected LEADERBOARD_EXPIRED_READONLY, got={error_code}")
    else:
        fail("Expired LB — rebuild rejected", f"expected HTTP 423, got {r['status']}: {json.dumps(r['body'], default=str)[:200]}")

    # ---- Verify player standing still works (read operation) ----
    subsection("12b-5: Verify player standing still works on expired read-only LB")
    if expired_lb_players:
        test_desc(f"Query standing for '{expired_lb_players[0]}' on expired LB — reads should still work")
        r = api_post("leaderboards/player/standing", {"playerLBStandingRequest": {
            "playerID": expired_lb_players[0], "leaderboardName": expired_lb_name,
            "includePercentile": True, "includeNeighbours": True, "neighboursCount": 3
        }})
        if r["ok"]:
            info = r["body"].get("playerLBStandingResponse", {}).get("playerLBStandingInfo", {})
            if info.get("rank") is not None:
                ok("Expired LB — standing reads work", f"rank={info.get('rank')}, percentile={info.get('percentile')}")
            else:
                fail("Expired LB — standing reads", "rank is None")
        else:
            fail("Expired LB — standing query", f"HTTP {r['status']}")


# =============================================================================
# PHASE 13: CAPTURE FULL LEADERBOARD STATE (pre-rebuild snapshot)
# =============================================================================
leaderboard_snapshots: Dict[str, dict] = {}  # lb_name → {scores, totalPlayers, ...}


def phase_capture_leaderboard_state():
    section("PHASE 13: CAPTURE FULL LEADERBOARD STATE (pre-rebuild snapshot)")

    for lb_name in created_leaderboards:
        test_desc(f"Capture full state of '{lb_name}' — top query (pageSize=500) to record all scores, players, ranks")
        r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
            "leaderboardName": lb_name, "queryType": "top", "pageSize": 500
        }})
        if r["ok"]:
            resp = r["body"].get("leaderboardScoresResponse", {})
            scores = resp.get("scores", [])
            meta = resp.get("metadata", {})
            total = meta.get("totalPlayers", 0)

            leaderboard_snapshots[lb_name] = {
                "scores": scores,
                "totalPlayers": total,
                "scoreValues": [s.get("score") for s in scores],
                "playerIDs": [s.get("playerID") for s in scores],
                "ranks": [s.get("rank") for s in scores],
            }
            ok(f"Snapshot {lb_name}", f"{total} players, {len(scores)} scores captured")
        else:
            # Some leaderboards may have been reset in edge-case tests; skip gracefully
            leaderboard_snapshots[lb_name] = {"scores": [], "totalPlayers": 0, "scoreValues": [], "playerIDs": [], "ranks": []}
            printlog(f"  (snapshot {lb_name}: HTTP {r['status']} — empty or unavailable)")


# =============================================================================
# PHASE 14: REBUILD EVERY LEADERBOARD AND VALIDATE
# =============================================================================
def phase_rebuild_and_validate():
    section("PHASE 14: REBUILD EVERY LEADERBOARD AND VALIDATE")

    for lb_name in created_leaderboards:
        snapshot = leaderboard_snapshots.get(lb_name, {})
        pre_total = snapshot.get("totalPlayers", 0)

        # Skip leaderboards with 0 scores (edge-case LBs that were already empty)
        if pre_total == 0:
            printlog(f"  (skipping rebuild for {lb_name} — 0 players in snapshot)")
            continue

        # Skip expired leaderboards (rebuild rejection already tested in Phase 12b)
        if expired_lb_name and lb_name == expired_lb_name:
            printlog(f"  (skipping rebuild for {lb_name} — expired LB, tested separately in Phase 12b)")
            continue

        subsection(f"Rebuild: {lb_name} ({pre_total} players)")

        test_desc(f"Rebuild '{lb_name}' from DynamoDB stats — expect {pre_total} players restored with identical scores")
        r = api_post("leaderboards/admin/rebuild", {"rebuildLeaderboardRequest": {
            "leaderboardName": lb_name,
            "confirmRebuild": True
        }})

        if r["status"] == 200:
            resp = r["body"].get("rebuildLeaderboardResponse", {})
            results = resp.get("rebuildResults", {})
            if results.get("success"):
                ok(f"Rebuild {lb_name}", f"processedItems={results.get('processedItems')}, finalSize={results.get('finalLeaderboardSize')}")
            else:
                fail(f"Rebuild {lb_name}", f"success=false: {json.dumps(results, default=str)[:200]}")
                continue
        elif r["status"] == 202:
            # Long-running — accepted but not complete. For test LBs this shouldn't happen.
            printlog(f"  Rebuild {lb_name}: HTTP 202 (in progress) — waiting 10s for completion")
            time.sleep(10)
            ok(f"Rebuild {lb_name} (async)", f"HTTP 202 accepted")
        else:
            fail(f"Rebuild {lb_name}", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")
            continue

        time.sleep(0.5)

        # Query rebuilt leaderboard and compare with snapshot
        test_desc(f"Query rebuilt '{lb_name}' — compare totalPlayers, scores, players, ranks with pre-rebuild snapshot")
        r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
            "leaderboardName": lb_name, "queryType": "top", "pageSize": 500
        }})
        if not r["ok"]:
            fail(f"Post-rebuild query {lb_name}", f"HTTP {r['status']}")
            continue

        resp = r["body"].get("leaderboardScoresResponse", {})
        post_scores = resp.get("scores", [])
        post_meta = resp.get("metadata", {})
        post_total = post_meta.get("totalPlayers", 0)

        # Validate total player count matches
        if post_total == pre_total:
            ok(f"Rebuild {lb_name} — totalPlayers match", f"pre={pre_total}, post={post_total}")
        else:
            fail(f"Rebuild {lb_name} — totalPlayers match", f"pre={pre_total}, post={post_total}")

        # Validate score count matches
        pre_count = len(snapshot.get("scores", []))
        post_count = len(post_scores)
        if post_count == pre_count:
            ok(f"Rebuild {lb_name} — score count match", f"pre={pre_count}, post={post_count}")
        else:
            fail(f"Rebuild {lb_name} — score count match", f"pre={pre_count}, post={post_count}")

        # Validate same players present (order may differ for equal scores)
        pre_pids = set(snapshot.get("playerIDs", []))
        post_pids = set(s.get("playerID") for s in post_scores)
        if pre_pids == post_pids:
            ok(f"Rebuild {lb_name} — same players", f"{len(pre_pids)} players match")
        else:
            missing = pre_pids - post_pids
            extra = post_pids - pre_pids
            fail(f"Rebuild {lb_name} — same players", f"missing={missing}, extra={extra}")

        # Validate ranks are contiguous starting at 1
        post_ranks = [s.get("rank") for s in post_scores]
        expected_ranks = list(range(1, len(post_scores) + 1))
        if post_ranks == expected_ranks:
            ok(f"Rebuild {lb_name} — contiguous ranks", f"ranks 1-{len(post_scores)}")
        else:
            fail(f"Rebuild {lb_name} — contiguous ranks", f"expected {expected_ranks[:5]}..., got {post_ranks[:5]}...")

        # Validate no placeholder leaked
        placeholder_found = any(s.get("playerID", "").startswith("_init") for s in post_scores)
        if not placeholder_found:
            ok(f"Rebuild {lb_name} — no placeholder", "clean")
        else:
            fail(f"Rebuild {lb_name} — PLACEHOLDER LEAK", "found in rebuilt results")

        # Validate per-player scores match (build lookup by playerID)
        pre_player_scores = {s.get("playerID"): s.get("score") for s in snapshot.get("scores", [])}
        post_player_scores = {s.get("playerID"): s.get("score") for s in post_scores}
        mismatched_scores = []
        for pid in pre_pids & post_pids:
            pre_s = pre_player_scores.get(pid)
            post_s = post_player_scores.get(pid)
            try:
                # Numeric comparison (handles float rounding)
                if abs(float(pre_s) - float(post_s)) > 0.01:
                    mismatched_scores.append(f"{pid}: pre={pre_s}, post={post_s}")
            except (TypeError, ValueError):
                # String comparison for time-formatted scores
                if str(pre_s) != str(post_s):
                    mismatched_scores.append(f"{pid}: pre={pre_s}, post={post_s}")

        if not mismatched_scores:
            ok(f"Rebuild {lb_name} — scores match", f"all {len(pre_pids & post_pids)} player scores verified")
        else:
            fail(f"Rebuild {lb_name} — scores match", f"{len(mismatched_scores)} mismatches: {mismatched_scores[:3]}")


# =============================================================================
# PHASE 15: RESET EVERY LEADERBOARD AND VALIDATE
# =============================================================================
def phase_reset_and_validate(retain: bool = False):
    section("PHASE 15: RESET LEADERBOARDS AND VALIDATE")

    if retain:
        # When --retain is used, only reset 2 sample leaderboards (one DESCENDING, one ASCENDING)
        # to validate reset works, while leaving the rest with data for manual inspection.
        desc_sample = next((lb for lb in created_leaderboards if "tscore-desc" in lb), None)
        asc_sample = next((lb for lb in created_leaderboards if "ttime-sec" in lb or "trank-asc" in lb), None)
        reset_targets = [lb for lb in [desc_sample, asc_sample] if lb]
        printlog(f"  --retain mode: resetting only {len(reset_targets)} sample leaderboard(s), preserving the rest with data")
    else:
        reset_targets = list(created_leaderboards)

    for lb_name in reset_targets:
        subsection(f"Reset: {lb_name}")

        test_desc(f"Reset '{lb_name}' with backup — expect all scores cleared, config preserved")
        r = api_post("leaderboards/admin/reset", {"resetLeaderboardRequest": {
            "leaderboardName": lb_name,
            "confirmReset": True,
            "createBackup": True
        }})

        if r["status"] == 200:
            resp = r["body"].get("resetLeaderboardResponse", {})
            results = resp.get("resetResults", {})
            if results.get("resetCompleted"):
                backup = results.get("backupData") or results.get("backupCreated")
                reset_info = results.get("resetInfo", {})
                ok(f"Reset {lb_name}", f"entriesRemoved={reset_info.get('entriesRemoved', '?')}, backup={'yes' if backup else 'no'}")
            else:
                fail(f"Reset {lb_name}", f"resetCompleted=false: {json.dumps(results, default=str)[:200]}")
                continue
        elif r["status"] == 202:
            printlog(f"  Reset {lb_name}: HTTP 202 (in progress) — waiting 5s")
            time.sleep(5)
            ok(f"Reset {lb_name} (async)", f"HTTP 202 accepted")
        else:
            fail(f"Reset {lb_name}", f"HTTP {r['status']}: {json.dumps(r['body'], default=str)[:200]}")
            continue

        time.sleep(0.3)

        # Verify leaderboard is now empty
        test_desc(f"Verify '{lb_name}' is empty after reset — expect 0 scores, totalPlayers=0, config still exists")
        r = api_post("leaderboards/scores", {"leaderboardScoresRequest": {
            "leaderboardName": lb_name, "queryType": "top", "pageSize": 10
        }})
        if r["ok"]:
            resp = r["body"].get("leaderboardScoresResponse", {})
            post_scores = resp.get("scores", [])
            post_total = resp.get("metadata", {}).get("totalPlayers", -1)

            if len(post_scores) == 0:
                ok(f"Post-reset {lb_name} — 0 scores", "empty as expected")
            else:
                pids = [s.get("playerID") for s in post_scores]
                fail(f"Post-reset {lb_name} — 0 scores", f"got {len(post_scores)}: {pids}")

            if post_total == 0:
                ok(f"Post-reset {lb_name} — totalPlayers=0", "placeholder excluded")
            else:
                fail(f"Post-reset {lb_name} — totalPlayers=0", f"got {post_total}")
        else:
            fail(f"Post-reset query {lb_name}", f"HTTP {r['status']}")

        # Verify config still exists (reset preserves config)
        r = api_post("leaderboards/config/get", {"gameLeaderboardConfigRequest": {
            "leaderboardName": lb_name
        }})
        if r["ok"]:
            ok(f"Post-reset {lb_name} — config preserved", "config still exists")
        else:
            fail(f"Post-reset {lb_name} — config preserved", f"HTTP {r['status']} (config lost!)")


# =============================================================================
# PHASE 16: CLEANUP
# =============================================================================
def phase_cleanup():
    section("PHASE 16: CLEANUP")

    for lb_name in created_leaderboards:
        r = api_delete("leaderboards/config/delete", {"gameLeaderboardConfigRequest": {"leaderboardName": lb_name}})
        if r["status"] in (200, 404):
            printlog(f"  Deleted: {lb_name}")
        else:
            printlog(f"  Failed to delete {lb_name}: HTTP {r['status']}")

    printlog(f"  Cleanup complete: {len(created_leaderboards)} leaderboards processed")


# =============================================================================
# MAIN
# =============================================================================
def main():
    global STACK_NAME, AWS_PROFILE, AWS_REGION

    # Parse CLI arguments
    parser = argparse.ArgumentParser(
        description="Comprehensive Integration Test Suite for Game Stats & Leaderboards",
        epilog="Examples:\n"
               "  python3 test_StatsAndLeaderboards.py\n"
               "  python3 test_StatsAndLeaderboards.py --profile myprofile\n"
               "  python3 test_StatsAndLeaderboards.py --profile prod-account --region eu-west-1 --stack-name MyStack\n",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", default=None,
                        help="AWS profile name (default: uses AWS_PROFILE env var or default credential chain)")
    parser.add_argument("--region", default="us-west-2",
                        help="AWS region (default: us-west-2)")
    parser.add_argument("--stack-name", default="GameStatsLeaderboardsStack",
                        help="CloudFormation stack name (default: GameStatsLeaderboardsStack)")
    parser.add_argument("--retain", action="store_true",
                        help="Skip cleanup — retain all created leaderboards for manual inspection")
    args = parser.parse_args()

    STACK_NAME = args.stack_name
    AWS_PROFILE = args.profile  # None means use default credential chain
    AWS_REGION = args.region
    retain_leaderboards = args.retain

    start_time = datetime.now(timezone.utc)

    section("CONFIGURATION DISCOVERY")
    discover_configuration()

    banner = f"""
{'#'*80}
#  COMPREHENSIVE INTEGRATION TEST
#  Session: {SESSION}
#  Endpoint: {API_ENDPOINT}
#  Studio: {STUDIO_ID} / Game: {GAME_ID}
#  Started: {start_time.isoformat()}
#  Log file: {LOG_FILE}
{'#'*80}"""
    printlog(banner)

    lb_defs = build_leaderboard_defs()

    try:
        phase_developer_and_validation()        # 12: Dev info, auth, input validation, config CRUD
        phase_create_leaderboards(lb_defs)      #  1: Create 9 leaderboards (all score types)
        phase_validate_configs(lb_defs)          #  2: GET each config and validate
        phase_submit_single_scores(lb_defs)      #  3: Single-store scores for all types
        phase_submit_cumulative_scores(lb_defs)  # 3b: Cumulative strategy test
        phase_batch_populate(lb_defs)            # 3c: Batch-populate to 100+ entries per leaderboard
        phase_validate_leaderboard_order(lb_defs)#  4: Top query ordering validation
        phase_validate_player_standing(lb_defs)  #  5: Standing + percentile + neighbours
        phase_batch_store_and_validate(lb_defs)  #  6: Small batch store + verify
        phase_validate_player_stats(lb_defs)     #  7: DynamoDB stats query
        phase_validate_range_and_around(lb_defs) #  8: Range + aroundPlayer queries
        phase_deep_time_tests()                  #  9: All time formats, strategies, precision, boundaries
        phase_placeholder_edge_cases()           # 10: Placeholder edge cases for all LB types
        phase_large_batch_store()                # 11: Large batches (16/32/64 players, 1800-param reports)
        phase_verify_expired_leaderboard()       # 12b: Verify expired LB rejects writes + rebuild
        phase_capture_leaderboard_state()        # 13: Snapshot all leaderboards before rebuild
        phase_rebuild_and_validate()             # 14: Rebuild every LB and compare with snapshot
        phase_reset_and_validate(retain_leaderboards)  # 15: Reset LBs (sample only if --retain)
    except KeyboardInterrupt:
        printlog("\n\n  [INTERRUPTED]")
    except Exception as e:
        printlog(f"\n  [UNEXPECTED ERROR] {type(e).__name__}: {e}")
    finally:
        if retain_leaderboards:
            section("CLEANUP SKIPPED (--retain flag)")
            printlog(f"  {len(created_leaderboards)} leaderboards retained for inspection:")
            for lb in created_leaderboards:
                printlog(f"    - {lb}")
        else:
            phase_cleanup()

    # Final summary
    end_time = datetime.now(timezone.utc)
    elapsed_total = (end_time - start_time).total_seconds()

    section("FINAL RESULTS")
    for line in [
        f"  Total tests:    {total_tests}",
        f"  Passed:         {passed_tests}",
        f"  Failed:         {failed_tests}",
        f"  API requests:   {request_counter}",
        f"  Duration:       {elapsed_total:.1f}s",
        f"  Log file:       {LOG_FILE}",
    ]:
        printlog(line)

    if failures:
        printlog(f"\n  FAILURES:")
        for name, detail in failures:
            printlog(f"    - {name}: {detail}")

    verdict = 'ALL TESTS PASSED' if failed_tests == 0 else f'{failed_tests} TEST(S) FAILED'
    printlog(f"\n  {verdict}")
    sys.exit(0 if failed_tests == 0 else 1)


if __name__ == "__main__":
    main()
