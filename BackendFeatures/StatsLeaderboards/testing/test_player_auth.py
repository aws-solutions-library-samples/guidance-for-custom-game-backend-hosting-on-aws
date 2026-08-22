#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Player Authorizer Test - Game Stats and Leaderboards
====================================================
Verifies the player authorizer is deployed, wired to the player routes, and fails
closed. Follows the live-integration style of test_StatsAndLeaderboards.py: it
discovers the API endpoint from CloudFormation and makes real HTTP calls.

Always-on checks (no Custom Identity Component required):
  - missing Authorization header     -> 401/403 rejected
  - malformed bearer token           -> 403 rejected
  - StudioAPI key on a player route   -> 403 rejected (the player lane must not
                                         accept the backend key)

Opt-in check (requires a real player token):
  - set PLAYER_TEST_TOKEN to a Custom Identity Component access token. The test
    sends it to a player route and expects the authorizer to allow it through
    (any response other than 401/403). Skipped when the variable is not set.

Usage:
  python3 test_player_auth.py --region us-west-2 --stack-name GameStatsLeaderboardsStack
  PLAYER_TEST_TOKEN=<token> python3 test_player_auth.py --region us-west-2
"""

import os
import sys
import json
import argparse
import requests
import boto3

# A player-authorized, read-only route. The authorizer runs before the handler,
# so the request body only matters once the authorizer has allowed the request.
PLAYER_ROUTE = "leaderboards/scores"
PROBE_BODY = {
    "leaderboardScoresRequest": {
        "leaderboardName": "player-auth-probe",
        "queryType": "top",
        "pageSize": 1,
    }
}

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    suffix = f"  ({detail})" if detail else ""
    if condition:
        passed += 1
        print(f"  PASS  {name}{suffix}")
    else:
        failed += 1
        print(f"  FAIL  {name}{suffix}")


def discover(stack_name: str, region: str, profile: str):
    """Return (api_endpoint, studio_api_key) from CloudFormation and SSM.

    The studio API key is best-effort: it is only used for the negative test that
    confirms the player lane rejects the backend key, so a missing key skips that
    one check rather than failing the run.
    """
    session = boto3.Session(profile_name=profile or None, region_name=region)

    stacks = session.client("cloudformation").describe_stacks(StackName=stack_name)["Stacks"]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}
    api_endpoint = outputs.get("ApiEndpoint", "")
    if not api_endpoint:
        print(f"  ERROR: 'ApiEndpoint' not found in stack '{stack_name}' outputs.")
        sys.exit(1)
    if not api_endpoint.endswith("/"):
        api_endpoint += "/"

    ssm_prefix = outputs.get("SSMParameterPrefix", "/game-statsleaderboards-dev")
    studio_api_key = ""
    try:
        ssm = session.client("ssm")
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=f"{ssm_prefix.rstrip('/')}/api-keys",
                                       Recursive=True, WithDecryption=True):
            for param in page.get("Parameters", []):
                try:
                    data = json.loads(param["Value"])
                    if data.get("status") == "active":
                        studio_api_key = data["apiKey"]
                        break
                except (json.JSONDecodeError, KeyError):
                    continue
            if studio_api_key:
                break
    except Exception:
        pass  # best-effort; the backend-key check is skipped if we can't read it

    return api_endpoint, studio_api_key


def post(api_endpoint: str, headers: dict) -> int:
    """POST the probe body to the player route and return the HTTP status code."""
    resp = requests.post(api_endpoint + PLAYER_ROUTE, headers=headers,
                         data=json.dumps(PROBE_BODY), timeout=15)
    return resp.status_code


def main():
    parser = argparse.ArgumentParser(description="Player authorizer test")
    parser.add_argument("--stack-name", default="GameStatsLeaderboardsStack")
    parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE", ""))
    args = parser.parse_args()

    print("Player authorizer test")
    print(f"  Stack:  {args.stack_name}")
    print(f"  Region: {args.region}")

    api_endpoint, studio_api_key = discover(args.stack_name, args.region, args.profile)
    print(f"  API:    {api_endpoint}\n")

    json_headers = {"Content-Type": "application/json"}

    # 1. Missing Authorization header -> rejected (401 or 403).
    status = post(api_endpoint, json_headers)
    check("missing Authorization header is rejected", status in (401, 403),
          f"HTTP {status}")

    # 2. Malformed bearer token -> denied by the authorizer (403).
    status = post(api_endpoint, {**json_headers, "Authorization": "Bearer not-a-real-token"})
    check("malformed token is denied", status == 403, f"HTTP {status}")

    # 3. StudioAPI key on a player route -> denied (the player lane must not accept it).
    if studio_api_key:
        status = post(api_endpoint, {**json_headers, "Authorization": f"Bearer {studio_api_key}"})
        check("StudioAPI key is rejected on player routes", status == 403, f"HTTP {status}")
    else:
        print("  SKIP  StudioAPI key check (no active key found in SSM)")

    # 4. Opt-in: a real player token should be allowed through (not 401/403).
    player_token = os.environ.get("PLAYER_TEST_TOKEN", "").strip()
    if player_token:
        status = post(api_endpoint, {**json_headers, "Authorization": f"Bearer {player_token}"})
        check("valid player token is allowed through", status not in (401, 403),
              f"HTTP {status}")
    else:
        print("  SKIP  valid-token check (set PLAYER_TEST_TOKEN to run it)")

    print(f"\nResult: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
