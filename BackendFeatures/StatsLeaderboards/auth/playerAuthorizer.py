# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
playerAuthorizer.py -- API Gateway Lambda Authorizer for Player-Facing APIs

=============================================================================
INTEGRATION POINT — Player Authentication
=============================================================================

This authorizer handles all player-facing API routes:
    /leaderboards/stats/*       — store player stats and scores
    /leaderboards/scores        — get leaderboard scores
    /leaderboards/player/*      — get player stats, standings

It is a SEPARATE authorizer from backendAuthorizer.py (which handles
backend/developer routes using the StudioAPI Key). This separation ensures:
    - Independent caching (backend and player auth caches are isolated)
    - Independent scaling and monitoring
    - Clean separation of concerns

HOW TO INTEGRATE:
    1. Replace the placeholder in lambda_handler() with your token validation
    2. Add any dependencies to layers/valkey-glide-layer/requirements.txt
    3. Deploy: the CDK stack (app.py) already wires this to player routes

The context you return is read by player Lambda functions via:
    event['requestContext']['authorizer']

Required context fields:
    studioId    — your studio identifier (from your config or token claims)
    gameId      — your game identifier (from your config or token claims)
    permissions — comma-separated: "read", "write", or "read,write"
    playerId    — the authenticated player's ID (recommended)
=============================================================================
"""

import json
import os
from typing import Dict, Any, Optional

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext

# Initialize AWS Lambda Powertools
logger = Logger(service="player-authorizer")
tracer = Tracer(service="player-authorizer")

# Environment variables
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'dev')

# =============================================================================
# CONFIGURE THESE for your game — or read them from environment variables,
# SSM Parameter Store, or your token claims.
# =============================================================================
STUDIO_ID = os.environ.get('STUDIO_ID', '')
GAME_ID = os.environ.get('GAME_ID', '')


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


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    Player authentication authorizer.

    =========================================================================
    INTEGRATION POINT — Replace the placeholder below with your game's
    player token validation logic.
    =========================================================================
    """
    method_arn = event.get('methodArn', '*')
    headers = event.get('headers', {})

    try:
        # -----------------------------------------------------------------
        # PLACEHOLDER — Replace this entire block with your auth logic.
        #
        # Example with JWT:
        #
        #     import jwt
        #     normalized = {k.lower(): v for k, v in headers.items()}
        #     token = normalized.get('authorization', '').replace('Bearer ', '').strip()
        #     if not token:
        #         return generate_policy('no-token', 'Deny', method_arn)
        #     try:
        #         claims = jwt.decode(token, YOUR_SECRET, algorithms=['HS256'])
        #         return generate_policy(
        #             principal_id=claims['player_id'],
        #             effect='Allow',
        #             resource=method_arn,
        #             context={
        #                 'authType': 'player_token',
        #                 'studioId': STUDIO_ID,
        #                 'gameId': GAME_ID,
        #                 'permissions': 'read,write',
        #                 'playerId': claims['player_id']
        #             }
        #         )
        #     except jwt.InvalidTokenError:
        #         return generate_policy('invalid-token', 'Deny', method_arn)
        #
        # -----------------------------------------------------------------

        logger.warning("Player authentication not yet integrated")
        return generate_policy(
            principal_id="player-auth-not-configured",
            effect='Allow',
            resource=method_arn,
            context={
                'authType': 'player_auth_not_configured',
                'studioId': '',
                'gameId': '',
                'permissions': '',
                'playerId': ''
            }
        )

    except Exception as e:
        logger.error(f"Unexpected player authorizer error: {str(e)}")
        return generate_policy(principal_id="system-error", effect='Deny', resource=method_arn)
