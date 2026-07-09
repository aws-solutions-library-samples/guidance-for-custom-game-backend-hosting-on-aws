# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
storePlayerStatsAndScores.py -- Store Player Game Stats and Update Leaderboards Lambda Function

Processes individual player game reports and updates leaderboards in real-time:
- Validates game reports and scores (including time-based formats)
- Stores stats in DynamoDB for persistence
- Updates leaderboards in Valkey/MemoryDB using GLIDE 2.4.1
- Handles expired leaderboards and read-only modes
- Supports multiple score strategies (replace, best, cumulative)

Updated for Python 3.13 and Valkey-GLIDE 2.4.1
High-performance, robust implementation optimized for data integrity and performance.
"""

import os
import sys
import json
import time
import uuid
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional, Union, Tuple, List
from functools import wraps, lru_cache
from datetime import datetime, timezone
from decimal import Decimal
import decimal

def convert_floats_to_decimal(obj):
    """Convert float values to Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        # Convert to Decimal and normalize to remove trailing zeros
        # This handles scientific notation properly (e.g., 1.23456e5 -> 123456 not 123456.0)
        decimal_val = Decimal(str(obj))
        return decimal_val.normalize()
    elif isinstance(obj, dict):
        return {k: convert_floats_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_floats_to_decimal(item) for item in obj]
    return obj

import re
import dateutil.parser

# AWS imports
import boto3
from botocore.exceptions import ClientError

# Valkey-GLIDE imports with fallback
try:
    from glide import (
        GlideClientConfiguration as BaseClientConfiguration,
        NodeAddress,
        GlideClusterClientConfiguration as ClusterClientConfiguration,
        GlideClusterClient,
        GlideClient,
        ServerCredentials as ValkeyCredentials,
        Logger as GlideLogger,
        LogLevel,
        ProtocolVersion,
        RangeByIndex,
        ExpirySet,
        ExpiryType
    )
    # valkey-glide moved the protobuf module from `glide.protobuf` (<=2.0.x) to
    # `glide_shared.protobuf` (>=2.1.0). Prefer the current location, fall back to
    # the legacy one so the import works regardless of the layer's pinned version.
    try:
        from glide_shared.protobuf.connection_request_pb2 import TlsMode
    except ImportError:
        from glide.protobuf.connection_request_pb2 import TlsMode
    VALKEY_AVAILABLE = True
except ImportError as e:
    print(f"WARNING: valkey_glide not available: {e}")
    VALKEY_AVAILABLE = False
    # Create dummy classes to prevent import errors
    class BaseClientConfiguration: pass
    class NodeAddress: pass
    class ClusterClientConfiguration: pass
    class GlideClusterClient: pass
    class GlideClient: pass
    class ValkeyCredentials: pass
    class TlsMode: pass
    class GlideLogger: pass
    class LogLevel: pass

# AWS Lambda Powertools
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext

# Environment variables
LEADERBOARDS_CONFIG_TABLE_NAME = os.environ.get('gameLeaderboardsConfigTablename')
GAME_STATS_TABLE_NAME = os.environ.get('gameStatsAndScoresTablename')
MEMORYDB_CLUSTER_NAME = os.environ.get('gameLeaderboardsMemoryDBName')
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()

# Valkey configuration
VALKEY_USE_TLS = os.environ.get('VALKEY_USE_TLS', 'true').lower() == 'true'
VALKEY_CLUSTER_MODE = os.environ.get('VALKEY_CLUSTER_MODE', 'true').lower() == 'true'

# Performance optimization settings
MAX_CONCURRENT_OPERATIONS = int(os.environ.get('MAX_CONCURRENT_OPERATIONS', '10'))
CONNECTION_TIMEOUT = int(os.environ.get('GLIDE_CONNECTION_TIMEOUT_MS', '2000'))
REQUEST_TIMEOUT = int(os.environ.get('GLIDE_REQUEST_TIMEOUT_MS', '5000'))
CONFIG_CACHE_TTL = int(os.environ.get('CONFIG_CACHE_TTL', '300'))

# Initialize AWS clients
secretsmanager = boto3.client('secretsmanager', config=boto3.session.Config(
    max_pool_connections=10,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
))

dynamodb = boto3.resource('dynamodb', config=boto3.session.Config(
    max_pool_connections=50,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
))

# Initialize Powertools
logger = Logger(service="leaderboard-service", level=LOG_LEVEL)
tracer = Tracer(service="leaderboard-service")

# Log initialization info
logger.info(f"Logger initialized with level: {LOG_LEVEL}")
logger.info(f"Function environment: {os.environ.get('ENVIRONMENT', 'unknown')}")
logger.info(f"Config table: {LEADERBOARDS_CONFIG_TABLE_NAME}")
logger.info(f"Stats table: {GAME_STATS_TABLE_NAME}")
logger.info(f"MemoryDB cluster: {MEMORYDB_CLUSTER_NAME}")

# Initialize DynamoDB tables
leaderboards_config_table = dynamodb.Table(LEADERBOARDS_CONFIG_TABLE_NAME) if LEADERBOARDS_CONFIG_TABLE_NAME else None
game_stats_table = dynamodb.Table(GAME_STATS_TABLE_NAME) if GAME_STATS_TABLE_NAME else None

# Constants
VALID_LEADERBOARD_TYPES = ["DESCENDING_LB", "ASCENDING_LB"]
VALID_SCORE_STRATEGIES = ["replace", "best", "cumulative"]
VALID_TIME_FORMATS = ["seconds", "milliseconds", "minutes_seconds", "hours_minutes_seconds"]

# Init placeholder keys for removal
INIT_PLACEHOLDER_KEYS = [
    "_init_topscore_", "_init_time_", "_init_distance_", 
    "_init_points_", "_init_rank_", "_init_level_"
]

# Global resources
valkey_client: Optional[Union[GlideClusterClient, GlideClient]] = None
thread_pool: Optional[ThreadPoolExecutor] = None
config_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}

# Resource validation cache (persists across invocations in same Lambda container)
_resources_validated = False
_validation_lock = threading.Lock()


# ============================================================================
# DECORATORS AND ERROR HANDLING
# ============================================================================

def log_execution(func):
    """
    Decorator to log function execution details with performance metrics.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        function_name = func.__name__
        request_id = None
        
        if len(args) > 0 and isinstance(args[0], dict) and 'requestContext' in args[0]:
            request_id = args[0].get('requestContext', {}).get('requestId', 'unknown')
        
        params = {}
        if len(args) > 0 and isinstance(args[0], dict):
            params = json.loads(json.dumps(args[0], default=str))
            
            # Sanitize sensitive data
            if 'body' in params:
                try:
                    body = json.loads(params['body']) if isinstance(params['body'], str) else params['body']
                    if 'gameReportBody' in body:
                        if 'fullRawGameReport' in body['gameReportBody']:
                            params['body'] = {
                                'gameReportBody': {
                                    **body['gameReportBody'],
                                    'fullRawGameReport': '[REDACTED]'
                                }
                            }
                except:
                    pass
        
        log_context = {
            "function": function_name,
            "request_id": request_id,
            "parameters": params,
            "operation": "start"
        }
        
        logger.info(f"Starting execution of {function_name}", extra=log_context)
        
        try:
            start_time = time.perf_counter()
            result = func(*args, **kwargs)
            execution_time = time.perf_counter() - start_time
            
            log_context.update({
                "operation": "complete",
                "execution_time_ms": int(execution_time * 1000),
                "execution_time_ns": int(execution_time * 1_000_000_000)
            })
            
            if isinstance(result, dict) and 'statusCode' in result:
                log_context["status_code"] = result['statusCode']
                
                if 200 <= result['statusCode'] < 300:
                    logger.info(f"Successfully completed {function_name}", extra=log_context)
                else:
                    log_context["response"] = result
                    logger.warning(f"Completed {function_name} with non-success status", extra=log_context)
            else:
                logger.info(f"Successfully completed {function_name}", extra=log_context)
            
            return result
            
        except Exception as e:
            log_context.update({
                "operation": "error",
                "error_type": e.__class__.__name__,
                "error_message": str(e)
            })
            logger.exception(f"Error in {function_name}", extra=log_context)
            raise
    
    return wrapper


class LeaderboardAuthenticationError(Exception):
    """Custom exception for leaderboard authentication failures"""
    pass


class LeaderboardNotFoundError(Exception):
    """Custom exception for leaderboard not found cases"""
    pass


class LeaderboardExpiredError(Exception):
    """Custom exception for expired leaderboard access"""
    pass


class PlayerIdentityMismatchError(Exception):
    """
    Raised when the playerID in the request does not match the authenticated
    player's identity (auth_context['playerId']). This is an anti-spoofing
    control: a player must only submit scores under their own identity. Mapped
    to HTTP 403 (Forbidden) — the caller is authenticated, but not permitted to
    act as the requested player. See the identity check in lambda_handler().
    """
    pass


def handle_errors(func):
    """
    Enhanced error handler with specific error types and recovery strategies.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except LeaderboardNotFoundError as e:
            logger.error(f"Not found error: {str(e)}")
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'gameReportResponse': {
                        'error': 'Not Found',
                        'message': str(e),
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                })
            }
        except LeaderboardExpiredError as e:
            logger.error(f"Leaderboard expired error: {str(e)}")

            # Determine if this is read-only or auto-deletion expiry based on error message
            if "read-only mode" in str(e):
                # Read-only preservation expiry
                return {
                    'statusCode': 423,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'gameReportResponse': {
                            'error': 'Leaderboard Locked',
                            'errorCode': 'LEADERBOARD_EXPIRED_READONLY',
                            'message': 'Cannot write to expired leaderboard in read-only mode',
                            'details': str(e),
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                    })
                }
            else:
                # Auto-deletion expiry
                return {
                    'statusCode': 423,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'gameReportResponse': {
                            'error': 'Leaderboard Expired',
                            'errorCode': 'LEADERBOARD_EXPIRED_AUTODELETE',
                            'message': 'Cannot write to expired leaderboard scheduled for deletion',
                            'details': str(e),
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                    })
                }
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'gameReportResponse': {
                        'error': 'Bad Request',
                        'message': str(e),
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                })
            }
        except LeaderboardAuthenticationError as e:
            logger.error(f"Authentication error: {str(e)}")
            return {
                'statusCode': 401,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'gameReportResponse': {
                        'error': 'Unauthorized',
                        'message': str(e),
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                })
            }
        except PlayerIdentityMismatchError as e:
            # 403 Forbidden: authenticated, but attempting to act as another player.
            # The WARNING with the PLAYER_ID_MISMATCH marker is emitted at the
            # detection site (below) for abuse monitoring; here we only shape the
            # client response and deliberately avoid echoing the attempted ID back.
            logger.error(f"Player identity mismatch: {str(e)}")
            return {
                'statusCode': 403,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'gameReportResponse': {
                        'error': 'Forbidden',
                        'message': str(e),
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                })
            }
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            logger.error(f"AWS service error: {error_code} - {error_message}")
            
            status_code = 500
            if error_code in ['ResourceNotFoundException', 'TableNotFoundException']:
                status_code = 404
            elif error_code in ['AccessDeniedException', 'UnauthorizedException']:
                status_code = 403
            elif error_code in ['ThrottlingException', 'ProvisionedThroughputExceededException']:
                status_code = 429
            
            return {
                'statusCode': status_code,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'gameReportResponse': {
                        'error': error_code,
                        'message': error_message,
                        'note': 'Data may have been partially stored',
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                })
            }
        except ConnectionError as e:
            logger.error(f"Valkey connection error: {str(e)}")
            global valkey_client
            valkey_client = None
            return {
                'statusCode': 503,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'gameReportResponse': {
                        'error': 'Service Unavailable',
                        'message': 'Database connection error',
                        'note': 'Data may have been stored in DynamoDB, but leaderboard update failed',
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                })
            }
        except Exception as e:
            error_msg = str(e)
            connection_error_keywords = ['ConnectionNotFound', 'connection error', 'ConnectionError',
                                         'Received connection error', 'ConnectionNotFoundForRoute']
            if any(keyword in error_msg for keyword in connection_error_keywords):
                logger.warning(f"Valkey connection/routing error detected, invalidating client: {error_msg}")
                valkey_client = None
            
            logger.exception(f"Unexpected error: {error_msg}")
            return {
                'statusCode': 500,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'gameReportResponse': {
                        'error': 'Internal Server Error',
                        'message': 'An unexpected error occurred',
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }
                })
            }
    
    return wrapper


# ============================================================================
# AUTHENTICATION AND VALIDATION
# ============================================================================

@tracer.capture_method
def validate_authenticated_context(event: Dict[str, Any], required_permission: str = 'read') -> Dict[str, str]:
    """
    Validate player authentication from API Gateway authorizer context.
    
    The player authorizer (auth/playerAuthorizer.py) validates the caller and
    populates the authorizer context with the fields below (identity mode by
    default, custom mode for bring-your-own auth). This function reads that
    context and enforces permission checks.
    
    Required authorizer context fields:
        - studioId:    Your studio identifier (set by authorizer from your config)
        - gameId:      Your game identifier (set by authorizer from your config)
        - permissions: Comma-separated permission string, e.g. "read,write"
        - playerId:    (Optional) Authenticated player ID from your auth system
    
    Args:
        event: Lambda event containing authorizer context from API Gateway
        required_permission: Required permission level ('read' or 'write')
        
    Returns:
        Dictionary containing authenticated context fields
        
    Raises:
        LeaderboardAuthenticationError: If authentication validation fails
    """
    logger.info("=== PLAYER AUTHENTICATION VALIDATION START ===")
    logger.info(f"Required permission: {required_permission}")
    
    auth_context = event.get('requestContext', {}).get('authorizer', {})
    
    if not auth_context:
        logger.error("No authorizer context found in request")
        raise LeaderboardAuthenticationError("Authentication required - no authorizer context found")
    
    # Check if player auth has not been integrated yet (authorizer marker)
    if auth_context.get('authType') == 'player_auth_not_configured':
        logger.error("Player authentication not yet integrated")
        raise LeaderboardAuthenticationError(
            "Player authentication has not been integrated. "
            "See auth/playerAuthorizer.py "
            "and docs/api_reference.md Section 1.5 for integration guidance."
        )
    
    # =========================================================================
    # REQUIRED FIELDS — These must be populated by your Lambda authorizer.
    # The authorizer validates the player's credentials (your auth system) and
    # sets these fields so downstream Lambdas know which studio/game the
    # request belongs to and what the caller is allowed to do.
    # =========================================================================
    authenticated_studio_id = auth_context.get('studioId')
    authenticated_game_id = auth_context.get('gameId')
    permissions = auth_context.get('permissions', '').split(',') if auth_context.get('permissions') else []
    
    # =========================================================================
    # OPTIONAL — Player identity from your auth system.
    # Your authorizer should set 'playerId' after validating the player's token.
    # This can be used for per-player rate limiting, audit logging, etc.
    # =========================================================================
    player_id = auth_context.get('playerId', '')
    
    if not authenticated_studio_id:
        logger.error("No studioId in authorizer context — check your Lambda authorizer configuration")
        raise LeaderboardAuthenticationError("Authentication required - missing studio identifier")
    
    if not authenticated_game_id:
        logger.error("No gameId in authorizer context — check your Lambda authorizer configuration")
        raise LeaderboardAuthenticationError("Authentication required - missing game identifier")
    
    if required_permission not in permissions:
        logger.warning(f"Insufficient permissions: required={required_permission}, granted={permissions}")
        raise LeaderboardAuthenticationError(f"Insufficient permissions - {required_permission} access required")
    
    logger.info(f"Player auth validated — studio: {authenticated_studio_id}, game: {authenticated_game_id}, player: {player_id or 'not set'}")
    
    return {
        'studioId': authenticated_studio_id,
        'gameId': authenticated_game_id,
        'permissions': permissions,
        'playerId': player_id
    }


# ============================================================================
# VALKEY/MEMORYDB CONNECTION MANAGEMENT
# ============================================================================

@lru_cache(maxsize=10)
@tracer.capture_method
def get_valkey_credentials_and_config() -> Dict[str, Any]:
    """
    Retrieve Valkey/MemoryDB credentials and configuration from environment variables and Secrets Manager.
    """
    credentials_config = {}
    
    # Get endpoint from environment variable
    endpoint = os.environ.get('MEMORYDB_CLUSTER_ENDPOINT')
    if not endpoint:
        endpoint = os.environ.get('VALKEY_CLUSTER_ENDPOINT') or os.environ.get('MEMORYDB_ENDPOINT')
        
    if not endpoint:
        raise ValueError("Required environment variable MEMORYDB_CLUSTER_ENDPOINT is not set")
    
    credentials_config['endpoint'] = endpoint
    logger.info(f"Using MemoryDB endpoint: {endpoint}")
    
    # Get port from environment variable
    port = os.environ.get('MEMORYDB_PORT', '6379')
    try:
        credentials_config['port'] = int(port)
    except (ValueError, TypeError):
        logger.warning(f"Invalid port value '{port}', using default 6379")
        credentials_config['port'] = 6379
    
    # Get credentials from Secrets Manager
    secret_arn = os.environ.get('MEMORYDB_SECRET_ARN')
    if not secret_arn:
        secret_arn = os.environ.get('VALKEY_SECRET_ARN') or os.environ.get('MEMORYDB_CREDENTIALS_SECRET')
        
    if not secret_arn:
        raise ValueError("Required environment variable MEMORYDB_SECRET_ARN is not set")
    
    try:
        logger.info(f"Retrieving secret from: {secret_arn}")
        response = secretsmanager.get_secret_value(SecretId=secret_arn)
        
        if 'SecretString' in response:
            secret_data = json.loads(response['SecretString'])
        elif 'SecretBinary' in response:
            import base64
            secret_data = json.loads(base64.b64decode(response['SecretBinary']))
        else:
            raise ValueError("Secret does not contain SecretString or SecretBinary")
        
        username = (
            secret_data.get('username') or 
            secret_data.get('Username') or 
            secret_data.get('user') or 
            secret_data.get('User') or
            'default'
        )
        
        password = (
            secret_data.get('password') or 
            secret_data.get('Password') or 
            secret_data.get('pass') or 
            secret_data.get('Pass')
        )
        
        if not password:
            raise ValueError("Password not found in secret")
        
        password = str(password).strip()
        
        if '\n' in password or '\r' in password:
            logger.warning("Password contains newline characters, removing them")
            password = password.replace('\n', '').replace('\r', '')

        credentials_config['username'] = username
        credentials_config['password'] = password
        credentials_config['tls'] = secret_data.get('tls', True)

        logger.info(f"Successfully retrieved credentials for user: {username}")
        
        return credentials_config
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ResourceNotFoundException':
            logger.error(f"Secret not found: {secret_arn}")
        elif error_code == 'AccessDeniedException':
            logger.error(f"Access denied to secret: {secret_arn}")
        else:
            logger.error(f"AWS Secrets Manager error: {error_code}")
        raise ValueError(f"Failed to retrieve Valkey credentials: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error retrieving credentials: {str(e)}")
        raise ValueError(f"Failed to retrieve Valkey credentials: {str(e)}")


async def get_valkey_client() -> Union[GlideClusterClient, GlideClient]:
    """
    Get or create a high-performance Valkey client using GLIDE 2.4.1+ internal connection pooling.
    """
    global valkey_client
    
    # Reuse existing client if available and connected
    if valkey_client:
        try:
            ping_result = await valkey_client.ping()
            if ping_result == b"PONG":
                await valkey_client.exists(["connection_test_key"])
                logger.debug("Reusing existing Valkey client connection")
                return valkey_client
            else:
                logger.warning(f"Client ping returned unexpected result, creating new client")
                valkey_client = None
        except Exception as e:
            logger.warning(f"Existing client connection failed, creating new client: {str(e)}")
            valkey_client = None
    
    try:
        valkey_config = get_valkey_credentials_and_config()
        VALKEY_CLUSTER_ENDPOINT = valkey_config['endpoint']
        VALKEY_PORT = valkey_config['port']
        VALKEY_USERNAME = valkey_config['username']
        VALKEY_PASSWORD = valkey_config['password']
        VALKEY_USE_TLS = valkey_config.get('tls', True)

        if not VALKEY_USERNAME or not VALKEY_PASSWORD:
            raise ValueError(f"Invalid credentials")

        logger.info(f"Initializing Valkey connection - Endpoint: {VALKEY_CLUSTER_ENDPOINT}:{VALKEY_PORT}")
        
    except Exception as e:
        logger.error(f"Failed to retrieve Valkey configuration: {str(e)}")
        raise ValueError(f"Unable to retrieve Valkey configuration: {str(e)}")
    
    # Set GLIDE logger configuration
    GlideLogger.set_logger_config(LogLevel.INFO)
    
    # Create credentials object
    credentials = ValkeyCredentials(
        username=VALKEY_USERNAME,
        password=VALKEY_PASSWORD
    )
    
    try:
        if VALKEY_CLUSTER_MODE:
            addresses = [NodeAddress(VALKEY_CLUSTER_ENDPOINT, VALKEY_PORT)]
            
            config = ClusterClientConfiguration(
                addresses=addresses,
                use_tls=VALKEY_USE_TLS,
                credentials=credentials,
                request_timeout=REQUEST_TIMEOUT,
                client_name="player-leaderboard-lambda",
                protocol=ProtocolVersion.RESP3
            )
            
            logger.info(f"Creating GLIDE cluster client...")            
            valkey_client = await GlideClusterClient.create(config)
            logger.info(f"Successfully connected to Valkey cluster")
            
        else:
            address = NodeAddress(VALKEY_CLUSTER_ENDPOINT, VALKEY_PORT)
            
            config = BaseClientConfiguration(
                addresses=[address],
                use_tls=VALKEY_USE_TLS,
                credentials=credentials,
                request_timeout=REQUEST_TIMEOUT,
                client_name="player-leaderboard-lambda",
                protocol=ProtocolVersion.RESP3
            )

            logger.info("Creating GLIDE standalone client...")
            valkey_client = await GlideClient.create(config)
            logger.info("Successfully connected to Valkey standalone")
                
        return valkey_client
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to connect to Valkey: {error_msg}")
        
        if "WRONGPASS" in error_msg or "invalid username-password" in error_msg:
            logger.error(f"Authentication failed for user '{VALKEY_USERNAME}'")
        elif "Connection refused" in error_msg or "timeout" in error_msg.lower():
            logger.error(f"Connection failed to {VALKEY_CLUSTER_ENDPOINT}:{VALKEY_PORT}")
        
        raise ConnectionError(f"Unable to connect to Valkey: {error_msg}")


def get_thread_pool() -> ThreadPoolExecutor:
    """
    Get or create a thread pool for concurrent operations.
    """
    global thread_pool
    if thread_pool is None:
        thread_pool = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_OPERATIONS,
            thread_name_prefix="player-worker"
        )
    return thread_pool


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def decimal_serializer(obj):
    """
    JSON serializer for objects not serializable by default json code.
    """
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif hasattr(obj, '__dict__'):
        return obj.__dict__
    else:
        return str(obj)


def generate_sorted_list_name(game_id: str, game_mode: str, leaderboard_name: str) -> str:
    """
    Generate a consistent sorted list name for leaderboards.
    """
    return f"{game_id}:{game_mode}:{leaderboard_name}"


@tracer.capture_method
def validate_aws_resources() -> None:
    """
    Enhanced AWS resource validation with health checks.
    
    OPTIMIZATION: Cache validation results per Lambda container.
    Only validates once per cold start, not on every request.
    This prevents DynamoDB DescribeTable throttling under high load.
    """
    global _resources_validated
    
    # Fast path: Already validated in this container
    if _resources_validated:
        logger.debug("Resources already validated in this container, skipping")
        return
    
    # Thread-safe validation (in case of concurrent invocations)
    with _validation_lock:
        # Double-check after acquiring lock
        if _resources_validated:
            return
        
        logger.info("Validating AWS resources (first time in this container)")
        
        if not LEADERBOARDS_CONFIG_TABLE_NAME:
            raise ValueError("Required environment variable gameLeaderboardsConfigTablename is not set")
        
        if not GAME_STATS_TABLE_NAME:
            raise ValueError("Required environment variable gameStatsAndScoresTablename is not set")
        
        try:
            valkey_config = get_valkey_credentials_and_config()
            logger.info("Successfully validated Valkey configuration from Secrets Manager")
        except Exception as e:
            raise ValueError(f"Failed to retrieve Valkey configuration: {str(e)}")
        
        try:
            # Check config table
            config_table_desc = leaderboards_config_table.meta.client.describe_table(
                TableName=LEADERBOARDS_CONFIG_TABLE_NAME
            )
            
            if config_table_desc['Table']['TableStatus'] != 'ACTIVE':
                raise ValueError(f"Leaderboards config table is not active")
            
            # Check stats table
            stats_table_desc = game_stats_table.meta.client.describe_table(
                TableName=GAME_STATS_TABLE_NAME
            )
            
            if stats_table_desc['Table']['TableStatus'] != 'ACTIVE':
                raise ValueError(f"Game stats table is not active")
            
            logger.info("DynamoDB tables validated successfully")
            
        except ClientError as e:
            logger.error(f"Error validating DynamoDB tables: {str(e)}")
            raise
        
        # Mark as validated
        _resources_validated = True
        logger.info("✓ AWS resources validated and cached for this container")


def run_async(coro):
    """
    Optimized helper function to run async code in sync context.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(coro)


# ============================================================================
# TIME SCORE PARSING
# ============================================================================

@tracer.capture_method
def parse_and_validate_time_score(
    score_value: Union[str, int, float, Decimal],
    leaderboard_config: Dict[str, Any]
) -> Decimal:
    """
    Parse and validate time-based scores with multiple format support.
    """
    score_type = leaderboard_config.get('scoreType', 'score')
    
    # Debug detail: keep full score value + config at DEBUG (avoids logging
    # player payloads / config bodies to CloudWatch at INFO — L2).
    logger.debug(f"=== SCORE VALIDATION DEBUG ===")
    logger.debug(f"Score value: {score_value} (type: {type(score_value)})")
    logger.debug(f"Score type: {score_type}")
    logger.debug(f"Leaderboard config: {json.dumps(leaderboard_config, default=str)}")
    
    # If not a time score, just validate as numeric
    if score_type != 'time':
        try:
            score = Decimal(str(score_value))
            logger.info(f"Parsed score as Decimal: {score}")
            
            if score < 0:
                logger.error(f"Score validation failed: negative score {score}")
                raise ValueError(f"Score must be non-negative")
            
            # Add range validation with type conversion (DynamoDB may store as strings)
            min_valid_score_raw = leaderboard_config.get('minValidScore')
            max_valid_score_raw = leaderboard_config.get('maxValidScore')
            
            min_valid_score = None
            max_valid_score = None
            
            if min_valid_score_raw is not None:
                try:
                    min_valid_score = Decimal(str(min_valid_score_raw))
                except (ValueError, TypeError, decimal.InvalidOperation):
                    logger.error(f"Invalid minValidScore: {min_valid_score_raw}")
                    
            if max_valid_score_raw is not None:
                try:
                    max_valid_score = Decimal(str(max_valid_score_raw))
                except (ValueError, TypeError, decimal.InvalidOperation):
                    logger.error(f"Invalid maxValidScore: {max_valid_score_raw}")
            
            logger.info(f"Score range validation - min: {min_valid_score}, max: {max_valid_score}, score: {score}")
            
            if min_valid_score is not None and score < min_valid_score:
                logger.error(f"Score validation failed: {score} < {min_valid_score}")
                raise ValueError(f"Score {score} is below minimum valid score {min_valid_score}")
            
            if max_valid_score is not None and score > max_valid_score:
                logger.error(f"Score validation failed: {score} > {max_valid_score}")
                raise ValueError(f"Score {score} exceeds maximum valid score {max_valid_score}")
            
            logger.info(f"Score validation passed: {score}")
            return score
        except (ValueError, TypeError, decimal.InvalidOperation) as e:
            logger.error(f"Score parsing failed: {str(e)}")
            raise ValueError(f"Invalid score value: {score_value}")
    
    # Time-specific parsing
    time_format = leaderboard_config.get('timeFormat', 'seconds')
    
    # Handle type conversion for time validation bounds (DynamoDB may store as strings)
    min_valid_time_raw = leaderboard_config.get('minValidTimeInSeconds', 0.001)
    max_valid_time_raw = leaderboard_config.get('maxValidTimeInSeconds', 86400.0)
    
    try:
        min_valid_time = float(min_valid_time_raw) if min_valid_time_raw is not None else 0.001
        max_valid_time = float(max_valid_time_raw) if max_valid_time_raw is not None else 86400.0
    except (ValueError, TypeError):
        logger.error(f"Invalid time validation bounds: min={min_valid_time_raw}, max={max_valid_time_raw}")
        min_valid_time = 0.001
        max_valid_time = 86400.0
    
    logger.info(f"=== TIME SCORE VALIDATION DEBUG ===")
    logger.info(f"Time format: {time_format}")
    logger.info(f"Raw min_valid_time: {min_valid_time} (type: {type(min_valid_time)})")
    logger.info(f"Raw max_valid_time: {max_valid_time} (type: {type(max_valid_time)})")
    logger.info(f"Time range: {min_valid_time} - {max_valid_time}")
    
    # Parse based on input type
    total_seconds = 0.0
    
    if isinstance(score_value, str):
        score_value = score_value.strip()
        # Parse time string formats
        if ':' in score_value:
            # Format: MM:SS.mmm or HH:MM:SS.mmm
            parts = score_value.split(':')
            try:
                if len(parts) == 2:
                    # MM:SS.mmm format
                    minutes = int(parts[0])
                    seconds = float(parts[1])
                    if minutes < 0 or seconds < 0:
                        raise ValueError("Time components cannot be negative")
                    total_seconds = minutes * 60 + seconds
                elif len(parts) == 3:
                    # HH:MM:SS.mmm format
                    hours = int(parts[0])
                    minutes = int(parts[1])
                    seconds = float(parts[2])
                    if hours < 0 or minutes < 0 or seconds < 0:
                        raise ValueError("Time components cannot be negative")
                    if minutes >= 60 or seconds >= 60:
                        raise ValueError("Minutes and seconds must be less than 60")
                    total_seconds = hours * 3600 + minutes * 60 + seconds
                else:
                    raise ValueError(f"Invalid time format: {score_value}")
            except (ValueError, TypeError) as e:
                raise ValueError(f"Invalid time format '{score_value}': {str(e)}")
        else:
            # Plain numeric string
            try:
                total_seconds = float(score_value)
            except (ValueError, TypeError):
                raise ValueError(f"Invalid time value: {score_value}")
    else:
        # Numeric input (int, float, Decimal)
        try:
            if time_format == 'milliseconds':
                total_seconds = float(score_value) / 1000.0
            else:
                total_seconds = float(score_value)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid time value: {score_value}")
    
    logger.info(f"Parsed time: {total_seconds} seconds")
    
    # Validate time range - convert to float for comparison
    min_time_float = float(min_valid_time) if min_valid_time is not None else 0.0
    max_time_float = float(max_valid_time) if max_valid_time is not None else float('inf')
    
    logger.info(f"=== TIME RANGE VALIDATION DEBUG ===")
    logger.info(f"Converted min_time_float: {min_time_float} (from {min_valid_time})")
    logger.info(f"Converted max_time_float: {max_time_float} (from {max_valid_time})")
    logger.info(f"Checking: {min_time_float} <= {total_seconds} <= {max_time_float}")
    
    if total_seconds < min_time_float:
        logger.error(f"Time validation failed: {total_seconds} < {min_time_float}")
        raise ValueError(f"Time {total_seconds:.3f}s is below minimum valid time {min_time_float}s")
    
    if total_seconds > max_time_float:
        logger.error(f"Time validation failed: {total_seconds} > {max_time_float}")
        raise ValueError(f"Time {total_seconds:.3f}s exceeds maximum valid time {max_time_float}s")
    
    # Apply precision
    time_precision = leaderboard_config.get('timePrecision', 3)
    try:
        time_precision = int(time_precision)
    except (ValueError, TypeError):
        time_precision = 3
    rounded_time = round(total_seconds, time_precision)
    
    logger.info(f"Time validation passed: {rounded_time}")
    return Decimal(str(rounded_time))


@tracer.capture_method
def is_leaderboard_expired(expiry_datetime_str: str) -> bool:
    """
    Check if a leaderboard has expired based on its expiry datetime.
    """
    try:
        if not expiry_datetime_str:
            return False
            
        expiry_dt = dateutil.parser.isoparse(expiry_datetime_str)
        current_utc = datetime.now(timezone.utc)
        
        return expiry_dt <= current_utc
    except Exception:
        return False


@tracer.capture_method
def is_leaderboard_expired_and_readonly(leaderboard_config: Dict[str, Any]) -> Tuple[bool, bool]:
    """
    Check if a leaderboard has expired and if it's in read-only mode.
    """
    try:
        expiry_datetime_str = leaderboard_config.get('optionalLBExpiryDateTimeStamp')
        if not expiry_datetime_str:
            return False, False
        
        expiry_dt = dateutil.parser.isoparse(expiry_datetime_str)
        current_utc = datetime.now(timezone.utc)
        is_expired = expiry_dt <= current_utc
        
        is_readonly_on_expiry = leaderboard_config.get('optionalLBReadOnlyOnExpiry', True)
        
        return is_expired, is_readonly_on_expiry
        
    except Exception as e:
        logger.warning(f"Error checking leaderboard expiry: {str(e)}")
        return False, False


@tracer.capture_method
def calculate_ttl_from_expiry(expiry_datetime_str: str) -> Optional[int]:
    """
    Calculate TTL seconds from expiry datetime for Valkey.
    """
    try:
        if not expiry_datetime_str:
            return None
            
        expiry_dt = dateutil.parser.isoparse(expiry_datetime_str)
        current_utc = datetime.now(timezone.utc)
        
        if expiry_dt <= current_utc:
            return None
            
        return int((expiry_dt - current_utc).total_seconds())
    except Exception:
        return None


# ============================================================================
# PLACEHOLDER MANAGEMENT
# ============================================================================

async def remove_init_placeholder_if_exists(client, sorted_list_name: str) -> None:
    """
    Remove any init placeholder from the sorted set when first real score is added.
    """
    try:
        # Check sorted set size first
        set_size = await client.zcard(sorted_list_name)
        if set_size <= 250:  # Only check for placeholders in smaller sets
            removed_count = await client.zrem(sorted_list_name, INIT_PLACEHOLDER_KEYS)
            if removed_count > 0:
                logger.debug(f"Removed {removed_count} init placeholder(s) from {sorted_list_name}")
    except Exception as e:
        # Non-critical operation - log but don't fail
        logger.warning(f"Failed to remove init placeholder from {sorted_list_name}: {str(e)}")


# ============================================================================
# REQUEST VALIDATION
# ============================================================================

@tracer.capture_method
def validate_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enhanced request validation with comprehensive checks.
    """
    # Check if body exists
    if 'body' not in event:
        raise ValueError("Request body is missing")
    
    # Parse body
    try:
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in request body")
    
    # Check if gameReportBody exists
    if 'gameReportBody' not in body:
        raise ValueError("gameReportBody is missing in request body")
    
    report = body['gameReportBody']
    
    # Validate required fields
    required_fields = [
        "playerID", 
        "gameID", 
        "gameMode", 
        "playerScore", 
        "leaderboardName",
        "fullRawGameReport"
    ]
    
    for field in required_fields:
        if field not in report:
            raise ValueError(f"Required field '{field}' is missing in gameReportBody")
    
    # Enhanced field validation
    if not isinstance(report['playerID'], str) or not report['playerID'].strip():
        raise ValueError("playerID must be a non-empty string")
    
    # Validate playerID format
    if not re.match(r'^[a-zA-Z0-9_-]+$', report['playerID']):
        raise ValueError("playerID contains invalid characters")
    
    if not isinstance(report['gameID'], str) or not report['gameID'].strip():
        raise ValueError("gameID must be a non-empty string")
    
    if not isinstance(report['gameMode'], str) or not report['gameMode'].strip():
        raise ValueError("gameMode must be a non-empty string")
    
    if not isinstance(report['leaderboardName'], str) or not report['leaderboardName'].strip():
        raise ValueError("leaderboardName must be a non-empty string")
    
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', report['leaderboardName']):
        raise ValueError("leaderboardName must start with a letter or number and contain only letters, numbers, hyphens, and underscores")
    
    # Store original score for later validation
    report['_originalPlayerScore'] = report['playerScore']
    
    # Validate fullRawGameReport
    if not isinstance(report['fullRawGameReport'], dict):
        raise ValueError("fullRawGameReport must be a JSON object")
    
    # Remove scoreStrategy from report if present (should come from leaderboard config)
    if 'scoreStrategy' in report:
        logger.warning("scoreStrategy in gameReport will be ignored - using leaderboard config value")
        del report['scoreStrategy']
    
    return report


# ============================================================================
# CONFIGURATION CACHE
# ============================================================================

def generate_config_cache_key(leaderboard_name: str) -> str:
    """Generate cache key for leaderboard configuration."""
    return f"config:{leaderboard_name}"


@tracer.capture_method
async def get_leaderboard_config_async(leaderboard_name: str) -> Dict[str, Any]:
    """
    Asynchronously retrieve leaderboard configuration with caching.
    """
    global config_cache
    current_time = time.time()
    
    # Check in-memory cache first
    if leaderboard_name in config_cache:
        config, timestamp = config_cache[leaderboard_name]
        if current_time - timestamp < CONFIG_CACHE_TTL:
            logger.debug(f"Retrieved config from memory cache: {leaderboard_name}")
            return config
        else:
            del config_cache[leaderboard_name]
    
    # Try Valkey cache
    try:
        client = await get_valkey_client()
        cache_key = generate_config_cache_key(leaderboard_name)
        
        cached_config = await client.get(cache_key)
        if cached_config:
            config = json.loads(cached_config.decode() if isinstance(cached_config, bytes) else cached_config)
            config_cache[leaderboard_name] = (config, current_time)
            logger.info(f"Retrieved leaderboard config from Valkey cache: {leaderboard_name}")
            return config
    except Exception as e:
        logger.warning(f"Failed to retrieve config from Valkey cache: {str(e)}")
    
    # Fallback to DynamoDB
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            get_thread_pool(),
            lambda: leaderboards_config_table.get_item(
                Key={'leaderboardName': leaderboard_name},
                ConsistentRead=True
            )
        )
        
        if 'Item' not in response:
            raise LeaderboardNotFoundError(f"Leaderboard with name '{leaderboard_name}' not found")
        
        config = response['Item']
        config_cache[leaderboard_name] = (config, current_time)
        
        # Cache in Valkey
        try:
            client = await get_valkey_client()
            cache_key = generate_config_cache_key(leaderboard_name)
            await client.set(
                cache_key, 
                json.dumps(config, default=decimal_serializer),
                expiry=ExpirySet(ExpiryType.SEC, CONFIG_CACHE_TTL)
            )
            logger.info(f"Cached leaderboard config in Valkey: {leaderboard_name}")
        except Exception as e:
            logger.warning(f"Failed to cache config in Valkey: {str(e)}")
        
        return config
        
    except ClientError as e:
        logger.error(f"Error retrieving leaderboard configuration: {str(e)}")
        raise


# ============================================================================
# GAME STATS STORAGE
# ============================================================================

@tracer.capture_method
async def store_game_stats_async(game_report: Dict[str, Any]) -> Tuple[str, str]:
    """
    Asynchronously store game stats in DynamoDB.
    """
    # Generate timestamp identifiers
    # Use float timestamp for sub-second precision, plus a short UUID suffix
    # to guarantee uniqueness even for concurrent Lambda invocations
    current_timestamp = int(time.time())
    current_datetime = datetime.fromtimestamp(current_timestamp, timezone.utc).isoformat()
    
    # Create sort key with millisecond precision to prevent overwrites
    # when multiple submissions arrive in the same second
    precise_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]  # millisecond precision
    sort_key = f"{game_report['gameID']}#{game_report['gameMode']}#{precise_time}+00:00"
    
    # Prepare the item for DynamoDB
    dynamodb_item = {
        'playerID': game_report['playerID'],
        'sortKey': sort_key,
        'gameID': game_report['gameID'],
        'gameMode': game_report['gameMode'],
        'playerScore': convert_floats_to_decimal(game_report['playerScore']),
        'leaderboardName': game_report['leaderboardName'],
        'fullRawGameReport': convert_floats_to_decimal(game_report['fullRawGameReport']),
        'timestamp': current_timestamp,
        'timestampISO': current_datetime,
        'scoreStrategy': 'best'  # Default, will be overridden by leaderboard config
    }
    
    # Add any additional fields
    for key, value in game_report.items():
        if key not in dynamodb_item and not key.startswith('_'):
            dynamodb_item[key] = convert_floats_to_decimal(value)
    
    # Store in DynamoDB
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        get_thread_pool(),
        lambda: game_stats_table.put_item(Item=dynamodb_item)
    )
    
    logger.info(f"Successfully stored game stats in DynamoDB: {sort_key}")
    
    return sort_key, current_datetime


# ============================================================================
# LEADERBOARD UPDATE
# ============================================================================

@tracer.capture_method
async def update_leaderboard_async(
    client: Union[GlideClusterClient, GlideClient],
    sorted_list_name: str,
    player_id: str,
    player_score: Decimal,
    leaderboard_type: str,
    score_strategy: str,
    ttl: Optional[int],
    leaderboard_config: Dict[str, Any]
) -> None:
    """
    Update leaderboard with the player's score.
    """
    # Remove any init placeholder when first real score is submitted
    await remove_init_placeholder_if_exists(client, sorted_list_name)
    
    # Get score type and precision for logging
    score_type = leaderboard_config.get('scoreType', 'score')
    time_precision = leaderboard_config.get('timePrecision', 3)
    
    player_score_float = float(player_score)
    
    if score_strategy == "replace":
        final_score = player_score_float
        if leaderboard_type == "ASCENDING_LB":
            # For ASCENDING_LB, store as negative for proper sorting
            # If score is already negative, make it positive first, then negative
            final_score = -abs(player_score_float)
        
        await client.zadd(sorted_list_name, {player_id: final_score})
        
        if score_type == 'time':
            logger.debug(f"Replacing time for {player_id}: {player_score_float:.{time_precision}f}s")
        
    elif score_strategy == "cumulative":
        if leaderboard_type == "ASCENDING_LB":
            # For ASCENDING_LB, cumulative means ADD times together (true cumulative)
            # Use case: total time across multiple sessions/races
            current_score = await client.zscore(sorted_list_name, player_id)
            if current_score is not None:
                current_actual = -float(current_score)
                new_total = current_actual + abs(player_score_float)  # Use abs to handle negative inputs
                await client.zadd(sorted_list_name, {player_id: -new_total})
            else:
                await client.zadd(sorted_list_name, {player_id: -abs(player_score_float)})
        else:
            # Use atomic increment for scores
            await client.zincrby(sorted_list_name, player_score_float, player_id)
            
    elif score_strategy == "best":
        # Get current score first
        current_score = await client.zscore(sorted_list_name, player_id)
        
        should_update = True
        final_score = player_score_float
        
        if current_score is not None:
            current_score = float(current_score)
            
            if leaderboard_type == "DESCENDING_LB":
                should_update = player_score_float > current_score
                final_score = max(current_score, player_score_float)
            else:  # ASCENDING_LB
                current_actual = -current_score
                player_abs = abs(player_score_float)  # Use absolute value for comparison
                should_update = player_abs < current_actual
                final_score = -min(current_actual, player_abs)
        elif leaderboard_type == "ASCENDING_LB":
            final_score = -abs(player_score_float)  # Use absolute value
        
        if should_update:
            await client.zadd(sorted_list_name, {player_id: final_score})
            if score_type == 'time':
                actual_score = -final_score if leaderboard_type == "ASCENDING_LB" else final_score
                logger.debug(f"Best time for {player_id}: {actual_score:.{time_precision}f}s")
    
    # Set TTL if specified
    if ttl:
        try:
            await client.expire(sorted_list_name, ttl)
        except Exception as e:
            logger.warning(f"Failed to set TTL: {str(e)}")


# ============================================================================
# MAIN PROCESSING
# ============================================================================

async def process_game_report(game_report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process the game report: validate, store stats, and update leaderboard.
    """
    # Get leaderboard configuration
    leaderboard_name = game_report['leaderboardName']
    logger.debug(f"=== PROCESSING DEBUG ===")
    logger.info(f"Leaderboard name: {leaderboard_name}")

    leaderboard_config = await get_leaderboard_config_async(leaderboard_name)

    logger.debug(f"Retrieved leaderboard config: {json.dumps(leaderboard_config, default=str)}")
    
    # Parse and validate score
    logger.info(f"Processing score: {game_report.get('_originalPlayerScore')}")
    
    validated_score = parse_and_validate_time_score(
        game_report['_originalPlayerScore'], 
        leaderboard_config
    )
    game_report['playerScore'] = validated_score
    
    logger.info(f"Score validation passed: {game_report['_originalPlayerScore']} -> {validated_score}")
    
    # Validate leaderboard type
    leaderboard_type = leaderboard_config.get('leaderboardType')
    if leaderboard_type not in VALID_LEADERBOARD_TYPES:
        raise ValueError(f"Invalid leaderboard type: {leaderboard_type}")
    
    # Validate score strategy
    score_strategy = leaderboard_config.get('scoreStrategy', 'best')
    if score_strategy not in VALID_SCORE_STRATEGIES:
        raise ValueError(f"Invalid score strategy: {score_strategy}. Must be one of {VALID_SCORE_STRATEGIES}")
    
    # Validate time format if scoreType is time
    if leaderboard_config.get('scoreType') == 'time':
        time_format = leaderboard_config.get('timeFormat', 'seconds')
        if time_format not in VALID_TIME_FORMATS:
            raise ValueError(f"Invalid time format: {time_format}. Must be one of {VALID_TIME_FORMATS}")
    
    # Validate gameID and gameMode match
    if game_report['gameID'] != leaderboard_config['gameID']:
        raise ValueError(f"gameID mismatch: {game_report['gameID']} != {leaderboard_config['gameID']}")
    
    if game_report['gameMode'] != leaderboard_config['gameMode']:
        raise ValueError(f"gameMode mismatch: {game_report['gameMode']} != {leaderboard_config['gameMode']}")
    
    # Check if leaderboard has expired
    if 'optionalLBExpiryDateTimeStamp' in leaderboard_config:
        is_expired, is_readonly_on_expiry = is_leaderboard_expired_and_readonly(leaderboard_config)
        if is_expired:
            expiry_time = leaderboard_config['optionalLBExpiryDateTimeStamp']
            if is_readonly_on_expiry:
                # Read-only preservation: leaderboard is preserved but no writes allowed
                raise LeaderboardExpiredError(
                    f"Leaderboard '{leaderboard_name}' has expired and is in read-only mode. "
                    f"Expired at: {expiry_time}. "
                    f"Status: read-only. "
                    f"Reason: Leaderboard has expired and is preserved for viewing only - no new scores can be added."
                )
            else:
                # Auto-deletion: leaderboard has expired and should be deleted
                raise LeaderboardExpiredError(
                    f"Leaderboard '{leaderboard_name}' has expired and is no longer accepting writes. "
                    f"Expired at: {expiry_time}. "
                    f"Status: expired. "
                    f"Reason: Leaderboard has expired and is scheduled for deletion - no new scores can be added."
                )
    
    # Store game stats in DynamoDB
    logger.info("=== STARTING DYNAMODB WRITE ===")
    sort_key, timestamp_iso = await store_game_stats_async(game_report)
    
    logger.info("=== DYNAMODB WRITE SUCCESSFUL - PROCEEDING TO MEMORYDB ===")
    
    # Get Valkey client
    client = await get_valkey_client()
    
    # Update leaderboard
    logger.info("=== STARTING MEMORYDB WRITE ===")
    # Only set TTL on the sorted set if the leaderboard is NOT read-only on expiry.
    # Read-only leaderboards must preserve their data in MemoryDB for continued reads
    # after expiry. Auto-delete leaderboards get TTL so MemoryDB cleans them up.
    read_only_on_expiry = leaderboard_config.get('optionalLBReadOnlyOnExpiry', True)
    memorydb_ttl = None if read_only_on_expiry else calculate_ttl_from_expiry(
        leaderboard_config.get('optionalLBExpiryDateTimeStamp')
    )
    await update_leaderboard_async(
        client,
        leaderboard_config['sortedListName'],
        game_report['playerID'],
        game_report['playerScore'],
        leaderboard_type,
        score_strategy,
        memorydb_ttl,
        leaderboard_config
    )
    
    logger.info("=== MEMORYDB WRITE SUCCESSFUL ===")
    
    # Prepare response metadata
    response_metadata = {
        'sortKey': sort_key,
        'timestamp': timestamp_iso,
        'leaderboardType': leaderboard_type,
        'scoreStrategy': score_strategy,
        'sortedListName': leaderboard_config['sortedListName']
    }
    
    # Add time-specific information if applicable
    if leaderboard_config.get('scoreType') == 'time':
        time_precision = leaderboard_config.get('timePrecision', 3)
        response_metadata.update({
            'timeFormatted': f"{float(game_report['playerScore']):.{time_precision}f}s",
            'scoreType': 'time',
            'timePrecision': time_precision,
            'timeFormat': leaderboard_config.get('timeFormat', 'seconds')
        })
    
    logger.info("=== PROCESSING COMPLETE ===")
    
    return response_metadata


# ============================================================================
# MAIN LAMBDA HANDLER
# ============================================================================

@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
@handle_errors
@log_execution
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    High-performance Lambda handler for storing individual player game stats and updating leaderboards.
    """
    # ENTRY LOGGING
    logger.info("=== LAMBDA HANDLER ENTRY ===")
    logger.info(f"Function: {context.function_name}")
    logger.info(f"Request ID: {context.aws_request_id}")
    
    start_time = time.perf_counter()
    
    # Check if Valkey is available
    if not VALKEY_AVAILABLE:
        logger.error("Valkey-GLIDE is not available - cannot process requests")
        return {
            'statusCode': 503,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                'Access-Control-Allow-Methods': 'POST,OPTIONS'
            },
            'body': json.dumps({
                'gameReportResponse': {
                    'success': False,
                    'error': 'Service temporarily unavailable - Valkey dependency not found',
                    'message': 'The leaderboard service is currently unavailable due to a dependency issue'
                }
            })
        }
    
    # Validate authentication
    auth_context = validate_authenticated_context(event, 'write')
    logger.info(f"Stats submission for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
    
    # Validate AWS resources
    validate_aws_resources()
    
    # Validate request
    game_report = validate_request(event)

    # =========================================================================
    # ANTI-SPOOFING: a player may only submit scores under THEIR OWN identity.
    # Compare the playerID in the request against the authenticated player's id
    # (auth_context['playerId'], set by your Lambda authorizer from the validated
    # token). A mismatch means the caller is trying to write a score as someone
    # else — a potential abuse condition — so we reject with HTTP 403 and log it.
    #
    # Enforce-when-present: if your authorizer does not set 'playerId' (it is
    # documented as recommended, not mandatory), this check is skipped so the
    # deployment keeps working. To make player identity MANDATORY, change the
    # condition to `if authenticated_player_id != game_report['playerID']:` and
    # have the authorizer always populate 'playerId'.
    # =========================================================================
    authenticated_player_id = auth_context.get('playerId', '')
    if authenticated_player_id and authenticated_player_id != game_report['playerID']:
        # WARNING (not error) so it is easy to alarm on as a potential-abuse
        # signal in CloudWatch via the stable PLAYER_ID_MISMATCH marker. We log
        # the authenticated id and the attempted id, plus context for triage; we
        # do NOT log tokens or full payloads.
        logger.warning(
            "PLAYER_ID_MISMATCH: authenticated player '%s' attempted to submit as '%s' "
            "(leaderboard=%s, studio=%s, game=%s, requestId=%s)",
            authenticated_player_id, game_report['playerID'],
            game_report.get('leaderboardName'), auth_context['studioId'],
            auth_context['gameId'], context.aws_request_id,
        )
        raise PlayerIdentityMismatchError(
            "playerID does not match the authenticated player"
        )

    # Execute async processing
    response_metadata = run_async(process_game_report(game_report))
    
    # Calculate processing time
    total_time = time.perf_counter() - start_time
    
    # Build response
    response_data = {
        'message': 'Game stats stored and leaderboard updated successfully',
        'leaderboardName': game_report['leaderboardName'],
        'playerID': game_report['playerID'],
        'playerScore': float(game_report['playerScore']),
        'metadata': {
            'studioId': auth_context['studioId'],
            'gameId': auth_context['gameId'],
            'requestId': context.aws_request_id,
            'timestamp': datetime.now(timezone.utc).isoformat()
        },
        'performance': {
            'processingTimeMs': int(total_time * 1000)
        },
        'success': True
    }
    
    # Add response metadata
    response_data.update(response_metadata)
    
    # COMPLETION LOGGING
    logger.info(f"=== LAMBDA HANDLER COMPLETION ===")
    logger.info(f"Processing time: {int(total_time * 1000)}ms")
    
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Cache-Control': 'no-cache'
        },
        'body': json.dumps({'gameReportResponse': response_data}, default=decimal_serializer)
    }


# ============================================================================
# CLEANUP
# ============================================================================

async def cleanup_resources():
    """
    Comprehensive cleanup function for proper resource management.
    """
    global thread_pool, valkey_client, config_cache
    
    cleanup_tasks = []
    
    if valkey_client:
        try:
            cleanup_tasks.append(valkey_client.close())
        except Exception as e:
            logger.warning(f"Error closing Valkey client: {str(e)}")
    
    if thread_pool:
        try:
            thread_pool.shutdown(wait=False)
            thread_pool = None
        except Exception as e:
            logger.warning(f"Error shutting down thread pool: {str(e)}")
    
    if cleanup_tasks:
        try:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            logger.info("Successfully cleaned up all resources")
        except Exception as e:
            logger.warning(f"Error during cleanup: {str(e)}")
    
    valkey_client = None
    config_cache.clear()


# Register cleanup for Lambda container lifecycle
import atexit
atexit.register(lambda: run_async(cleanup_resources()))