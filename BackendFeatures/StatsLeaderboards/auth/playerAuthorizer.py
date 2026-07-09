# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
playerAuthorizer.py -- API Gateway Lambda Authorizer for Player-Facing APIs

Handles the player-facing routes:
    POST /leaderboards/stats            -- submit a player's score
    POST /leaderboards/scores           -- query a leaderboard
    POST /leaderboards/player/stats     -- player stat history
    POST /leaderboards/player/standing  -- player rank / neighbours

Separate from backendAuthorizer.py (StudioAPI-key / developer routes) so the two
auth paths cache, scale, and are monitored independently.

Mode is selected by the PLAYER_AUTH_MODE environment variable:
    identity (default) -- validate access tokens issued by the AWS Game Backend
                          Custom Identity Component: an RS256 JWT verified against
                          the issuer's JWKS (aud "gamebackend", matching issuer).
    custom             -- bring-your-own: standalone deployments implement their
                          own validation in _authorize_custom(); fail closed until
                          they do.

Context returned to the player Lambdas via event['requestContext']['authorizer']:
    studioId, gameId  -- this deployment's studio/game (read from SSM)
    permissions       -- "read", "write", or "read,write"
    playerId          -- authenticated player id (the JWT 'sub' claim)
"""

import json
import os
import time
import threading
from typing import Dict, Any, Optional

import jwt
import requests
import boto3
from botocore.exceptions import ClientError

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="player-authorizer")
tracer = Tracer(service="player-authorizer")

# Configuration (wired by the CDK stack, see app.py player_authorizer env)
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'dev')
PLAYER_AUTH_MODE = os.environ.get('PLAYER_AUTH_MODE', 'identity').strip().lower()
ISSUER_URL = os.environ.get('ISSUER_URL', '').rstrip('/')
TOKEN_AUDIENCE = os.environ.get('TOKEN_AUDIENCE', 'gamebackend')
SSM_PARAMETER_PREFIX = os.environ.get('SSM_PARAMETER_PREFIX', f'/game-statsleaderboards-{ENVIRONMENT}')

# Log the resolved config once at cold start. If tokens are rejected with 403, the
# most common cause is ISSUER_URL not matching the token's `iss`. That makes the
# expected issuer easy to compare against the token. None of these are secrets.
logger.info(
    f"Player authorizer init: mode={PLAYER_AUTH_MODE}, "
    f"issuer={ISSUER_URL or '(unset)'}, audience={TOKEN_AUDIENCE}"
)

# scope claim -> permissions granted. Tighten a scope here (e.g. "guest": "read")
# to restrict it. An unrecognized scope is denied.
SCOPE_PERMISSIONS = {
    "guest": "read,write",
    "authenticated": "read,write",
}

ssm = boto3.client('ssm')

# JWKS cache. Keys rotate, so refetch on an unknown kid or once the cache is stale.
_jwks_lock = threading.Lock()
_jwks_keys: Dict[str, Any] = {}
_jwks_fetched_at = 0.0
_JWKS_TTL = 900

# studio/game identity cache (single-tenant: one fixed pair per deployment)
_registration_cache: Optional[Dict[str, str]] = None


def generate_policy(principal_id: str, effect: str, resource: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """Generate IAM policy for API Gateway."""
    if resource and 'execute-api' in resource:
        arn_parts = resource.split('/')
        if len(arn_parts) >= 2:
            base_arn = arn_parts[0]
            stage = arn_parts[1]
            resource = f"{base_arn}/{stage}/*/*"

    policy = {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [{
                'Action': 'execute-api:Invoke',
                'Effect': effect,
                'Resource': resource
            }]
        }
    }
    if context:
        policy['context'] = {k: str(v) if not isinstance(v, str) else v for k, v in context.items()}
    return policy


def _refresh_jwks() -> None:
    """Fetch the issuer's public keys into the module cache."""
    global _jwks_keys, _jwks_fetched_at
    response = requests.get(f"{ISSUER_URL}/.well-known/jwks.json", timeout=3)
    response.raise_for_status()
    _jwks_keys = {k["kid"]: k for k in response.json().get("keys", []) if "kid" in k}
    _jwks_fetched_at = time.time()


def _get_signing_key(kid: str):
    """Return the RSA key for kid, refetching the JWKS once if it's missing or stale."""
    with _jwks_lock:
        if kid not in _jwks_keys or (time.time() - _jwks_fetched_at) > _JWKS_TTL:
            _refresh_jwks()
        key = _jwks_keys.get(kid)
    if not key:
        return None
    return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))


def _get_studio_game() -> Optional[Dict[str, str]]:
    """Read this deployment's studioId/gameId from SSM (cached; single-tenant)."""
    global _registration_cache
    if _registration_cache is not None:
        return _registration_cache
    try:
        param = ssm.get_parameter(Name=f"{SSM_PARAMETER_PREFIX}/config/registration")
        data = json.loads(param['Parameter']['Value'])
        _registration_cache = {"studioId": data["studioId"], "gameId": data["gameId"]}
        return _registration_cache
    except (ClientError, KeyError, json.JSONDecodeError) as e:
        logger.error(f"Could not read studio/game registration from SSM: {e}")
        return None


def _authorize_identity(event: Dict[str, Any], method_arn: str) -> Dict[str, Any]:
    """Validate a Custom Identity Component access token (RS256 JWT)."""
    if not ISSUER_URL:
        logger.error("PLAYER_AUTH_MODE=identity but ISSUER_URL is not set - denying.")
        return generate_policy("issuer-not-configured", 'Deny', method_arn)

    headers = {k.lower(): v for k, v in (event.get('headers') or {}).items()}
    auth_header = headers.get('authorization', '').strip()
    token = auth_header[7:].strip() if auth_header.lower().startswith('bearer ') else auth_header
    if not token:
        return generate_policy("no-token", 'Deny', method_arn)

    # Identify the signing key from the (unverified) header, then look it up in the JWKS.
    try:
        kid = jwt.get_unverified_header(token).get('kid')
    except jwt.InvalidTokenError:
        return generate_policy("malformed-token", 'Deny', method_arn)
    if not kid:
        return generate_policy("no-kid", 'Deny', method_arn)

    signing_key = _get_signing_key(kid)
    if signing_key is None:
        logger.warning("No JWKS key matched the token's kid.")
        return generate_policy("unknown-key", 'Deny', method_arn)

    # Verify signature, audience, issuer, and expiry.
    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=TOKEN_AUDIENCE,
            issuer=ISSUER_URL,
            options={"require": ["exp", "iss", "sub"]},
        )
    except jwt.InvalidTokenError as e:
        logger.info(f"Token rejected: {e}")
        return generate_policy("invalid-token", 'Deny', method_arn)

    permissions = SCOPE_PERMISSIONS.get(claims.get('scope'))
    if not permissions:
        logger.info(f"Unrecognized scope '{claims.get('scope')}' - denying.")
        return generate_policy("unrecognized-scope", 'Deny', method_arn)

    studio_game = _get_studio_game()
    if not studio_game:
        return generate_policy("registration-unavailable", 'Deny', method_arn)

    player_id = claims['sub']
    return generate_policy(
        principal_id=player_id,
        effect='Allow',
        resource=method_arn,
        context={
            'authType': 'player_token',
            'studioId': studio_game['studioId'],
            'gameId': studio_game['gameId'],
            'permissions': permissions,
            'playerId': player_id,
        },
    )


def _authorize_custom(event: Dict[str, Any], method_arn: str) -> Dict[str, Any]:
    """
    Standalone / bring-your-own player auth. Fail closed until implemented.

    Replace the body below with your own token validation, and on success return
    an Allow via generate_policy(...) with context fields: studioId, gameId,
    permissions ("read" / "write" / "read,write"), and playerId.
    """
    logger.warning(
        "PLAYER_AUTH_MODE=custom but no custom validation is implemented - "
        "denying (fail closed). Implement _authorize_custom() in auth/playerAuthorizer.py."
    )
    return generate_policy(
        principal_id="player-auth-not-configured",
        effect='Deny',
        resource=method_arn,
        context={'authType': 'player_auth_not_configured'},
    )


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """Player authorizer. Dispatches on PLAYER_AUTH_MODE; fails closed on any error."""
    method_arn = event.get('methodArn', '*')
    try:
        if PLAYER_AUTH_MODE == 'custom':
            return _authorize_custom(event, method_arn)
        return _authorize_identity(event, method_arn)
    except Exception as e:
        logger.error(f"Unexpected player authorizer error: {str(e)}")
        return generate_policy(principal_id="system-error", effect='Deny', resource=method_arn)
