# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
getPlayerLBStanding.py -- Player Leaderboard Standing Retrieval Lambda Function

Retrieve a player's standing and score in a leaderboard with high-performance
implementation optimized for Python 3.13 and Valkey-GLIDE 2.4.1.

Features:
- Player standing and score retrieval
- Percentile calculation
- Neighbour players information
- Multi-level caching (memory + Valkey)
- Batch operations for optimal performance
- Comprehensive error handling

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
        ReadFrom,
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
MEMORYDB_CLUSTER_NAME = os.environ.get('gameLeaderboardsMemoryDBName')
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()

# Valkey configuration
VALKEY_USE_TLS = os.environ.get('VALKEY_USE_TLS', 'true').lower() == 'true'
VALKEY_CLUSTER_MODE = os.environ.get('VALKEY_CLUSTER_MODE', 'true').lower() == 'true'

# Performance optimization settings
MAX_CONCURRENT_OPERATIONS = int(os.environ.get('MAX_CONCURRENT_OPERATIONS', '10'))
CONNECTION_TIMEOUT = int(os.environ.get('GLIDE_CONNECTION_TIMEOUT_MS', '2000'))  # Optimized: 2000ms
REQUEST_TIMEOUT = int(os.environ.get('GLIDE_REQUEST_TIMEOUT_MS', '5000'))        # Increased for failover resilience (was 2500ms)

# Player standing specific settings
CONFIG_CACHE_TTL = int(os.environ.get('CONFIG_CACHE_TTL', '300'))  # 5 minutes
MAX_NEIGHBOURS_COUNT = int(os.environ.get('MAX_NEIGHBOURS_COUNT', '50'))
INCLUDE_PERCENTILE_DEFAULT = os.environ.get('INCLUDE_PERCENTILE_DEFAULT', 'false').lower() == 'true'
INCLUDE_NEIGHBOURS_DEFAULT = os.environ.get('INCLUDE_NEIGHBOURS_DEFAULT', 'false').lower() == 'true'

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
logger.info(f"MemoryDB cluster: {MEMORYDB_CLUSTER_NAME}")

# Initialize DynamoDB tables
leaderboards_config_table = dynamodb.Table(LEADERBOARDS_CONFIG_TABLE_NAME) if LEADERBOARDS_CONFIG_TABLE_NAME else None

# Constants
VALID_LEADERBOARD_TYPES = ["DESCENDING_LB", "ASCENDING_LB"]
VALID_SCORE_TYPES = ["score", "time", "distance", "points", "rank", "level"]

# Score type-specific initialization configuration (for dummy item detection)
INIT_CONFIG = {
    "score": {"key": "_init_topscore_", "value": -999999.0},
    "time": {"key": "_init_time_", "value": 999999999.0},  # Very high time (999,999 seconds ≈ 11.5 days)
    "distance": {"key": "_init_distance_", "value": -1.0},
    "points": {"key": "_init_points_", "value": -999999.0},
    "rank": {"key": "_init_rank_", "value": 999999999.0},  # Very high rank (worst possible)
    "level": {"key": "_init_level_", "value": -1.0}
}

# Global resources
thread_pool: Optional[ThreadPoolExecutor] = None
valkey_client: Optional[Union[GlideClusterClient, GlideClient]] = None
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


class PlayerNotFoundError(Exception):
    """Custom exception for player not found in leaderboard"""
    pass


def handle_errors(func):
    """
    Enhanced error handler with specific error types and recovery strategies.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except PlayerNotFoundError as e:
            logger.error(f"Player not found error: {str(e)}")
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'playerLBStandingResponse': {
                    'error': 'Player Not Found',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except LeaderboardNotFoundError as e:
            logger.error(f"Leaderboard not found error: {str(e)}")
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'playerLBStandingResponse': {
                    'error': 'Leaderboard Not Found',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'playerLBStandingResponse': {
                    'error': 'Bad Request',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except LeaderboardAuthenticationError as e:
            logger.error(f"Authentication error: {str(e)}")
            return {
                'statusCode': 401,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'playerLBStandingResponse': {
                    'error': 'Unauthorized',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
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
                'body': json.dumps({'playerLBStandingResponse': {
                    'error': error_code,
                    'message': error_message,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except ConnectionError as e:
            logger.error(f"Valkey connection error: {str(e)}")
            global valkey_client
            valkey_client = None
            return {
                'statusCode': 503,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'playerLBStandingResponse': {
                    'error': 'Service Unavailable',
                    'message': 'Database connection error',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
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
                'body': json.dumps({'playerLBStandingResponse': {
                    'error': 'Internal Server Error',
                    'message': 'An unexpected error occurred',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
    
    return wrapper


# ============================================================================
# AUTHENTICATION AND VALIDATION
# ============================================================================

@tracer.capture_method
def validate_authenticated_context(event: Dict[str, Any], required_permission: str = 'read') -> Dict[str, str]:
    """
    Validate player authentication from API Gateway authorizer context.
    
    INTEGRATION POINT: The player authorizer (auth/playerAuthorizer.py) must be
    modified to validate your game's player credentials (e.g., JWT, OAuth, session
    tokens, platform tokens) and populate the authorizer context with the fields
    below. This function reads that context and enforces permission checks.
    
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
    Enhanced with better error handling and validation.
    """
    credentials_config = {}
    
    # Get endpoint from environment variable
    endpoint = os.environ.get('MEMORYDB_CLUSTER_ENDPOINT')
    if not endpoint:
        # Try alternative environment variable names
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
        # Try alternative environment variable names
        secret_arn = os.environ.get('VALKEY_SECRET_ARN') or os.environ.get('MEMORYDB_CREDENTIALS_SECRET')
        
    if not secret_arn:
        raise ValueError("Required environment variable MEMORYDB_SECRET_ARN is not set")
    
    try:
        logger.info(f"Retrieving secret from: {secret_arn}")
        response = secretsmanager.get_secret_value(SecretId=secret_arn)
        
        # Handle both SecretString and SecretBinary
        if 'SecretString' in response:
            secret_data = json.loads(response['SecretString'])
        elif 'SecretBinary' in response:
            import base64
            secret_data = json.loads(base64.b64decode(response['SecretBinary']))
        else:
            raise ValueError("Secret does not contain SecretString or SecretBinary")
        
        # Try multiple possible key names for username
        username = (
            secret_data.get('username') or 
            secret_data.get('Username') or 
            secret_data.get('user') or 
            secret_data.get('User') or
            'default'  # Fallback to 'default' user
        )
        
        # Try multiple possible key names for password
        password = (
            secret_data.get('password') or 
            secret_data.get('Password') or 
            secret_data.get('pass') or 
            secret_data.get('Pass')
        )
        
        if not password:
            raise ValueError("Password not found in secret. Checked keys: password, Password, pass, Pass")
        
        # Ensure password is a clean string without any extra characters
        password = str(password).strip()
        
        # Validate the password doesn't have unexpected characters
        if '\n' in password or '\r' in password:
            logger.warning("Password contains newline characters, removing them")
            password = password.replace('\n', '').replace('\r', '')

        credentials_config['username'] = username
        credentials_config['password'] = password
        # Also get TLS setting from secret if available
        credentials_config['tls'] = secret_data.get('tls', True)

        logger.info(f"Successfully retrieved credentials for user: {username}")
        logger.info(f"Password characteristics: length={len(password)}, starts_with='{password[:3]}...', ends_with='...{password[-3:]}'")
        
        # Validate credentials format
        if not username or not password:
            raise ValueError(f"Invalid credentials retrieved - Username: {'present' if username else 'missing'}, Password: {'present' if password else 'missing'}")
        
        logger.info(f"Successfully retrieved Valkey configuration - Endpoint: {endpoint}, Port: {credentials_config['port']}, Username: {username}, TLS: {credentials_config['tls']}")

        return credentials_config
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ResourceNotFoundException':
            logger.error(f"Secret not found: {secret_arn}")
            logger.error("Ensure the secret exists and the Lambda has permission to access it")
        elif error_code == 'AccessDeniedException':
            logger.error(f"Access denied to secret: {secret_arn}")
            logger.error("Check IAM permissions for the Lambda execution role")
        else:
            logger.error(f"AWS Secrets Manager error: {error_code} - {e.response['Error']['Message']}")
        raise ValueError(f"Failed to retrieve Valkey credentials: {str(e)}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse secret JSON: {str(e)}")
        logger.error("Ensure the secret contains valid JSON with 'username' and 'password' fields")
        raise ValueError(f"Invalid secret format: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error retrieving credentials: {str(e)}")
        raise ValueError(f"Failed to retrieve Valkey credentials: {str(e)}")


async def get_valkey_client() -> Union[GlideClusterClient, GlideClient]:
    """
    Get or create a high-performance Valkey client using GLIDE 2.4.1+ internal connection pooling.
    Reuses existing client within Lambda execution context for optimal performance.
    """
    global valkey_client
    
    # Reuse existing client if available and connected
    if valkey_client:
        try:
            # Test connection with a quick ping - more robust check
            ping_result = await valkey_client.ping()
            if ping_result == b"PONG":
                # Additional connection stability test
                await valkey_client.exists(["connection_test_key"])
                logger.debug("Reusing existing Valkey client connection")
                return valkey_client
            else:
                logger.warning(f"Client ping returned unexpected result: {ping_result.decode() if isinstance(ping_result, bytes) else ping_result}, creating new client")
                valkey_client = None
        except Exception as e:
            logger.warning(f"Existing client connection failed, creating new client: {str(e)}")
            valkey_client = None
    
    try:
        # Get configuration from environment and Secrets Manager
        valkey_config = get_valkey_credentials_and_config()
        VALKEY_CLUSTER_ENDPOINT = valkey_config['endpoint']
        VALKEY_PORT = valkey_config['port']
        VALKEY_USERNAME = valkey_config['username']
        VALKEY_PASSWORD = valkey_config['password']
        VALKEY_USE_TLS = valkey_config.get('tls', True)

        # Validate credentials
        if not VALKEY_USERNAME or not VALKEY_PASSWORD:
            raise ValueError(f"Invalid credentials - Username: {'present' if VALKEY_USERNAME else 'missing'}, Password: {'present' if VALKEY_PASSWORD else 'missing'}")

        logger.info(f"Initializing Valkey connection - Endpoint: {VALKEY_CLUSTER_ENDPOINT}:{VALKEY_PORT}, Username: {VALKEY_USERNAME}, TLS: {VALKEY_USE_TLS}, ClusterMode: {VALKEY_CLUSTER_MODE}")
        
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
            # For MemoryDB cluster mode with optimized settings
            addresses = [NodeAddress(VALKEY_CLUSTER_ENDPOINT, VALKEY_PORT)]
            
            # Create cluster configuration with only valid GLIDE parameters
            config = ClusterClientConfiguration(
                addresses=addresses,
                use_tls=VALKEY_USE_TLS,
                credentials=credentials,
                request_timeout=REQUEST_TIMEOUT,
                client_name="player-standing-lambda",
                protocol=ProtocolVersion.RESP3,
                read_from=ReadFrom.PREFER_REPLICA
            )
            
            logger.info(f"Creating GLIDE cluster client with optimized settings...")            
            valkey_client = await GlideClusterClient.create(config)
            logger.info(f"Successfully connected to Valkey cluster")
            
        else:
            # For standalone mode
            address = NodeAddress(VALKEY_CLUSTER_ENDPOINT, VALKEY_PORT)
            
            config = BaseClientConfiguration(
                addresses=[address],
                use_tls=VALKEY_USE_TLS,
                credentials=credentials,
                request_timeout=REQUEST_TIMEOUT,
                client_name="player-standing-lambda",
                protocol=ProtocolVersion.RESP3
            )

            logger.info("Creating GLIDE standalone client with optimized settings...")
            valkey_client = await GlideClient.create(config)
            logger.info("Successfully connected to Valkey standalone")
                
        return valkey_client
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to connect to Valkey: {error_msg}")
        
        # Provide specific troubleshooting based on error
        if "WRONGPASS" in error_msg or "invalid username-password" in error_msg:
            troubleshooting = (
                f"Authentication failed for user '{VALKEY_USERNAME}'. "
                f"Troubleshooting: 1) Verify ACL user exists and is enabled in MemoryDB, "
                f"2) Confirm password in Secrets Manager matches ACL password exactly, "
                f"3) Check user has required permissions (+@all or specific commands), "
                f"4) Secret ARN: {os.environ.get('MEMORYDB_SECRET_ARN', 'NOT SET')}"
            )
            logger.error(troubleshooting)
        elif "Connection refused" in error_msg or "timeout" in error_msg.lower():
            troubleshooting = (
                f"Connection failed to {VALKEY_CLUSTER_ENDPOINT}:{VALKEY_PORT}. "
                f"Troubleshooting: 1) Verify security group allows inbound on port {VALKEY_PORT}, "
                f"2) Check Lambda is in correct VPC/subnets, "
                f"3) Verify MemoryDB cluster endpoint is correct, "
                f"4) Ensure cluster is in AVAILABLE state"
            )
            logger.error(troubleshooting)
        elif "TLS" in error_msg or "SSL" in error_msg:
            troubleshooting = (
                f"TLS/SSL error connecting to MemoryDB. "
                f"Current TLS setting: {VALKEY_USE_TLS}. "
                f"MemoryDB requires TLS=true. Verify the cluster has TLS enabled."
            )
            logger.error(troubleshooting)
        else:
            logger.error(f"Failed to connect to Valkey: {error_msg}")
        
        raise ConnectionError(f"Unable to connect to Valkey: {error_msg}")


def get_thread_pool() -> ThreadPoolExecutor:
    """
    Get or create a thread pool for concurrent operations.
    """
    global thread_pool
    if thread_pool is None:
        thread_pool = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_OPERATIONS,
            thread_name_prefix="player-standing-worker"
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


def format_score_for_display(score: float, leaderboard_config: Dict[str, Any]) -> Union[float, str]:
    """Format score based on leaderboard configuration."""
    score_type = leaderboard_config.get('scoreType', 'score')
    leaderboard_type = leaderboard_config.get('leaderboardType', 'DESCENDING_LB')
    
    # Convert negative scores back to positive for ASCENDING_LB leaderboards
    # (scores are stored as negative in MemoryDB for proper sorting)
    if leaderboard_type == "ASCENDING_LB" and score < 0:
        score = -score
    
    if score_type != 'time':
        return score
    
    time_format = leaderboard_config.get('timeFormat', 'seconds')
    time_precision = int(leaderboard_config.get('timePrecision', 3))
    
    if time_format == 'seconds':
        return round(score, time_precision)
    elif time_format == 'milliseconds':
        return round(score * 1000, time_precision)
    elif time_format == 'minutes_seconds':
        minutes = int(score // 60)
        seconds = score % 60
        return f"{minutes}:{seconds:0{time_precision+3}.{time_precision}f}"
    elif time_format == 'hours_minutes_seconds':
        hours = int(score // 3600)
        minutes = int((score % 3600) // 60)
        seconds = score % 60
        return f"{hours}:{minutes:02d}:{seconds:0{time_precision+3}.{time_precision}f}"
    
    return score


def generate_sorted_list_name(game_id: str, game_mode: str, leaderboard_name: str) -> str:
    """
    Generate a consistent sorted list name for leaderboards.
    """
    return f"{game_id}:{game_mode}:{leaderboard_name}"


def generate_config_cache_key(leaderboard_name: str) -> str:
    """
    Generate cache key for leaderboard configuration.
    """
    return f"config:{leaderboard_name}"


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
        
        try:
            valkey_config = get_valkey_credentials_and_config()
            logger.info("Successfully validated Valkey configuration from Secrets Manager")
        except Exception as e:
            raise ValueError(f"Failed to retrieve Valkey configuration from Secrets Manager: {str(e)}")
        
        try:
            config_table_desc = leaderboards_config_table.meta.client.describe_table(
                TableName=LEADERBOARDS_CONFIG_TABLE_NAME
            )
            
            if config_table_desc['Table']['TableStatus'] != 'ACTIVE':
                raise ValueError(f"Leaderboards config table is not active: {config_table_desc['Table']['TableStatus']}")
            
            logger.info("DynamoDB table validated successfully")
            
        except ClientError as e:
            logger.error(f"Error validating DynamoDB table: {str(e)}")
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


def is_dummy_entry(member: str, score: float, score_type: str = "score") -> bool:
    """
    Check if an entry is a dummy initialization entry that should be filtered out.
    
    Args:
        member: The member name/key
        score: The score value
        score_type: The type of score (score, time, distance, etc.)
        
    Returns:
        True if this is a dummy entry that should be filtered out
    """
    init_config = INIT_CONFIG.get(score_type, INIT_CONFIG["score"])
    
    # Check if this matches the initialization pattern
    if member == init_config["key"] and abs(float(score) - init_config["value"]) < 0.001:
        return True
    
    # Also check for legacy _init_ entries
    if member.startswith("_init") and member.endswith("_"):
        return True
    
    return False


# ============================================================================
# REQUEST VALIDATION
# ============================================================================

@tracer.capture_method
def validate_player_standing_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enhanced request validation with comprehensive security checks.
    """
    if 'body' not in event or event['body'] is None:
        raise ValueError("Request body is missing")
    
    try:
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in request body")
    
    if 'playerLBStandingRequest' not in body:
        raise ValueError("playerLBStandingRequest is missing in request body")
    
    request_params = body['playerLBStandingRequest']
    
    # Validate required fields
    required_fields = ["playerID", "leaderboardName"]
    
    for field in required_fields:
        if field not in request_params:
            raise ValueError(f"Required field '{field}' is missing in playerLBStandingRequest")
    
    # Enhanced field validation with security checks
    player_id = request_params['playerID']
    if not isinstance(player_id, str) or not player_id.strip():
        raise ValueError("playerID must be a non-empty string")
    
    # Security: Validate playerID format (allow alphanumeric, underscore, hyphen)
    player_id = player_id.strip()
    if len(player_id) > 255:
        raise ValueError("playerID must be 255 characters or less")
    
    if not re.match(r'^[a-zA-Z0-9_-]+$', player_id):
        raise ValueError("playerID contains invalid characters. Only letters, numbers, underscores, and hyphens are allowed")
    
    request_params['playerID'] = player_id
    
    leaderboard_name = request_params['leaderboardName']
    if not isinstance(leaderboard_name, str) or not leaderboard_name.strip():
        raise ValueError("leaderboardName must be a non-empty string")
    
    # Security: Validate leaderboard name format
    leaderboard_name = leaderboard_name.strip()
    if len(leaderboard_name) > 255:
        raise ValueError("leaderboardName must be 255 characters or less")
    
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', leaderboard_name):
        raise ValueError("leaderboardName must start with a letter or number and contain only letters, numbers, hyphens, and underscores")
    
    request_params['leaderboardName'] = leaderboard_name
    
    # Validate optional boolean fields
    request_params['includePercentile'] = request_params.get('includePercentile', INCLUDE_PERCENTILE_DEFAULT)
    if not isinstance(request_params['includePercentile'], bool):
        raise ValueError("includePercentile must be a boolean")
    
    # Support both spellings for neighbours (American: includeNeighbors, British: includeNeighbours)
    include_neighbours = request_params.get('includeNeighbours', request_params.get('includeNeighbors', INCLUDE_NEIGHBOURS_DEFAULT))
    if not isinstance(include_neighbours, bool):
        raise ValueError("includeNeighbours must be a boolean")
    request_params['includeNeighbours'] = include_neighbours
    
    # Support both spellings for neighbours count (American: neighborsCount, British: neighboursCount)
    # Only validate neighboursCount if includeNeighbours is true
    if include_neighbours:
        neighbours_count = request_params.get('neighboursCount', request_params.get('neighborsCount', 3))
        try:
            neighbours_count = int(neighbours_count)
            if neighbours_count <= 0:
                raise ValueError("neighboursCount must be a positive integer")
            if neighbours_count > MAX_NEIGHBOURS_COUNT:
                raise ValueError(f"neighboursCount cannot exceed {MAX_NEIGHBOURS_COUNT}. Maximum total players returned will be {(MAX_NEIGHBOURS_COUNT * 2) + 1} ({MAX_NEIGHBOURS_COUNT} before + target player + {MAX_NEIGHBOURS_COUNT} after)")
            request_params['neighboursCount'] = neighbours_count
        except (ValueError, TypeError) as e:
            if "neighboursCount cannot exceed" in str(e):
                raise e
            raise ValueError("neighboursCount must be a valid positive integer")
    else:
        # Set default even when not used to maintain consistency
        request_params['neighboursCount'] = 3
    
    return request_params


# ============================================================================
# LEADERBOARD CONFIGURATION RETRIEVAL
# ============================================================================

@tracer.capture_method
async def get_leaderboard_config_cached(leaderboard_name: str) -> Dict[str, Any]:
    """
    Get leaderboard configuration with multi-level caching (memory + Valkey + DynamoDB).
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
            # Remove expired entry
            del config_cache[leaderboard_name]
    
    # Try Valkey cache
    try:
        client = await get_valkey_client()
        cache_key = generate_config_cache_key(leaderboard_name)
        cached_config = await client.get(cache_key)
        
        if cached_config:
            if isinstance(cached_config, bytes):
                cached_config = cached_config.decode('utf-8')
            
            config = json.loads(cached_config)
            # Store in memory cache
            config_cache[leaderboard_name] = (config, current_time)
            logger.debug(f"Retrieved config from Valkey cache: {leaderboard_name}")
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
            raise LeaderboardNotFoundError(f"Leaderboard '{leaderboard_name}' not found")
        
        config = response['Item']
        
        # Cache in both Valkey and memory
        try:
            client = await get_valkey_client()
            cache_key = generate_config_cache_key(leaderboard_name)
            await client.set(cache_key, json.dumps(config, default=decimal_serializer), expiry=ExpirySet(ExpiryType.SEC, CONFIG_CACHE_TTL))
        except Exception as e:
            logger.warning(f"Failed to cache config in Valkey: {str(e)}")
        
        config_cache[leaderboard_name] = (config, current_time)
        logger.info(f"Retrieved and cached config from DynamoDB: {leaderboard_name}")
        
        return config
        
    except ClientError as e:
        logger.error(f"Error retrieving leaderboard configuration: {str(e)}")
        raise


# ============================================================================
# PLAYER STANDING OPERATIONS
# ============================================================================

# ============================================================================
# MAIN HANDLER LOGIC
# ============================================================================

async def handle_player_standing_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle player leaderboard standing request operation with original business logic.
    """
    logger.info("=== HANDLE_PLAYER_STANDING_REQUEST ENTRY ===")
    start_time = time.perf_counter()
    
    try:
        # Validate request
        request_params = validate_player_standing_request(event)
        
        player_id = request_params['playerID']
        leaderboard_name = request_params['leaderboardName']
        include_percentile = request_params['includePercentile']
        include_neighbours = request_params['includeNeighbours']
        neighbours_count = request_params['neighboursCount']
        
        logger.info(f"Processing player standing request: Player={player_id}, Leaderboard={leaderboard_name}")
        
        # Get leaderboard configuration with caching
        leaderboard_config = await get_leaderboard_config_cached(leaderboard_name)
        
        # Validate leaderboard type
        leaderboard_type = leaderboard_config.get('leaderboardType')
        if leaderboard_type not in VALID_LEADERBOARD_TYPES:
            raise ValueError(f"Invalid leaderboard type: {leaderboard_type}")
        
        # Get score type for proper dummy entry handling
        score_type = leaderboard_config.get('scoreType', 'score')
        
        # Get Valkey client
        client = await get_valkey_client()
        
        # Get sorted list name
        sorted_list_name = leaderboard_config['sortedListName']
        
        # Generate leaderboard key for existence check
        # leaderboard_key = generate_leaderboard_key(sorted_list_name)  # TEMPORARILY COMMENTED OUT
        
        # Verify the sorted list exists
        exists = await client.exists([sorted_list_name])  # Use sorted_list_name directly
        if exists == 0:
            raise LeaderboardNotFoundError(f"Leaderboard data not found for '{leaderboard_name}'")
        
        # Get player standing information
        player_standing_info = await get_player_standing_optimized(
            client,
            sorted_list_name,  # Pass sorted_list_name, function will generate leaderboard_key
            leaderboard_type,
            score_type,
            player_id,
            include_percentile,
            include_neighbours,
            neighbours_count,
            leaderboard_config
        )
        
        processing_time = time.perf_counter() - start_time

        response_data = {
            'playerLBStandingResponse': {
                'leaderboardName': leaderboard_name,
                'leaderboardType': leaderboard_type,
                'playerLBStandingInfo': player_standing_info,
                'metadata': {
                    'processingTimeMs': int(processing_time * 1000),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                },
                'success': True
            }
        }
        
        logger.info(f"=== HANDLE_PLAYER_STANDING_REQUEST EXIT === Processing time: {int(processing_time * 1000)}ms")
        return response_data

    except Exception as e:
        processing_time = time.perf_counter() - start_time
        logger.error(f"=== HANDLE_PLAYER_STANDING_REQUEST ERROR EXIT === Processing time: {int(processing_time * 1000)}ms, Error: {str(e)}")
        raise

def generate_leaderboard_key(sorted_list_name: str) -> str:
    """
    Generate consistent key for leaderboard data
    """
    return f"leaderboard:{sorted_list_name}"

@tracer.capture_method
async def get_player_standing_optimized(
    client: Union[GlideClusterClient, GlideClient],
    sorted_list_name: str,
    leaderboard_type: str,
    score_type: str,
    player_id: str,
    include_percentile: bool,
    include_neighbours: bool,
    neighbours_count: int,
    leaderboard_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Optimized player standing retrieval with exact business logic from original.
    """
    logger.info(f"Getting player standing for {player_id} in {sorted_list_name}")
    
    # IMPORTANT: Use the leaderboard key wrapper
    # leaderboard_key = generate_leaderboard_key(sorted_list_name)  # TEMPORARILY COMMENTED OUT
    
    # Batch operation to get all player info at once
    async def get_player_info_batch():
        # Use zrevrank for both leaderboard types to match storage strategy
        # ASCENDING_LB: scores stored as negative, zrevrank gives correct position
        # DESCENDING_LB: scores stored as positive, zrevrank gives correct position
        tasks = [
            client.zscore(sorted_list_name, player_id),  # Use sorted_list_name directly
            client.zrevrank(sorted_list_name, player_id)  # Use sorted_list_name directly
        ]
        
        # Add total count if needed for percentile
        if include_percentile:
            tasks.append(client.zcard(sorted_list_name))  # Use sorted_list_name directly
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        player_score = results[0] if not isinstance(results[0], Exception) else None
        player_rank = results[1] if not isinstance(results[1], Exception) else None
        total_players = results[2] if len(results) > 2 and not isinstance(results[2], Exception) else None
        
        return player_score, player_rank, total_players
    
    # Get player information
    player_score, player_rank, total_players = await get_player_info_batch()
    
    # Check if player exists
    if player_score is None or player_rank is None:
        raise PlayerNotFoundError(f"Player '{player_id}' not found in leaderboard")
    
    # Convert to 1-based rank
    player_rank += 1
    
    # Adjust score based on leaderboard type
    if leaderboard_type == "ASCENDING_LB":
        player_score = -float(player_score)
    else:
        player_score = float(player_score)
    
    # Prepare base result
    result = {
        'playerID': player_id,
        'rank': player_rank,
        'score': format_score_for_display(player_score, leaderboard_config)
    }
    
    # Add percentile information if requested
    if include_percentile and total_players is not None:
        # Account for the single init placeholder in total count.
        # By design, each leaderboard has at most one placeholder entry (added at creation
        # to prevent MemoryDB auto-deletion of empty sorted sets). It is removed when the
        # first real score is written, but we check here as a safety net for empty leaderboards.
        actual_total = total_players

        try:
            init_config = INIT_CONFIG.get(score_type, INIT_CONFIG["score"])
            placeholder_score = await client.zscore(sorted_list_name, init_config["key"])
            if placeholder_score is not None:
                actual_total = max(1, total_players - 1)
            else:
                actual_total = max(1, total_players)
        except Exception as e:
            logger.warning(f"Could not check for placeholder entry: {str(e)}")
            actual_total = max(1, total_players)

        percentile = 100 - ((player_rank - 1) / actual_total * 100) if actual_total > 0 else 100
        result['percentile'] = round(max(0, min(100, percentile)), 2)
        result['totalPlayers'] = actual_total
    
    # Add neighbours if requested
    if include_neighbours:
        logger.info(f"Processing neighbours: count={neighbours_count}, player_rank={player_rank}")
        
        # Calculate range for neighbours
        start_rank = max(0, player_rank - neighbours_count - 1)
        end_rank = player_rank + neighbours_count - 1
        
        logger.info(f"neighbours range: start_rank={start_rank}, end_rank={end_rank}")
        
        # Get neighbours data using sorted_list_name
        try:
            neighbour_results = await client.zrange_withscores(
                sorted_list_name,  # Use sorted_list_name directly
                RangeByIndex(start_rank, end_rank),
                reverse=True  # Use reverse parameter for descending order
            )
            logger.info(f"Neighbor results type: {type(neighbour_results)}, length: {len(neighbour_results) if neighbour_results else 0}")
        except Exception as e:
            logger.error(f"Error getting neighbours: {str(e)}")
            raise
        
        # Process neighbours - original iteration logic
        neighbours = []
        
        try:
            if isinstance(neighbour_results, dict):
                # Handle dictionary format from GLIDE - convert to list for consistent processing
                neighbour_list = [(member, score) for member, score in neighbour_results.items()]
                logger.info(f"Converted dict to list: {len(neighbour_list)} items")
            elif isinstance(neighbour_results, list):
                neighbour_list = neighbour_results
                logger.info(f"Using list format: {len(neighbour_list)} items")
            else:
                neighbour_list = []
                logger.warning(f"Unexpected neighbour_results type: {type(neighbour_results)}")
            
            # Track skipped init placeholder entries to prevent rank gaps. By design,
            # placeholders are removed on first score write, so this only activates in
            # the rare edge case where removal failed silently.
            skipped_count = 0

            # Process each neighbour with proper position calculation
            for i, item in enumerate(neighbour_list):
                logger.debug(f"Processing neighbour {i}: {item}")

                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    member, score = item[0], item[1]
                else:
                    logger.warning(f"Skipping invalid neighbour item: {item}")
                    continue

                if isinstance(member, bytes):
                    member = member.decode('utf-8')

                score = float(score)

                # Skip dummy entries
                if is_dummy_entry(member, score, score_type):
                    skipped_count += 1
                    logger.debug(f"Skipping dummy entry: {member}")
                    continue

                # ORIGINAL LOGIC: Adjust score based on leaderboard type
                if leaderboard_type == "ASCENDING_LB":
                    score = -score

                neighbour_rank = start_rank + i + 1 - skipped_count
                logger.debug(f"Adding neighbour: rank={neighbour_rank}, player={member}, score={score}")

                neighbours.append({
                    'rank': neighbour_rank,
                    'playerID': member,
                    'score': format_score_for_display(score, leaderboard_config),
                    'isTarget': member == player_id
                })
            
            logger.info(f"Processed {len(neighbours)} neighbours successfully")
            result['neighbours'] = neighbours
            
        except Exception as e:
            logger.error(f"Error processing neighbours: {str(e)}")
            raise
    
    return result

# ============================================================================
# MAIN LAMBDA HANDLER
# ============================================================================
@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
@handle_errors
@log_execution
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    Lambda handler for player leaderboard standing retrieval operations.
    """
    # ENTRY LOGGING - Always log function entry
    logger.info("=== LAMBDA HANDLER ENTRY ===")
    logger.info(f"Function: {context.function_name}")
    logger.info(f"Request ID: {context.aws_request_id}")
    
    start_time = time.perf_counter()
    
    # Extract HTTP method and path early for logging
    http_method = event.get('httpMethod', '').upper()
    path = event.get('path', '')
    
    logger.info(f"Processing request: {http_method} {path}")
    logger.info(f"Event keys: {list(event.keys())}")
    
    # Validate HTTP method - API Gateway only allows POST
    if http_method != 'POST':
        logger.error(f"Invalid HTTP method: {http_method}")
        return {
            'statusCode': 405,
            'headers': {
                'Content-Type': 'application/json',
                'Allow': 'POST',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'playerLBStandingResponse': {
                'error': 'Method Not Allowed',
                'message': f'HTTP method {http_method} not allowed. Only POST is supported.',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }})
        }
    
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
            'body': json.dumps({'playerLBStandingResponse': {
                'success': False,
                'error': 'Service temporarily unavailable - Valkey dependency not found',
                'message': 'The leaderboard service is currently unavailable due to a dependency issue'
            }})
        }
    
    logger.info("Valkey is available, proceeding with request processing")
    
    # Authenticate request
    auth_context = validate_authenticated_context(event, 'read')
    logger.info(f"Player standing request for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
    
    # Validate AWS resources after authentication
    validate_aws_resources()
    
    # Process the player standing request
    response_data = run_async(handle_player_standing_request(event))
    
    # Calculate total processing time
    total_time = time.perf_counter() - start_time
    
    # Add common metadata
    if 'processingTimeMs' not in response_data:
        response_data['processingTimeMs'] = int(total_time * 1000)
    
    if 'metadata' in response_data:
        response_data['metadata']['requestId'] = context.aws_request_id
        response_data['metadata']['totalProcessingTimeMs'] = int(total_time * 1000)
    
    # Generate ETag for caching
    config_hash = hash(str(response_data.get('playerLBStandingResponse', {}).get('playerLBStandingInfo', {})))
    etag = f'"{abs(config_hash)}"'
    
    # Build response headers
    headers = {
        'Content-Type': 'application/json',
        'Cache-Control': 'max-age=60',  # Cache for 1 minute
        'ETag': etag
    }
    
    # COMPLETION LOGGING
    logger.info(f"=== LAMBDA HANDLER COMPLETION ===")
    logger.info(f"Status: 200, Processing time: {int(total_time * 1000)}ms")
    logger.info(f"Response size: {len(json.dumps(response_data, default=decimal_serializer))} bytes")
    
    # Return response
    return {
        'statusCode': 200,
        'headers': headers,
        'body': json.dumps(response_data, default=decimal_serializer)
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
    
    # Clear memory cache
    config_cache.clear()
    
    if cleanup_tasks:
        try:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            logger.info("Successfully cleaned up all resources")
        except Exception as e:
            logger.warning(f"Error during cleanup: {str(e)}")
    
    valkey_client = None


# Register cleanup for Lambda container lifecycle
import atexit
atexit.register(lambda: run_async(cleanup_resources()))