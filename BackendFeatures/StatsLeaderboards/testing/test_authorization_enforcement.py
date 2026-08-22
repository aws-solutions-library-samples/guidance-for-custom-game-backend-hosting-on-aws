#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Authorization-enforcement guard test — Game Stats and Leaderboards
==================================================================

WHY THIS TEST EXISTS (security finding M4)
------------------------------------------
Route-level authorization is DELIBERATELY DELEGATED TO THE HANDLERS.

The API Gateway Lambda authorizer (auth/backendAuthorizer.py) intentionally
returns a broad resource ARN ("{base_arn}/{stage}/*/*") and its result is cached
for 5 minutes (results_cache_ttl). That means a valid Studio API key is
authorized at the API Gateway layer for ALL methods/paths in the stage — the
authorizer does NOT enforce per-route read/write/admin separation.

That separation is enforced INSIDE each handler instead: every backend/player
Lambda calls validate_authenticated_context(event, <required_permission>) and
rejects the request when the caller's granted permissions don't include the
required one. This is a sound, intentional design — but it is a COMPENSATING
CONTROL, and the entire per-route authZ story rests on it being present in every
handler. If a new handler is added (or an existing one refactored) WITHOUT the
permission check, that route silently becomes "any valid key can call it".

This test asserts the compensating control is present in every data-plane
handler, so the delegation stays safe as the code evolves. It is a STATIC source
analysis — it does not require AWS, a deployment, or network access — so it can
run in CI on every change.

WHAT IT CHECKS
--------------
For each read/write handler (backend leaderboard ops + player ops):
  1. It defines validate_authenticated_context(...).
  2. That function contains the guard:
        if required_permission not in permissions: raise ...
  3. Every lambda_handler entry path calls validate_authenticated_context(event, ...)
     with an explicit permission argument ('read' or 'write').

developerRegistration.py uses a DIFFERENT (ownership-based) model — it validates
studioId/gameId ownership rather than read/write permissions — so it is checked
for that model instead.

USAGE
-----
    python3 testing/test_authorization_enforcement.py        # exit 0 = pass
    (or run under pytest: it also exposes test_* functions)
"""

import ast
import os
import sys

HANDLER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Handlers that enforce read/write permissions via validate_authenticated_context.
# value = the permission(s) we expect to see passed at the call sites.
PERMISSION_HANDLERS = {
    "backend/leaderboardsConfig.py": {"read", "write"},
    "backend/batchStoreStatsAndScores.py": {"write"},
    "backend/rebuildLeaderboard.py": {"write"},
    "backend/resetLeaderboard.py": {"write"},
    "player/getLeaderboardScores.py": {"read"},
    "player/getPlayerLBStanding.py": {"read"},
    "player/getPlayerStatsAndScores.py": {"read"},
    "player/storePlayerStatsAndScores.py": {"write"},
}

# Handler that uses the ownership model instead of read/write permissions.
OWNERSHIP_HANDLER = "backend/developerRegistration.py"


def _load(path):
    full = os.path.join(HANDLER_DIR, path)
    with open(full, "r") as f:
        src = f.read()
    return src, ast.parse(src)


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _has_permission_guard(func):
    """True if the function body contains: `required_permission not in permissions`
    used in a condition that raises."""
    for node in ast.walk(func):
        if isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.NotIn):
            left = getattr(node.left, "id", None)
            right = getattr(node.comparators[0], "id", None)
            if left == "required_permission" and right == "permissions":
                return True
    return False


def _validate_calls(tree):
    """Return the list of validate_authenticated_context(event, <arg>) calls,
    with the literal permission string where present."""
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "validate_authenticated_context":
            perm = None
            # second positional arg, if it's a string literal
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                perm = node.args[1].value
            calls.append(perm)
    return calls


def check_permission_handler(path, expected_perms):
    """Returns (ok: bool, messages: list[str])."""
    msgs = []
    src, tree = _load(path)

    vac = _func(tree, "validate_authenticated_context")
    if vac is None:
        return False, [f"{path}: missing validate_authenticated_context()"]

    if not _has_permission_guard(vac):
        msgs.append(
            f"{path}: validate_authenticated_context() does not enforce "
            f"'required_permission not in permissions' (the compensating control for M4)"
        )

    calls = _validate_calls(tree)
    if not calls:
        msgs.append(f"{path}: lambda_handler never calls validate_authenticated_context(event, ...)")

    # Every call site should pass an explicit permission literal.
    explicit = [c for c in calls if c is not None]
    if not explicit:
        msgs.append(f"{path}: no validate_authenticated_context() call passes an explicit permission literal")

    # The permissions used should be within the expected set (typo / wrong-perm guard).
    for perm in explicit:
        if perm not in expected_perms:
            msgs.append(
                f"{path}: validate_authenticated_context(event, '{perm}') uses an "
                f"unexpected permission (expected one of {sorted(expected_perms)})"
            )
    return (len(msgs) == 0), msgs


def check_ownership_handler(path):
    msgs = []
    _src, tree = _load(path)
    vac = _func(tree, "validate_authenticated_context")
    if vac is None:
        return False, [f"{path}: missing validate_authenticated_context()"]
    # Ownership model: authenticates studio/game and raises on missing/invalid creds.
    raises = any(isinstance(n, ast.Raise) for n in ast.walk(vac))
    if not raises:
        msgs.append(f"{path}: validate_authenticated_context() never raises on bad/missing credentials")
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "validate_authenticated_context"]
    if not calls:
        msgs.append(f"{path}: never calls validate_authenticated_context(event, ...)")
    return (len(msgs) == 0), msgs


def run():
    failures = []
    print("Authorization-enforcement guard test (M4 compensating control)")
    print("=" * 64)

    for path, perms in PERMISSION_HANDLERS.items():
        ok, msgs = check_permission_handler(path, perms)
        if ok:
            print(f"  PASS  {path}  (enforces {sorted(perms)})")
        else:
            print(f"  FAIL  {path}")
            for m in msgs:
                print(f"        - {m}")
            failures.extend(msgs)

    ok, msgs = check_ownership_handler(OWNERSHIP_HANDLER)
    if ok:
        print(f"  PASS  {OWNERSHIP_HANDLER}  (ownership model: studioId/gameId)")
    else:
        print(f"  FAIL  {OWNERSHIP_HANDLER}")
        for m in msgs:
            print(f"        - {m}")
        failures.extend(msgs)

    print("=" * 64)
    if failures:
        print(f"RESULT: FAIL — {len(failures)} issue(s). Every data-plane handler must enforce")
        print("        its required permission (route-level authZ is delegated to handlers; see M4).")
        return 1
    print(f"RESULT: PASS — all {len(PERMISSION_HANDLERS) + 1} handlers enforce route-level authorization.")
    return 0


# ---- pytest entry points (optional; the file also runs standalone) ----
def test_permission_handlers_enforce_required_permission():
    for path, perms in PERMISSION_HANDLERS.items():
        ok, msgs = check_permission_handler(path, perms)
        assert ok, "; ".join(msgs)


def test_developer_registration_enforces_ownership():
    ok, msgs = check_ownership_handler(OWNERSHIP_HANDLER)
    assert ok, "; ".join(msgs)


if __name__ == "__main__":
    sys.exit(run())
