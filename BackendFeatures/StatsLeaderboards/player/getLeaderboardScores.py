# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
getLeaderboardScores.py -- Get Player Stats and Scores from Leaderboards

Retrieves player statistics and scores from configured leaderboards.
Supports multiple query patterns:
- Top scores
- Score ranges  
- Scores around a specific player
- Player-specific scores

Updated for Python 3.13 and Valkey-GLIDE 2.4.1
"""

import os
import sys
import json
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional, Union, Tuple
from functools import wraps, lru_cache
from datetime import datetime, timezone
from decimal import Decimal

def safe_float_conversion(score):
    """Safely convert score from various types (Decimal, str, bytes, float) to float."""
    if score is None:
        return None
    # Explicitly exclude boolean values (which are instances of int in Python)
    if isinstance(score, bool):
        return None
    if isinstance(score, (int, float)):
        return float(score)
    if isinstance(score, Decimal):
        return float(score)
    if isinstance(score, bytes):
        try:
            return float(score.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None
    if isinstance(score, str):
        try:
            return float(score)
        except ValueError:
            return None
    return None
import base64
import re

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
        RangeByScore,
        InfBound,
        ScoreBoundary,
        Limit,
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
    class RangeByIndex: pass
    class RangeByScore: pass
    class InfBound: pass
    class ScoreBoundary: pass
    class Limit: pass

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
CONNECTION_TIMEOUT = int(os.environ.get('GLIDE_CONNECTION_TIMEOUT_MS', '2000'))
REQUEST_TIMEOUT = int(os.environ.get('GLIDE_REQUEST_TIMEOUT_MS', '5000'))
CONFIG_CACHE_TTL = int(os.environ.get('CONFIG_CACHE_TTL', '300'))  # 5 minutes
MAX_PAGE_SIZE = int(os.environ.get('MAX_PAGE_SIZE', '500'))
DEFAULT_PAGE_SIZE = int(os.environ.get('DEFAULT_PAGE_SIZE', '10'))

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
VALID_QUERY_TYPES = ['top', 'range', 'aroundPlayer', 'playerScore']
INIT_PLACEHOLDER_PATTERNS = [
    "_init_topscore_", "_init_time_", "_init_distance_",
    "_init_points_", "_init_rank_", "_init_level_"
]
VALID_LEADERBOARD_TYPES = ["DESCENDING_LB", "ASCENDING_LB"]

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
                'body': json.dumps({'leaderboardScoresResponse': {
                    'error': 'Not Found',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'success': False
                }})
            }
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'leaderboardScoresResponse': {
                    'error': 'Bad Request',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'success': False
                }})
            }
        except LeaderboardAuthenticationError as e:
            logger.error(f"Authentication error: {str(e)}")
            return {
                'statusCode': 401,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'leaderboardScoresResponse': {
                    'error': 'Unauthorized',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'success': False
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
                'body': json.dumps({'leaderboardScoresResponse': {
                    'error': error_code,
                    'message': error_message,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'success': False
                }})
            }
        except ConnectionError as e:
            logger.error(f"Valkey connection error: {str(e)}")
            # Invalidate the client so next invocation creates a fresh one
            global valkey_client
            valkey_client = None
            return {
                'statusCode': 503,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'leaderboardScoresResponse': {
                    'error': 'Service Unavailable',
                    'message': 'Database connection error',
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'success': False
                }})
            }
        except Exception as e:
            error_msg = str(e)
            # Check if this is a Valkey connection/routing error that warrants client reset
            connection_error_keywords = ['ConnectionNotFound', 'connection error', 'ConnectionError', 
                                         'Received connection error', 'ConnectionNotFoundForRoute']
            if any(keyword in error_msg for keyword in connection_error_keywords):
                logger.warning(f"Valkey connection/routing error detected, invalidating client for retry: {error_msg}")
                valkey_client = None
            
            logger.exception(f"Unexpected error: {error_msg}")
            return {
                'statusCode': 500,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'leaderboardScoresResponse': {
                    'error': 'Internal Server Error',
                    'message': f'An unexpected error occurred: {str(e)}',
                    'errorType': e.__class__.__name__,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'success': False
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
                client_name="leaderboard-scores-lambda",
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
                client_name="leaderboard-scores-lambda",
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
            thread_name_prefix="leaderboard-scores-worker"
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
    time_precision = leaderboard_config.get('timePrecision', 3)
    
    # Ensure time_precision is an integer (DynamoDB may return Decimal)
    # This matches the pattern used in batchStoreStatsAndScores.py:805-809
    try:
        time_precision = int(time_precision)
    except (ValueError, TypeError):
        time_precision = 3
    
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


# ============================================================================
# REQUEST VALIDATION
# ============================================================================

@tracer.capture_method
def validate_scores_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate request for getting leaderboard scores.
    """
    if 'body' not in event or event['body'] is None:
        raise ValueError("Request body is missing")
    
    try:
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in request body")
    
    if 'leaderboardScoresRequest' not in body:
        raise ValueError("leaderboardScoresRequest is missing in request body")
    
    request_params = body['leaderboardScoresRequest']
    
    # Validate leaderboardName
    if 'leaderboardName' not in request_params:
        raise ValueError("leaderboardName is missing")
    
    leaderboard_name = request_params['leaderboardName']
    if not isinstance(leaderboard_name, str) or not leaderboard_name.strip():
        raise ValueError("leaderboardName must be a non-empty string")
    
    # Security: Validate leaderboard name format
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', leaderboard_name):
        raise ValueError("leaderboardName contains invalid characters")
    
    # Validate and set query type
    query_type = request_params.get('queryType', 'top')
    if query_type not in VALID_QUERY_TYPES:
        raise ValueError(f"queryType must be one of {VALID_QUERY_TYPES}")
    request_params['queryType'] = query_type
    
    # Validate page size (support both 'pageSize' and 'limit' parameters)
    page_size = request_params.get('pageSize')
    if page_size is None:
        page_size = request_params.get('limit', DEFAULT_PAGE_SIZE)
    try:
        page_size = int(page_size)
        if page_size <= 0 or page_size > MAX_PAGE_SIZE:
            raise ValueError(f"pageSize must be between 1 and {MAX_PAGE_SIZE}")
        request_params['pageSize'] = page_size
    except (ValueError, TypeError):
        raise ValueError("pageSize must be a valid positive integer")
    
    # Validate query-specific parameters
    if query_type == 'range':
        if 'minScore' not in request_params or 'maxScore' not in request_params:
            raise ValueError("minScore and maxScore are required for range queries")
        
        try:
            min_score = float(request_params['minScore'])
            max_score = float(request_params['maxScore'])
            
            if min_score > max_score:
                raise ValueError("minScore must be less than or equal to maxScore")
            
            # Security: Reasonable bounds check
            if abs(min_score) > 1e15 or abs(max_score) > 1e15:
                raise ValueError("Score values are too large")
            
            request_params['minScore'] = min_score
            request_params['maxScore'] = max_score
        except (ValueError, TypeError, OverflowError):
            raise ValueError("minScore and maxScore must be valid numbers")
        
        request_params['inclusive'] = request_params.get('inclusive', True)
        if not isinstance(request_params['inclusive'], bool):
            raise ValueError("inclusive must be a boolean")
    
    elif query_type == 'aroundPlayer':
        if 'playerID' not in request_params:
            raise ValueError("playerID is required for aroundPlayer queries")
        
        player_id = request_params['playerID']
        if not isinstance(player_id, str) or not player_id.strip():
            raise ValueError("playerID must be a non-empty string")
        
        # Security: Validate playerID format
        if not re.match(r'^[a-zA-Z0-9_-]+$', player_id):
            raise ValueError("playerID contains invalid characters")
        
        # Validate count parameters
        count_before = request_params.get('countBefore', 5)
        count_after = request_params.get('countAfter', 5)
        
        try:
            count_before = int(count_before)
            count_after = int(count_after)
            
            if count_before < 0 or count_after < 0:
                raise ValueError("countBefore and countAfter must be non-negative")
            
            if count_before + count_after + 1 > MAX_PAGE_SIZE:
                raise ValueError(f"Total count (countBefore + countAfter + 1) cannot exceed {MAX_PAGE_SIZE}")
            
            request_params['countBefore'] = count_before
            request_params['countAfter'] = count_after
        except (ValueError, TypeError):
            raise ValueError("countBefore and countAfter must be valid non-negative integers")
    
    elif query_type == 'playerScore':
        if 'playerID' not in request_params:
            raise ValueError("playerID is required for playerScore queries")
        
        player_id = request_params['playerID']
        if not isinstance(player_id, str) or not player_id.strip():
            raise ValueError("playerID must be a non-empty string")
        
        # Security: Validate playerID format
        if not re.match(r'^[a-zA-Z0-9_-]+$', player_id):
            raise ValueError("playerID contains invalid characters")
    
    # Validate pagination token
    if 'nextToken' in request_params and request_params['nextToken']:
        try:
            decoded_token = json.loads(base64.b64decode(request_params['nextToken']).decode('utf-8'))
            if not isinstance(decoded_token, dict):
                raise ValueError("Invalid nextToken format")
            request_params['decodedNextToken'] = decoded_token
        except Exception:
            raise ValueError("Invalid nextToken format")
    
    # Handle offset parameter for top queries (convert to nextToken format)
    if query_type == 'top' and 'offset' in request_params:
        try:
            offset = int(request_params['offset'])
            if offset < 0:
                raise ValueError("offset must be non-negative")
            # Convert offset to nextToken format
            request_params['decodedNextToken'] = {'startIndex': offset}
        except (ValueError, TypeError):
            raise ValueError("offset must be a valid non-negative integer")
    
    return request_params


# ============================================================================
# LEADERBOARD CONFIGURATION RETRIEVAL
# ============================================================================

@tracer.capture_method
async def get_leaderboard_config_cached(leaderboard_name: str) -> Dict[str, Any]:
    """
    Get leaderboard configuration with multi-level caching.
    """
    global config_cache
    current_time = time.time()
    cache_key = f"config:{leaderboard_name}"
    
    # Check in-memory cache first
    if cache_key in config_cache:
        config, timestamp = config_cache[cache_key]
        if current_time - timestamp < CONFIG_CACHE_TTL:
            logger.info(f"Retrieved config from memory cache: {leaderboard_name}")
            return config
        else:
            # Remove expired entry
            del config_cache[cache_key]
    
    # Try Valkey cache
    try:
        client = await get_valkey_client()
        valkey_cache_key = f"config:{leaderboard_name}"
        cached_config = await client.get(valkey_cache_key)
        
        if cached_config:
            config = json.loads(cached_config.decode('utf-8') if isinstance(cached_config, bytes) else cached_config)
            # Store in memory cache
            config_cache[cache_key] = (config, current_time)
            logger.info(f"Retrieved config from Valkey cache: {leaderboard_name}")
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
            valkey_cache_key = f"config:{leaderboard_name}"
            # Use set with expiry parameter for GlideClusterClient compatibility
            await client.set(valkey_cache_key, json.dumps(config, default=decimal_serializer), expiry=ExpirySet(ExpiryType.SEC, CONFIG_CACHE_TTL))
        except Exception as e:
            logger.warning(f"Failed to cache config in Valkey: {str(e)}")
        
        config_cache[cache_key] = (config, current_time)
        logger.info(f"Retrieved and cached config from DynamoDB: {leaderboard_name}")
        
        return config
        
    except ClientError as e:
        logger.error(f"Error retrieving leaderboard configuration: {str(e)}")
        raise


def is_placeholder_entry(player_id: str) -> bool:
    """Check if player ID is an initialization placeholder."""
    return player_id in INIT_PLACEHOLDER_PATTERNS


# ============================================================================
# SCORE RETRIEVAL OPERATIONS
# ============================================================================

@tracer.capture_method
async def get_top_scores(
    client: Union[GlideClusterClient, GlideClient],
    sorted_list_name: str,
    leaderboard_type: str,
    page_size: int,
    leaderboard_config: Dict[str, Any],
    next_token: Optional[Dict[str, Any]] = None
) -> Tuple[List[Dict[str, Any]], Optional[str], Dict[str, Any]]:
    """
    Get top scores from leaderboard using GLIDE 2.4.1 API.
    """
    start_index = next_token.get('startIndex', 0) if next_token else 0
    end_index = start_index + page_size - 1
    
    # Get scores - use reverse=True for both leaderboard types
    # ASCENDING_LB: scores stored as negative, reverse=True gives us smallest negatives first
    #               which become smallest positives when converted back
    # DESCENDING_LB: scores stored as positive, reverse=True gives us largest first
    result = await client.zrange_withscores(
        sorted_list_name,
        RangeByIndex(start_index, end_index),
        reverse=True
    )
    
    total_count = await client.zcard(sorted_list_name)
    
    # Adjust count if placeholder exists (placeholder is filtered from results but counted in zcard)
    for placeholder in INIT_PLACEHOLDER_PATTERNS:
        if await client.zscore(sorted_list_name, placeholder) is not None:
            total_count = max(0, total_count - 1)
            break
    
    # Process results
    scores = []
    if result:
        logger.info(f"=== DEBUGGING RESULT FORMAT ===")
        logger.info(f"Result type: {type(result)}")
        logger.info(f"Result length: {len(result) if hasattr(result, '__len__') else 'N/A'}")

        # Track skipped init placeholder entries to prevent rank gaps. By design,
        # placeholders are removed on first score write, so this only activates in
        # the rare edge case where removal failed silently.
        skipped_count = 0

        # Handle dictionary results from GLIDE API
        if isinstance(result, dict):
            logger.info(f"Processing dictionary result with {len(result)} items")
            for i, (player_id, score) in enumerate(result.items()):
                logger.debug(f"Processing dict item: player_id={player_id}, score={score}")

                if isinstance(player_id, bytes):
                    player_id = player_id.decode('utf-8')

                # Skip placeholder entries
                if is_placeholder_entry(player_id):
                    skipped_count += 1
                    continue

                if isinstance(score, bytes):
                    score = float(score.decode('utf-8'))
                elif not isinstance(score, (int, float)):
                    score = float(score)

                scores.append({
                    'playerID': player_id,
                    'score': format_score_for_display(score, leaderboard_config),
                    'rank': start_index + i + 1 - skipped_count
                })
        else:
            # Fallback for list/tuple results
            logger.info(f"Processing non-dict result type: {type(result)}")
            for i, item in enumerate(result):
                logger.debug(f"Processing item {i}: type={type(item)}, value={item}")
                if isinstance(item, tuple) and len(item) == 2:
                    player_id, score = item
                    logger.debug(f"Successfully unpacked tuple: player_id={player_id}, score={score}")

                    if isinstance(player_id, bytes):
                        player_id = player_id.decode('utf-8')

                    # Skip placeholder entries
                    if is_placeholder_entry(player_id):
                        skipped_count += 1
                        continue

                    if isinstance(score, bytes):
                        score = float(score.decode('utf-8'))
                    elif not isinstance(score, (int, float)):
                        score = float(score)

                    scores.append({
                        'playerID': player_id,
                        'score': format_score_for_display(score, leaderboard_config),
                        'rank': start_index + i + 1 - skipped_count
                    })
                else:
                    logger.error(f"FAILED TO UNPACK: Unexpected result format at index {i}: {item} (type: {type(item)})")
                    continue
        
        logger.info(f"Successfully processed {len(scores)} scores")
    
    # Determine pagination
    has_more = (start_index + page_size) < total_count
    next_page_token = None
    if has_more:
        next_token_data = {'startIndex': start_index + page_size}
        next_page_token = base64.b64encode(
            json.dumps(next_token_data).encode('utf-8')
        ).decode('utf-8')
    
    metadata = {
        'totalPlayers': total_count,
        'startIndex': start_index,
        'endIndex': min(end_index, total_count - 1)
    }
    
    return scores, next_page_token, metadata


@tracer.capture_method
async def get_range_scores(
    client: Union[GlideClusterClient, GlideClient],
    sorted_list_name: str,
    leaderboard_type: str,
    min_score: float,
    max_score: float,
    inclusive: bool,
    page_size: int,
    leaderboard_config: Dict[str, Any],
    next_token: Optional[Dict[str, Any]] = None
) -> Tuple[List[Dict[str, Any]], Optional[str], Dict[str, Any]]:
    """
    Get scores within a range using GLIDE 2.4.1 API.
    """
    offset = next_token.get('offset', 0) if next_token else 0
    
    # Create score boundaries - adjust for ASCENDING_LB negative storage
    if leaderboard_type == "ASCENDING_LB":
        # For ASCENDING_LB, scores are stored as negative values, so flip the range
        if inclusive:
            min_boundary = ScoreBoundary(-max_score, is_inclusive=True)
            max_boundary = ScoreBoundary(-min_score, is_inclusive=True)
        else:
            min_boundary = ScoreBoundary(-max_score, is_inclusive=False)
            max_boundary = ScoreBoundary(-min_score, is_inclusive=False)
    else:
        # For DESCENDING_LB, use scores as-is
        if inclusive:
            min_boundary = ScoreBoundary(min_score, is_inclusive=True)
            max_boundary = ScoreBoundary(max_score, is_inclusive=True)
        else:
            min_boundary = ScoreBoundary(min_score, is_inclusive=False)
            max_boundary = ScoreBoundary(max_score, is_inclusive=False)
    
    # Get scores in range using GLIDE 2.4.1 zrange_withscores
    range_by_score = RangeByScore(min_boundary, max_boundary)
    
    logger.info(f"Range query boundaries: min={min_boundary.value}, max={max_boundary.value}, inclusive={inclusive}")
    
    try:
        # GLIDE 2.4.1 reverse=True with RangeByScore is broken, so always use reverse=False
        # and handle sorting in application code
        all_results = await client.zrange_withscores(
            sorted_list_name,
            range_by_score,
            reverse=False
        )
        
        logger.info(f"ZRANGE_WITHSCORES returned: type={type(all_results)}, length={len(all_results) if hasattr(all_results, '__len__') else 'N/A'}")
        
    except Exception as e:
        logger.error(f"ZRANGE_WITHSCORES failed: {e}")
        all_results = {}
    
    # Process all results first, then paginate
    all_scores = []
    if all_results:
        logger.info(f"=== RANGE QUERY PROCESSING ===")
        logger.info(f"all_results type: {type(all_results)}, length: {len(all_results) if hasattr(all_results, '__len__') else 'N/A'}")
        
        # Handle dictionary results from GLIDE API
        if isinstance(all_results, dict):
            logger.info(f"Processing range dictionary result with {len(all_results)} items")
            for player_id, score in all_results.items():
                if isinstance(player_id, bytes):
                    player_id = player_id.decode('utf-8')
                
                # Skip placeholder entries
                if is_placeholder_entry(player_id):
                    continue
                
                if isinstance(score, bytes):
                    score = float(score.decode('utf-8'))
                elif not isinstance(score, (int, float)):
                    score = float(score)
                
                all_scores.append({
                    'playerID': player_id,
                    'score': format_score_for_display(score, leaderboard_config),
                    'rank': len(all_scores) + 1
                })
        else:
            # Handle list/tuple results
            logger.info(f"Processing range list result with {len(all_results)} items")
            for item in all_results:
                if isinstance(item, tuple) and len(item) == 2:
                    player_id, score = item
                    
                    if isinstance(player_id, bytes):
                        player_id = player_id.decode('utf-8')
                    
                    # Skip placeholder entries
                    if is_placeholder_entry(player_id):
                        continue
                    
                    if isinstance(score, bytes):
                        score = float(score.decode('utf-8'))
                    elif not isinstance(score, (int, float)):
                        score = float(score)
                    
                    all_scores.append({
                        'playerID': player_id,
                        'score': format_score_for_display(score, leaderboard_config),
                        'rank': len(all_scores) + 1
                    })
                else:
                    logger.error(f"RANGE FAILED TO UNPACK: Unexpected result format: {item} (type: {type(item)})")
                    continue
    
    # Native Valkey ordering with reverse=True provides correct ranking for both leaderboard types
    # No post-retrieval sorting needed - ranks are already correct from Valkey
    
    # Apply pagination to processed scores
    start_idx = offset
    end_idx = offset + page_size
    scores = all_scores[start_idx:end_idx] if start_idx < len(all_scores) else []
    
    logger.info(f"Range query: processed {len(all_scores)} total scores, returning {len(scores)} after pagination")
    
    # Get count in range using the same adjusted boundaries
    count_in_range = await client.zcount(sorted_list_name, min_boundary, max_boundary)
    
    # Pagination
    has_more = (offset + page_size) < count_in_range
    next_page_token = None
    if has_more:
        next_token_data = {'offset': offset + page_size}
        next_page_token = base64.b64encode(
            json.dumps(next_token_data).encode('utf-8')
        ).decode('utf-8')
    
    metadata = {
        'totalInRange': count_in_range,
        'offset': offset,
        'minScore': min_score,
        'maxScore': max_score,
        'inclusive': inclusive
    }
    
    return scores, next_page_token, metadata


@tracer.capture_method
async def get_scores_around_player(
    client: Union[GlideClusterClient, GlideClient],
    sorted_list_name: str,
    leaderboard_type: str,
    player_id: str,
    count_before: int,
    count_after: int,
    leaderboard_config: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Get scores around a specific player using GLIDE 2.4.1 API.
    """
    # Get player's score and rank
    player_score = await client.zscore(sorted_list_name, player_id)
    
    if player_score is None:
        raise ValueError(f"Player '{player_id}' not found in leaderboard")
    
    # For both leaderboard types, use zrevrank to match the reverse=True query
    # ASCENDING_LB: scores stored as negative, zrevrank gives position from largest (worst) to smallest (best)
    #               but we want position from smallest (best) to largest (worst), so we use zrevrank
    #               which gives us the reverse position that matches our reverse=True query
    # DESCENDING_LB: scores stored as positive, zrevrank gives position from largest (best) to smallest (worst)
    if leaderboard_type == "DESCENDING_LB":
        player_rank = await client.zrevrank(sorted_list_name, player_id)
    else:
        # For ASCENDING_LB, use zrevrank to match the reverse=True query below
        player_rank = await client.zrevrank(sorted_list_name, player_id)
    
    if player_rank is None:
        raise ValueError(f"Could not determine rank for player '{player_id}'")
    
    total_count = await client.zcard(sorted_list_name)
    
    # Adjust count if placeholder exists
    for placeholder in INIT_PLACEHOLDER_PATTERNS:
        if await client.zscore(sorted_list_name, placeholder) is not None:
            total_count = max(0, total_count - 1)
            break
    
    # Calculate range
    start_rank = max(0, player_rank - count_before)
    end_rank = min(player_rank + count_after, total_count - 1)
    
    # Get scores in range - use reverse=True for both leaderboard types
    # This matches the zrevrank used above for both types
    # ASCENDING_LB: scores stored as negative, reverse=True gives us smallest negatives first
    #               which become smallest positives when converted back
    # DESCENDING_LB: scores stored as positive, reverse=True gives us largest first
    result = await client.zrange_withscores(
        sorted_list_name,
        RangeByIndex(start_rank, end_rank),
        reverse=True
    )
    
    # Process results
    scores = []
    if result:
        logger.info(f"=== AROUND PLAYER RESULT FORMAT ===")
        logger.info(f"Result type: {type(result)}")
        logger.info(f"Result length: {len(result) if hasattr(result, '__len__') else 'N/A'}")

        # Track skipped init placeholder entries to prevent rank gaps. By design,
        # placeholders are removed on first score write, so this only activates in
        # the rare edge case where removal failed silently.
        skipped_count = 0

        # Handle dictionary results from GLIDE API
        if isinstance(result, dict):
            logger.info(f"Processing aroundPlayer dictionary result with {len(result)} items")
            # Convert dict to list for proper rank calculation
            result_items = list(result.items())

            for i, (player_id_bytes, score) in enumerate(result_items):
                if isinstance(player_id_bytes, bytes):
                    current_player_id = player_id_bytes.decode('utf-8')
                else:
                    current_player_id = str(player_id_bytes)

                # Skip placeholder entries
                if is_placeholder_entry(current_player_id):
                    skipped_count += 1
                    continue

                if isinstance(score, bytes):
                    score = float(score.decode('utf-8'))
                elif not isinstance(score, (int, float)):
                    score = float(score)

                # Calculate actual rank: start_rank is 0-based, ranks are 1-based
                actual_rank = start_rank + i + 1 - skipped_count

                scores.append({
                    'playerID': current_player_id,
                    'score': format_score_for_display(score, leaderboard_config),
                    'rank': actual_rank
                })
        else:
            # Handle list/tuple results
            logger.info(f"Processing aroundPlayer list result type: {type(result)}")
            for i, item in enumerate(result):
                if isinstance(item, tuple) and len(item) == 2:
                    pid, score = item

                    if isinstance(pid, bytes):
                        pid = pid.decode('utf-8')

                    # Skip placeholder entries
                    if is_placeholder_entry(pid):
                        skipped_count += 1
                        continue

                    if isinstance(score, bytes):
                        score = float(score.decode('utf-8'))
                    elif not isinstance(score, (int, float)):
                        score = float(score)

                    # Calculate actual rank: start_rank is 0-based, ranks are 1-based
                    actual_rank = start_rank + i + 1 - skipped_count

                    scores.append({
                        'playerID': pid,
                        'score': format_score_for_display(score, leaderboard_config),
                        'rank': actual_rank
                    })
                else:
                    logger.error(f"AROUND PLAYER FAILED TO UNPACK: Unexpected result format at index {i}: {item} (type: {type(item)})")
                    continue
        
        logger.info(f"Successfully processed {len(scores)} aroundPlayer scores")
    
    metadata = {
        'totalPlayers': total_count,
        'targetPlayer': player_id,
        'targetRank': player_rank + 1,
        'targetScore': format_score_for_display(player_score, leaderboard_config),
        'countBefore': count_before,
        'countAfter': count_after
    }
    
    return scores, metadata


@tracer.capture_method
async def get_player_score(
    client: Union[GlideClusterClient, GlideClient],
    sorted_list_name: str,
    leaderboard_type: str,
    player_id: str,
    leaderboard_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Get a specific player's score and rank using GLIDE 2.4.1 API.
    """
    # Get player's score
    player_score = await client.zscore(sorted_list_name, player_id)
    
    if player_score is None:
        raise ValueError(f"Player '{player_id}' not found in leaderboard")
    
    # Get player's rank - use zrevrank for both leaderboard types
    # ASCENDING_LB: scores stored as negative, zrevrank gives correct position
    # DESCENDING_LB: scores stored as positive, zrevrank gives correct position
    if leaderboard_type == "DESCENDING_LB":
        player_rank = await client.zrevrank(sorted_list_name, player_id)
    else:
        # For ASCENDING_LB, use zrevrank to match the negative score storage
        player_rank = await client.zrevrank(sorted_list_name, player_id)
    
    if player_rank is None:
        raise ValueError(f"Could not determine rank for player '{player_id}'")
    
    # Get total count
    total_count = await client.zcard(sorted_list_name)
    
    # Adjust count if placeholder exists
    for placeholder in INIT_PLACEHOLDER_PATTERNS:
        if await client.zscore(sorted_list_name, placeholder) is not None:
            total_count = max(0, total_count - 1)
            break
    
    converted_player_score = safe_float_conversion(player_score)
    if converted_player_score is None:
        raise ValueError(f"Invalid score format for player '{player_id}': {player_score} (type: {type(player_score)})")
    
    return {
        'playerID': player_id,
        'score': format_score_for_display(converted_player_score, leaderboard_config),
        'rank': player_rank + 1,
        'totalPlayers': total_count,
        'percentile': round((1 - (player_rank / total_count)) * 100, 2) if total_count > 0 else 0
    }


# ============================================================================
# MAIN REQUEST HANDLERS
# ============================================================================

async def handle_get_scores(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle get leaderboard scores operation.
    """
    logger.info("=== HANDLE_GET_SCORES ENTRY ===")
    start_time = time.perf_counter()
    
    try:
        request_params = validate_scores_request(event)
        leaderboard_name = request_params['leaderboardName']
        query_type = request_params['queryType']
        
        # Get leaderboard configuration
        leaderboard_config = await get_leaderboard_config_cached(leaderboard_name)
        
        # Validate leaderboard type
        leaderboard_type = leaderboard_config.get('leaderboardType')
        if leaderboard_type not in VALID_LEADERBOARD_TYPES:
            raise ValueError(f"Invalid leaderboard type: {leaderboard_type}")
        
        # Get sorted list name
        sorted_list_name = leaderboard_config.get('sortedListName')
        if not sorted_list_name:
            raise ValueError(f"Leaderboard configuration missing sortedListName")
        
        # Get Valkey client
        client = await get_valkey_client()
        
        # Check if sorted list exists
        exists = await client.exists([sorted_list_name])
        if exists == 0:
            logger.warning(f"Sorted list {sorted_list_name} does not exist in Valkey")
            # Return empty result set
            return {
                'leaderboardName': leaderboard_name,
                'leaderboardType': leaderboard_type,
                'queryType': query_type,
                'scores': [],
                'metadata': {
                    'totalPlayers': 0,
                    'scoresCount': 0,
                    'message': 'Leaderboard is empty'
                },
                'success': True
            }
        
        # Process query based on type
        if query_type == 'top':
            scores, next_token, metadata = await get_top_scores(
                client,
                sorted_list_name,
                leaderboard_type,
                request_params['pageSize'],
                leaderboard_config,
                request_params.get('decodedNextToken')
            )
            
            response_data = {
                'leaderboardName': leaderboard_name,
                'leaderboardType': leaderboard_type,
                'queryType': query_type,
                'scores': scores,
                'metadata': {
                    'totalPlayers': metadata['totalPlayers'],
                    'scoresCount': len(scores),
                    'pageSize': request_params['pageSize'],
                    'hasMoreResults': next_token is not None,
                    'nextToken': next_token
                },
                'success': True
            }
            
        elif query_type == 'range':
            scores, next_token, metadata = await get_range_scores(
                client,
                sorted_list_name,
                leaderboard_type,
                request_params['minScore'],
                request_params['maxScore'],
                request_params['inclusive'],
                request_params['pageSize'],
                leaderboard_config,
                request_params.get('decodedNextToken')
            )
            
            response_data = {
                'leaderboardName': leaderboard_name,
                'leaderboardType': leaderboard_type,
                'queryType': query_type,
                'queryParameters': {
                    'minScore': request_params['minScore'],
                    'maxScore': request_params['maxScore'],
                    'inclusive': request_params['inclusive']
                },
                'scores': scores,
                'metadata': {
                    'totalInRange': metadata['totalInRange'],
                    'scoresCount': len(scores),
                    'pageSize': request_params['pageSize'],
                    'hasMoreResults': next_token is not None,
                    'nextToken': next_token
                },
                'success': True
            }
            
        elif query_type == 'aroundPlayer':
            scores, metadata = await get_scores_around_player(
                client,
                sorted_list_name,
                leaderboard_type,
                request_params['playerID'],
                request_params['countBefore'],
                request_params['countAfter'],
                leaderboard_config
            )
            
            response_data = {
                'leaderboardName': leaderboard_name,
                'leaderboardType': leaderboard_type,
                'queryType': query_type,
                'targetPlayer': {
                    'playerID': metadata['targetPlayer'],
                    'rank': metadata['targetRank'],
                    'score': metadata['targetScore']
                },
                'scores': scores,
                'metadata': {
                    'totalPlayers': metadata['totalPlayers'],
                    'scoresCount': len(scores),
                    'countBefore': metadata['countBefore'],
                    'countAfter': metadata['countAfter']
                },
                'success': True
            }
            
        elif query_type == 'playerScore':
            player_data = await get_player_score(
                client,
                sorted_list_name,
                leaderboard_type,
                request_params['playerID'],
                leaderboard_config
            )
            
            response_data = {
                'leaderboardName': leaderboard_name,
                'leaderboardType': leaderboard_type,
                'queryType': query_type,
                'playerData': player_data,
                'metadata': {
                    'totalPlayers': player_data['totalPlayers']
                },
                'success': True
            }
            
        else:
            raise ValueError(f"Unsupported query type: {query_type}")
        
        # Add score type information if available
        if 'scoreType' in leaderboard_config:
            response_data['scoreType'] = leaderboard_config['scoreType']
            
            # Add time-specific formatting if applicable
            if leaderboard_config['scoreType'] == 'time':
                response_data['timeFormat'] = leaderboard_config.get('timeFormat', 'seconds')
                response_data['timePrecision'] = leaderboard_config.get('timePrecision', 3)
        
        processing_time = time.perf_counter() - start_time
        response_data['processingTimeMs'] = int(processing_time * 1000)
        
        logger.info(f"=== HANDLE_GET_SCORES EXIT === Processing time: {int(processing_time * 1000)}ms")
        return response_data
        
    except Exception as e:
        processing_time = time.perf_counter() - start_time
        logger.error(f"=== HANDLE_GET_SCORES ERROR EXIT === Processing time: {int(processing_time * 1000)}ms, Error: {str(e)}")
        raise


# ============================================================================
# MAIN LAMBDA HANDLER
# ============================================================================

@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
@handle_errors
@log_execution
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    Lambda handler for retrieving player stats and scores from leaderboards.
    """
    # ENTRY LOGGING
    logger.info("=== LAMBDA HANDLER ENTRY ===")
    logger.info(f"Function: {context.function_name}")
    logger.info(f"Request ID: {context.aws_request_id}")
    
    start_time = time.perf_counter()
    
    # Extract HTTP method and path
    http_method = event.get('httpMethod', '').upper()
    path = event.get('path', '')
    
    logger.info(f"Processing request: {http_method} {path}")
    logger.info(f"Event keys: {list(event.keys())}")
    
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
                'success': False,
                'error': 'Service Unavailable',
                'message': 'The leaderboard service is currently unavailable due to a dependency issue'
            })
        }
    
    logger.info("Valkey is available, proceeding with request routing")
    
    # Authenticate request
    auth_context = validate_authenticated_context(event, 'read')
    logger.info(f"Authenticated request for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
    
    # Validate AWS resources
    validate_aws_resources()
    
    # Route based on method and path
    if http_method == 'POST':
        if path.endswith('/scores') or path.endswith('/leaderboard/scores'):
            response_data = run_async(handle_get_scores(event))
            status_code = 200
        else:
            raise ValueError(f"Unsupported path: {path}")
    else:
        raise ValueError(f"Unsupported HTTP method: {http_method}")
    
    # Calculate total processing time
    total_time = time.perf_counter() - start_time
    
    # Add common metadata
    if 'processingTimeMs' not in response_data:
        response_data['processingTimeMs'] = int(total_time * 1000)
    
    # Build response headers
    headers = {
        'Content-Type': 'application/json',
        'Cache-Control': 'max-age=5',  # Short cache for score data
        'X-Request-Id': context.aws_request_id
    }
    
    # Add total count header if applicable
    if 'metadata' in response_data and 'totalPlayers' in response_data['metadata']:
        headers['X-Total-Players'] = str(response_data['metadata']['totalPlayers'])
    
    # COMPLETION LOGGING
    logger.info(f"=== LAMBDA HANDLER COMPLETION ===")
    logger.info(f"Status: {status_code}, Processing time: {int(total_time * 1000)}ms")
    logger.info(f"Response size: {len(json.dumps(response_data, default=decimal_serializer))} bytes")
    
    # Return response wrapped in leaderboardScoresResponse
    return {
        'statusCode': status_code,
        'headers': headers,
        'body': json.dumps({'leaderboardScoresResponse': response_data}, default=decimal_serializer)
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
    
    # Clear cache
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