# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
batchStoreStatsAndScores.py -- High-Performance Batch Stats and Leaderboard Update Lambda

IMPORTANT: This Lambda function is designed for MULTIPLAYER GAME SERVERS, not individual players.

PURPOSE:
- Processes batch submissions from multiplayer game servers after a game session ends
- Each batch contains results for MULTIPLE DIFFERENT PLAYERS from ONE multiplayer session
- Each player should appear ONLY ONCE per batch (one result per player per session)
- Optimized for server-to-server communication with high throughput requirements

USAGE PATTERN:
- Multiplayer game server completes a match with 4 players
- Server submits ONE batch with 4 different playerIDs and their respective scores
- Each player gets their score stored in DynamoDB and leaderboard updated in MemoryDB

NOT FOR:
- Individual player score submissions (use storePlayerStatsAndScores.py instead)
- Multiple scores for the same player in one batch (causes DynamoDB duplicate key errors)
- Cumulative scoring across sessions (handle via separate batch calls per session)

KEY DESIGN:
- DynamoDB Primary Key: playerID (hash) + gameID#gameMode#timestamp (range)
- This ensures all player submissions are grouped together while maintaining uniqueness per session
- Same timestamp across batch ensures atomic processing of one multiplayer session

Batch processing for game statistics storage and leaderboard updates.
Supports multiple score strategies and time-based leaderboards.

Updated for Python 3.13 and Valkey-GLIDE 2.0.1
"""

import os
import sys
import json
import time
import uuid
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional, Union, Tuple, Set
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
from collections import defaultdict, Counter

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
MAX_CONCURRENT_OPERATIONS = int(os.environ.get('MAX_CONCURRENT_OPERATIONS', '50'))
MAX_BATCH_SIZE = int(os.environ.get('MAX_BATCH_SIZE', '25'))  # DynamoDB limit
MAX_VALKEY_PIPELINE_SIZE = int(os.environ.get('MAX_VALKEY_PIPELINE_SIZE', '100'))
CONNECTION_TIMEOUT = int(os.environ.get('GLIDE_CONNECTION_TIMEOUT_MS', '2000'))
REQUEST_TIMEOUT = int(os.environ.get('GLIDE_REQUEST_TIMEOUT_MS', '5000'))
CONFIG_CACHE_TTL = int(os.environ.get('CONFIG_CACHE_TTL', '300'))
MAX_ITEMS_PER_REQUEST = int(os.environ.get('MAX_ITEMS_PER_REQUEST', '1000'))

# Initialize AWS clients
secretsmanager = boto3.client('secretsmanager', config=boto3.session.Config(
    max_pool_connections=10,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
))

dynamodb = boto3.resource('dynamodb', config=boto3.session.Config(
    max_pool_connections=100,
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
            
            # Sanitize batch data for logging
            if 'body' in params:
                try:
                    body = json.loads(params['body']) if isinstance(params['body'], str) else params['body']
                    if 'batchGameReportBody' in body:
                        batch_body = body['batchGameReportBody']
                        if 'gameReports' in batch_body:
                            params['body'] = {
                                'batchGameReportBody': {
                                    'gameReportsCount': len(batch_body['gameReports']),
                                    'sampleReport': '[REDACTED]'
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


class DuplicatePlayerError(Exception):
    """Custom exception for duplicate player IDs in batch request"""
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
                'body': json.dumps({'batchGameReportResponse': {
                    'error': 'Not Found',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except DuplicatePlayerError as e:
            logger.error(f"Duplicate player error: {str(e)}")
            return {
                'statusCode': 423,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'batchGameReportResponse': {
                    'error': 'Duplicate Players Detected',
                    'errorCode': 'DUPLICATE_PLAYERS_IN_BATCH',
                    'message': 'Batch request contains duplicate player IDs',
                    'details': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except LeaderboardExpiredError as e:
            logger.error(f"Leaderboard expired error: {str(e)}")
            
            # Determine if this is read-only or auto-deletion expiry based on error message
            if "read-only mode" in str(e):
                # Read-only preservation expiry
                return {
                    'statusCode': 423,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({'batchGameReportResponse': {
                        'error': 'Leaderboard Locked',
                        'errorCode': 'LEADERBOARD_EXPIRED_READONLY',
                        'message': 'Cannot write to expired leaderboard in read-only mode',
                        'details': str(e),
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }})
                }
            else:
                # Auto-deletion expiry
                return {
                    'statusCode': 423,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({'batchGameReportResponse': {
                        'error': 'Leaderboard Expired',
                        'errorCode': 'LEADERBOARD_EXPIRED_AUTODELETE',
                        'message': 'Cannot write to expired leaderboard scheduled for deletion',
                        'details': str(e),
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }})
                }
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'batchGameReportResponse': {
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
                'body': json.dumps({'batchGameReportResponse': {
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
                'body': json.dumps({'batchGameReportResponse': {
                    'error': error_code,
                    'message': error_message,
                    'note': 'Data may have been partially stored',
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
                'body': json.dumps({'batchGameReportResponse': {
                    'error': 'Service Unavailable',
                    'message': 'Database connection error',
                    'note': 'Data may have been stored in DynamoDB, but leaderboard updates may be incomplete',
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
                'body': json.dumps({'batchGameReportResponse': {
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
def validate_authenticated_context(event: Dict[str, Any], required_permission: str = 'write') -> Dict[str, str]:
    """
    Validate authenticated context from API Gateway authorizer.
    """
    logger.info("=== AUTHENTICATION VALIDATION START ===")
    logger.info(f"Required permission: {required_permission}")
    
    auth_context = event.get('requestContext', {}).get('authorizer', {})
    
    logger.info(f"RequestContext keys: {list(event.get('requestContext', {}).keys())}")
    logger.info(f"Authorizer context keys: {list(auth_context.keys()) if auth_context else 'None'}")
    
    if not auth_context:
        logger.error("No authorizer context found in request")
        raise LeaderboardAuthenticationError("Authentication required - no authorizer context found")
    
    authenticated_studio_id = auth_context.get('studioId')
    authenticated_game_id = auth_context.get('gameId')
    studio_name = auth_context.get('studioName')
    game_title = auth_context.get('gameTitle')
    contact_email = auth_context.get('contactEmail')
    permissions = auth_context.get('permissions', '').split(',') if auth_context.get('permissions') else []
    
    if not authenticated_studio_id:
        logger.error("No authenticated studio ID found in authorizer context")
        raise LeaderboardAuthenticationError("Authentication required - invalid studio credentials")
    
    if not authenticated_game_id:
        logger.error("No authenticated game ID found in authorizer context")
        raise LeaderboardAuthenticationError("Authentication required - invalid game credentials")
    
    if required_permission not in permissions:
        logger.warning(f"Insufficient permissions for studio {authenticated_studio_id}: {permissions}")
        raise LeaderboardAuthenticationError(f"Insufficient permissions - {required_permission} access required")
    
    logger.info(f"Authentication validated for studio: {authenticated_studio_id}, game: {authenticated_game_id}")
    
    return {
        'studioId': authenticated_studio_id,
        'gameId': authenticated_game_id,
        'studioName': studio_name or '',
        'gameTitle': game_title or '',
        'contactEmail': contact_email or '',
        'permissions': permissions
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
    Get or create a high-performance Valkey client using GLIDE 2.0.1+ internal connection pooling.
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
                client_name="batch-leaderboard-lambda",
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
                client_name="batch-leaderboard-lambda",
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
            thread_name_prefix="batch-worker"
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
    
    # ENHANCED LOGGING FOR DEBUGGING
    logger.info(f"=== SCORE VALIDATION DEBUG ===")
    logger.info(f"Score value: {score_value} (type: {type(score_value)})")
    logger.info(f"Score type: {score_type}")
    logger.info(f"Leaderboard config: {json.dumps(leaderboard_config, default=str)}")
    
    # If not a time score, just validate as numeric
    if score_type != 'time':
        try:
            score = Decimal(str(score_value))
            # Normalize to remove trailing zeros (handles scientific notation properly)
            score = score.normalize()
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
# BATCH PROCESSING FUNCTIONS
# ============================================================================

@tracer.capture_method
def validate_batch_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enhanced request validation with comprehensive batch validation.
    """
    if 'body' not in event:
        raise ValueError("Request body is missing")
    
    # Log the actual size of the request body received
    body_content = event['body']
    body_size = len(body_content) if isinstance(body_content, str) else len(str(body_content))
    logger.info(f"Request body size received: {body_size} characters")
    
    try:
        body = json.loads(body_content) if isinstance(body_content, str) else body_content
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error at position {e.pos}: {e.msg}")
        logger.error(f"Body content around error (last 200 chars): ...{body_content[-200:] if isinstance(body_content, str) else str(body_content)[-200:]}")
        raise ValueError("Invalid JSON in request body")
    
    if 'batchGameReportBody' not in body:
        raise ValueError("batchGameReportBody is missing in request body")
    
    batch_report = body['batchGameReportBody']
    
    if 'gameReports' not in batch_report or not isinstance(batch_report['gameReports'], list):
        raise ValueError("gameReports must be a non-empty array in batchGameReportBody")
    
    if not batch_report['gameReports']:
        raise ValueError("gameReports array cannot be empty")
    
    if len(batch_report['gameReports']) > MAX_ITEMS_PER_REQUEST:
        raise ValueError(f"Too many items in batch. Maximum allowed: {MAX_ITEMS_PER_REQUEST}")
    
    seen_combinations = set()
    seen_players = set()  # Track duplicate playerIDs
    leaderboard_names = set()
    
    for i, report in enumerate(batch_report['gameReports']):
        # Validate required fields
        required_fields = ["playerID", "gameID", "gameMode", "playerScore", "leaderboardName", "fullRawGameReport"]
        
        for field in required_fields:
            if field not in report:
                raise ValueError(f"Required field '{field}' is missing in gameReport at index {i}")
        
        # Validate playerID
        player_id = report['playerID']
        if not isinstance(player_id, str) or not player_id.strip():
            raise ValueError(f"playerID must be a non-empty string in gameReport at index {i}")
        
        if not re.match(r'^[a-zA-Z0-9_-]+$', player_id):
            raise ValueError(f"playerID contains invalid characters in gameReport at index {i}")
        
        # Check for duplicate playerIDs - STRICT VALIDATION
        if player_id in seen_players:
            logger.error(f"=== DUPLICATE PLAYER DETECTION ===")
            logger.error(f"Duplicate playerID '{player_id}' found at index {i}")
            logger.error(f"This playerID was already seen in the current batch")
            raise DuplicatePlayerError(f"Duplicate playerID '{player_id}' detected in batch at index {i}. Each player can only appear once per batch request, regardless of leaderboard or game mode.")
        
        seen_players.add(player_id)
        
        # Validate gameID
        game_id = report['gameID']
        if not isinstance(game_id, str) or not game_id.strip():
            raise ValueError(f"gameID must be a non-empty string in gameReport at index {i}")
        
        # Validate gameMode
        game_mode = report['gameMode']
        if not isinstance(game_mode, str) or not game_mode.strip():
            raise ValueError(f"gameMode must be a non-empty string in gameReport at index {i}")
        
        # Validate leaderboardName
        leaderboard_name = report['leaderboardName']
        if not isinstance(leaderboard_name, str) or not leaderboard_name.strip():
            raise ValueError(f"leaderboardName must be a non-empty string in gameReport at index {i}")
        
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', leaderboard_name):
            raise ValueError(f"leaderboardName must start with a letter or number and contain only letters, numbers, hyphens, and underscores in gameReport at index {i}")
        
        leaderboard_names.add(leaderboard_name)
        
        # Store original score for later validation
        report['_originalPlayerScore'] = report['playerScore']
        
        # Validate fullRawGameReport
        if not isinstance(report['fullRawGameReport'], dict):
            raise ValueError(f"fullRawGameReport must be a JSON object in gameReport at index {i}")
        
        # Remove scoreStrategy from report if present (should come from leaderboard config)
        if 'scoreStrategy' in report:
            logger.warning(f"scoreStrategy in gameReport at index {i} will be ignored - using leaderboard config value")
            del report['scoreStrategy']
        
        # Check for duplicate player-leaderboard combinations
        combination = (player_id, leaderboard_name)
        if combination in seen_combinations:
            logger.warning(f"Duplicate player-leaderboard combination found: {combination}")
        seen_combinations.add(combination)
    
    return {
        'batchGameReportBody': batch_report,
        'leaderboardNames': list(leaderboard_names),
        'totalItems': len(batch_report['gameReports']),
        'jobId': str(uuid.uuid4())
    }


def generate_config_cache_key(leaderboard_name: str) -> str:
    """Generate cache key for leaderboard configuration."""
    return f"config:{leaderboard_name}"


@tracer.capture_method
async def get_leaderboard_configs_batch(leaderboard_names: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Get multiple leaderboard configurations with caching.
    """
    global config_cache
    current_time = time.time()
    
    configs = {}
    missing_names = []
    
    # Check in-memory cache first
    for name in leaderboard_names:
        if name in config_cache:
            config, timestamp = config_cache[name]
            if current_time - timestamp < CONFIG_CACHE_TTL:
                configs[name] = config
                logger.debug(f"Retrieved config from memory cache: {name}")
            else:
                del config_cache[name]
                missing_names.append(name)
        else:
            missing_names.append(name)
    
    if not missing_names:
        return configs
    
    # Try Valkey cache for missing configs
    try:
        client = await get_valkey_client()
        cache_tasks = []
        
        for name in missing_names:
            cache_key = generate_config_cache_key(name)
            cache_tasks.append(client.get(cache_key))
        
        cache_results = await asyncio.gather(*cache_tasks, return_exceptions=True)
        
        still_missing = []
        for i, result in enumerate(cache_results):
            name = missing_names[i]
            if not isinstance(result, Exception) and result:
                try:
                    config = json.loads(result.decode() if isinstance(result, bytes) else result)
                    configs[name] = config
                    config_cache[name] = (config, current_time)
                    logger.debug(f"Retrieved config from Valkey cache: {name}")
                except (json.JSONDecodeError, AttributeError):
                    still_missing.append(name)
            else:
                still_missing.append(name)
        
        missing_names = still_missing
        
    except Exception as e:
        logger.warning(f"Failed to retrieve configs from Valkey cache: {str(e)}")
    
    # Fallback to DynamoDB for remaining missing configs
    if missing_names:
        try:
            loop = asyncio.get_event_loop()
            
            # Batch get from DynamoDB
            def get_configs_from_dynamodb():
                response = dynamodb.batch_get_item(
                    RequestItems={
                        LEADERBOARDS_CONFIG_TABLE_NAME: {
                            'Keys': [{'leaderboardName': name} for name in missing_names],
                            'ConsistentRead': True
                        }
                    }
                )
                return response.get('Responses', {}).get(LEADERBOARDS_CONFIG_TABLE_NAME, [])
            
            items = await loop.run_in_executor(get_thread_pool(), get_configs_from_dynamodb)
            
            # Process results
            found_configs = {}
            for item in items:
                name = item['leaderboardName']
                configs[name] = item
                found_configs[name] = item
                config_cache[name] = (item, current_time)
            
            # Cache in Valkey
            if found_configs:
                try:
                    client = await get_valkey_client()
                    cache_tasks = []
                    
                    for name, config in found_configs.items():
                        cache_key = generate_config_cache_key(name)
                        cache_tasks.append(
                            client.set(
                                cache_key, 
                                json.dumps(config, default=decimal_serializer),
                                expiry=ExpirySet(ExpiryType.SEC, CONFIG_CACHE_TTL)
                            )
                        )
                    
                    await asyncio.gather(*cache_tasks, return_exceptions=True)
                    logger.info(f"Cached {len(found_configs)} configs in Valkey")
                    
                except Exception as e:
                    logger.warning(f"Failed to cache configs in Valkey: {str(e)}")
            
            # Check for missing leaderboards
            found_names = {item['leaderboardName'] for item in items}
            missing_leaderboards = [name for name in missing_names if name not in found_names]
            
            if missing_leaderboards:
                raise LeaderboardNotFoundError(f"Leaderboards not found: {', '.join(missing_leaderboards)}")
            
        except ClientError as e:
            logger.error(f"Error retrieving leaderboard configurations: {str(e)}")
            raise
    
    return configs


@tracer.capture_method
async def store_game_stats_batch(game_reports: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Optimized batch storage with parallel processing.
    """
    results = []
    current_timestamp = int(time.time())
    current_datetime = datetime.fromtimestamp(current_timestamp, timezone.utc).isoformat()
    
    # Prepare all items first
    prepared_batches = []
    for i in range(0, len(game_reports), MAX_BATCH_SIZE):
        batch = game_reports[i:i+MAX_BATCH_SIZE]
        batch_items = []
        
        for j, report in enumerate(batch):
            # Millisecond precision sortKey to prevent overwrites within same second
            precise_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]
            sort_key = f"{report['gameID']}#{report['gameMode']}#{precise_time}+00:00"
            
            item = {
                'playerID': report['playerID'],
                'sortKey': sort_key,
                'gameID': report['gameID'],
                'gameMode': report['gameMode'],
                'playerScore': convert_floats_to_decimal(report['playerScore']),
                'leaderboardName': report['leaderboardName'],
                'fullRawGameReport': convert_floats_to_decimal(report['fullRawGameReport']),
                'timestamp': current_timestamp,
                'timestampISO': current_datetime,
                'scoreStrategy': 'best'  # Default, will be overridden by leaderboard config
            }
            
            # Add additional fields
            for key, value in report.items():
                if key not in item and not key.startswith('_'):
                    item[key] = convert_floats_to_decimal(value)
            
            batch_items.append({
                'item': item,
                'originalIndex': i + j,
                'success': False
            })
        
        prepared_batches.append(batch_items)
    
    # Process batches in parallel
    async def process_batch(batch_items):
        try:
            with game_stats_table.batch_writer() as batch:
                for batch_item in batch_items:
                    batch.put_item(Item=batch_item['item'])
            
            for batch_item in batch_items:
                batch_item['success'] = True
                
            return batch_items
            
        except Exception as e:
            logger.error(f"=== DYNAMODB BATCH WRITE FAILURE ===")
            logger.error(f"Batch write failed: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Batch size: {len(batch_items)}")
            
            # Log details of failed items
            for idx, batch_item in enumerate(batch_items):
                logger.error(f"Failed item {idx}: playerID={batch_item['item'].get('playerID')}, leaderboard={batch_item['item'].get('leaderboardName')}")
                batch_item['error'] = str(e)
                batch_item['success'] = False
            return batch_items
    
    # Execute all batches concurrently
    loop = asyncio.get_event_loop()
    batch_tasks = []
    
    for batch_items in prepared_batches:
        task = loop.run_in_executor(get_thread_pool(), lambda b=batch_items: run_async(process_batch(b)))
        batch_tasks.append(task)
    
    # Wait for all batches to complete
    batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
    
    # Flatten results
    for batch_result in batch_results:
        if isinstance(batch_result, Exception):
            logger.error(f"Batch processing failed: {str(batch_result)}")
        else:
            results.extend(batch_result)
    
    return results


@tracer.capture_method
async def update_leaderboards_batch(
    client: Union[GlideClusterClient, GlideClient],
    leaderboard_updates: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Optimized batch leaderboard updates with pipeline operations.
    """
    results = []
    
    # Group updates by leaderboard
    leaderboard_groups = defaultdict(list)
    for update in leaderboard_updates:
        sorted_list_name = update['sortedListName']
        leaderboard_groups[sorted_list_name].append(update)
    
    # Process each leaderboard group
    for sorted_list_name, updates in leaderboard_groups.items():
        try:
            # Remove any init placeholder when first real scores are submitted
            await remove_init_placeholder_if_exists(client, sorted_list_name)
            
            # Get leaderboard configuration for time formatting
            sample_update = updates[0]
            leaderboard_config = sample_update.get('_leaderboardConfig', {})
            score_type = leaderboard_config.get('scoreType', 'score')
            time_precision = leaderboard_config.get('timePrecision', 3)
            
            # Process updates
            for update in updates:
                try:
                    player_id = update['playerID']
                    player_score = float(update['playerScore'])
                    leaderboard_type = update['leaderboardType']
                    score_strategy = update['scoreStrategy']
                    
                    # Handle score strategy
                    if score_strategy == "replace":
                        final_score = player_score
                        if leaderboard_type == "ASCENDING_LB":
                            # For ASCENDING_LB, store as negative for proper sorting
                            # If score is already negative, make it positive first, then negative
                            final_score = -abs(player_score)
                        
                        await client.zadd(sorted_list_name, {player_id: final_score})
                        
                        if score_type == 'time':
                            logger.debug(f"Replacing time for {player_id}: {player_score:.{time_precision}f}s")
                        
                    elif score_strategy == "cumulative":
                        if leaderboard_type == "ASCENDING_LB":
                            # For ASCENDING_LB, cumulative means ADD times together (true cumulative)
                            # Use case: total time across multiple sessions/races
                            current_score = await client.zscore(sorted_list_name, player_id)
                            if current_score is not None:
                                current_actual = -float(current_score)
                                new_total = current_actual + abs(player_score)  # Use abs to handle negative inputs
                                await client.zadd(sorted_list_name, {player_id: -new_total})
                            else:
                                await client.zadd(sorted_list_name, {player_id: -abs(player_score)})
                        else:
                            # Use atomic increment for scores
                            await client.zincrby(sorted_list_name, player_score, player_id)
                            
                    elif score_strategy == "best":
                        # Get current score first
                        current_score = await client.zscore(sorted_list_name, player_id)
                        
                        should_update = True
                        final_score = player_score
                        
                        if current_score is not None:
                            current_score = float(current_score)
                            
                            if leaderboard_type == "DESCENDING_LB":
                                should_update = player_score > current_score
                                final_score = max(current_score, player_score)
                            else:  # ASCENDING_LB
                                current_actual = -current_score
                                player_abs = abs(player_score)  # Use absolute value for comparison
                                should_update = player_abs < current_actual
                                final_score = -min(current_actual, player_abs)
                        elif leaderboard_type == "ASCENDING_LB":
                            final_score = -abs(player_score)  # Use absolute value
                        
                        if should_update:
                            await client.zadd(sorted_list_name, {player_id: final_score})
                            if score_type == 'time':
                                actual_score = -final_score if leaderboard_type == "ASCENDING_LB" else final_score
                                logger.debug(f"Best time for {player_id}: {actual_score:.{time_precision}f}s")
                    
                    results.append({
                        'success': True,
                        'leaderboard': sorted_list_name,
                        'playerID': player_id,
                        'scoreType': score_type
                    })
                    
                except Exception as e:
                    logger.error(f"=== MEMORYDB UPDATE FAILURE ===")
                    logger.error(f"Failed to update leaderboard for player {update.get('playerID')}: {str(e)}")
                    logger.error(f"Exception type: {type(e).__name__}")
                    logger.error(f"Leaderboard: {sorted_list_name}")
                    logger.error(f"Player: {update.get('playerID')}")
                    logger.error(f"Score: {update.get('playerScore')}")
                    logger.error(f"Strategy: {update.get('scoreStrategy')}")
                    results.append({
                        'success': False,
                        'leaderboard': sorted_list_name,
                        'playerID': update.get('playerID'),
                        'error': str(e),
                        'scoreType': score_type
                    })
            
            # Set TTL if specified (only once per leaderboard)
            if updates[0].get('ttl'):
                try:
                    await client.expire(sorted_list_name, updates[0]['ttl'])
                except Exception as e:
                    logger.warning(f"Failed to set TTL: {str(e)}")
                    
        except Exception as e:
            logger.error(f"Leaderboard group processing failed for {sorted_list_name}: {str(e)}")
            for update in updates:
                results.append({
                    'success': False,
                    'leaderboard': sorted_list_name,
                    'playerID': update.get('playerID'),
                    'error': str(e),
                    'scoreType': leaderboard_config.get('scoreType', 'score')
                })
    
    return results


async def process_batch_request(request_data: Dict[str, Any], auth_context: Dict[str, str]) -> Dict[str, Any]:
    """
    Main async processing function for batch requests.
    """
    # Get leaderboard configurations
    logger.info(f"=== BATCH PROCESSING DEBUG ===")
    logger.info(f"Leaderboard names to fetch: {request_data['leaderboardNames']}")
    
    leaderboard_configs = await get_leaderboard_configs_batch(request_data['leaderboardNames'])
    
    logger.info(f"Retrieved leaderboard configs: {json.dumps(leaderboard_configs, default=str)}")
    
    game_reports = request_data['batchGameReportBody']['gameReports']
    validated_reports = []
    validation_errors = []
    
    # Validate all game reports
    for i, report in enumerate(game_reports):
        try:
            leaderboard_name = report['leaderboardName']
            logger.info(f"Processing report {i}: leaderboard={leaderboard_name}, score={report.get('_originalPlayerScore')}")
            
            if leaderboard_name not in leaderboard_configs:
                raise ValueError(f"Leaderboard configuration not found: {leaderboard_name}")
            
            leaderboard_config = leaderboard_configs[leaderboard_name]
            logger.info(f"Using config for {leaderboard_name}: {json.dumps(leaderboard_config, default=str)}")
            
            # Parse and validate score
            validated_score = parse_and_validate_time_score(
                report['_originalPlayerScore'], 
                leaderboard_config
            )
            report['playerScore'] = validated_score
            
            logger.info(f"Score validation passed for report {i}: {report['_originalPlayerScore']} -> {validated_score}")
            
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
            if report['gameID'] != leaderboard_config['gameID']:
                raise ValueError(f"gameID mismatch: {report['gameID']} != {leaderboard_config['gameID']}")
            
            if report['gameMode'] != leaderboard_config['gameMode']:
                raise ValueError(f"gameMode mismatch: {report['gameMode']} != {leaderboard_config['gameMode']}")
            
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
            
            # Add config to report for later use
            report['_leaderboardConfig'] = leaderboard_config
            validated_reports.append(report)
            
        except Exception as e:
            logger.error(f"=== VALIDATION ERROR DEBUG ===")
            logger.error(f"Report index: {i}")
            logger.error(f"Report data: {json.dumps(report, default=str)}")
            logger.error(f"Leaderboard config: {json.dumps(leaderboard_config, default=str) if 'leaderboard_config' in locals() else 'Not loaded'}")
            logger.error(f"Validation failed for report {i}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            validation_errors.append({
                'index': i,
                'error': str(e),
                'playerID': report.get('playerID', 'unknown'),
                'leaderboardName': report.get('leaderboardName', 'unknown'),
                'originalScore': report.get('_originalPlayerScore', 'unknown')
            })
    
    logger.info(f"Validation complete: {len(validated_reports)} valid, {len(validation_errors)} errors")
    
    # Check if any validation errors were due to expired leaderboards
    expired_errors = [error for error in validation_errors if 'expired and is in read-only mode' in error.get('error', '') or 'expired and is no longer accepting writes' in error.get('error', '')]
    if expired_errors:
        # If any expired leaderboards detected, raise LeaderboardExpiredError for HTTP 423
        logger.error(f"Expired leaderboards detected: {len(expired_errors)} out of {len(validation_errors)} errors")
        raise LeaderboardExpiredError(expired_errors[0]['error'])
    
    if not validated_reports:
        logger.error("No valid reports after validation - raising error")
        raise ValueError("No valid reports to process after validation")
    
    # Store game stats in DynamoDB
    logger.info("=== STARTING DYNAMODB BATCH WRITE ===")
    stats_results = await store_game_stats_batch(validated_reports)
    
    # Check DynamoDB write results
    dynamodb_success_count = sum(1 for result in stats_results if result.get('success', False))
    dynamodb_error_count = sum(1 for result in stats_results if result.get('error'))
    
    logger.info(f"=== DYNAMODB WRITE RESULTS ===")
    logger.info(f"Total records: {len(stats_results)}")
    logger.info(f"Successful writes: {dynamodb_success_count}")
    logger.info(f"Failed writes: {dynamodb_error_count}")
    
    if dynamodb_error_count > 0:
        logger.error("=== DYNAMODB WRITE FAILURES DETECTED ===")
        for i, result in enumerate(stats_results):
            if result.get('error'):
                logger.error(f"DynamoDB write failed for record {i}: {result.get('error')}")
                logger.error(f"Failed record data: {json.dumps(result, default=str)}")
    
    # Only proceed to MemoryDB if DynamoDB writes were successful
    if dynamodb_error_count > 0:
        logger.error(f"=== ABORTING MEMORYDB WRITES DUE TO DYNAMODB FAILURES ===")
        logger.error(f"Cannot proceed with MemoryDB updates when DynamoDB writes failed")
        raise ValueError(f"DynamoDB batch write failed for {dynamodb_error_count} records. Aborting to prevent data inconsistency.")
    
    logger.info("=== DYNAMODB WRITES SUCCESSFUL - PROCEEDING TO MEMORYDB ===")
    
    # Get Valkey client
    client = await get_valkey_client()
    
    # Prepare leaderboard updates
    leaderboard_updates = []
    for report in validated_reports:
        config = report['_leaderboardConfig']
        # Use validated score_strategy from config validation
        validated_score_strategy = config.get('scoreStrategy', 'best')
        leaderboard_updates.append({
            'sortedListName': config['sortedListName'],
            'playerID': report['playerID'],
            'playerScore': report['playerScore'],
            'leaderboardType': config['leaderboardType'],
            'scoreStrategy': validated_score_strategy,
            'ttl': None if config.get('optionalLBReadOnlyOnExpiry', True) else calculate_ttl_from_expiry(config.get('optionalLBExpiryDateTimeStamp')),
            '_leaderboardConfig': config
        })
    
    # Update leaderboards
    logger.info("=== STARTING MEMORYDB BATCH WRITE ===")
    logger.info(f"MemoryDB updates to process: {len(leaderboard_updates)}")
    leaderboard_results = await update_leaderboards_batch(client, leaderboard_updates)
    
    # Check MemoryDB write results
    memorydb_success_count = sum(1 for result in leaderboard_results if result.get('success', False))
    memorydb_error_count = sum(1 for result in leaderboard_results if result.get('error'))
    
    logger.info(f"=== MEMORYDB WRITE RESULTS ===")
    logger.info(f"Total updates: {len(leaderboard_results)}")
    logger.info(f"Successful updates: {memorydb_success_count}")
    logger.info(f"Failed updates: {memorydb_error_count}")
    
    if memorydb_error_count > 0:
        logger.error("=== MEMORYDB WRITE FAILURES DETECTED ===")
        for i, result in enumerate(leaderboard_results):
            if result.get('error'):
                logger.error(f"MemoryDB write failed for update {i}: {result.get('error')}")
                logger.error(f"Failed update data: {json.dumps(result, default=str)}")
    
    logger.info("=== BATCH PROCESSING COMPLETE ===")
    logger.info(f"Final summary - DynamoDB: {dynamodb_success_count}/{len(stats_results)} success, MemoryDB: {memorydb_success_count}/{len(leaderboard_results)} success")
    
    return {
        'stats_results': stats_results,
        'leaderboard_results': leaderboard_results,
        'validation_errors': validation_errors,
        'total_processed': len(validated_reports)
    }


# ============================================================================
# MAIN LAMBDA HANDLER
# ============================================================================

@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
@handle_errors
@log_execution
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    High-performance Lambda handler for batch storing game stats and updating leaderboards.
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
            'body': json.dumps({'batchGameReportResponse': {
                'success': False,
                'error': 'Service temporarily unavailable - Valkey dependency not found',
                'message': 'The leaderboard service is currently unavailable due to a dependency issue'
            }})
        }
    
    # Validate authentication
    auth_context = validate_authenticated_context(event, 'write')
    logger.info(f"Batch operation for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
    
    # Validate AWS resources
    validate_aws_resources()
    
    # Validate request
    request_data = validate_batch_request(event)
    
    # Execute async processing
    results = run_async(process_batch_request(request_data, auth_context))
    
    # Calculate processing time and statistics
    total_time = time.perf_counter() - start_time
    
    # Analyze results
    stats_success_count = sum(1 for r in results['stats_results'] if r.get('success', False))
    stats_failure_count = len(results['stats_results']) - stats_success_count
    
    leaderboard_success_count = sum(1 for r in results['leaderboard_results'] if r.get('success', False))
    leaderboard_failure_count = len(results['leaderboard_results']) - leaderboard_success_count
    
    validation_error_count = len(results['validation_errors'])
    
    # Count time-based vs regular score updates
    time_based_updates = sum(1 for r in results['leaderboard_results'] 
                           if r.get('scoreType') == 'time' and r.get('success', False))
    
    # Build response with wrapper
    response_data = {
        'batchGameReportResponse': {
            'message': 'Batch processing completed',
            'jobId': request_data['jobId'],
            'summary': {
                'totalItems': request_data['totalItems'],
                'processedItems': results['total_processed'],
                'validationErrors': validation_error_count,
                'statsStorage': {
                    'successful': stats_success_count,
                    'failed': stats_failure_count
                },
                'leaderboardUpdates': {
                    'successful': leaderboard_success_count,
                    'failed': leaderboard_failure_count,
                    'timeBasedUpdates': time_based_updates
                }
            },
            'performance': {
                'processingTimeMs': int(total_time * 1000),
                'itemsPerSecond': round(results['total_processed'] / total_time, 2) if total_time > 0 else 0
            },
            'metadata': {
                'studioId': auth_context['studioId'],
                'gameId': auth_context['gameId'],
                'requestId': context.aws_request_id,
                'timestamp': datetime.now(timezone.utc).isoformat()
            },
            'success': True
        }
    }
    
    # Add errors if any
    if results['validation_errors']:
        response_data['batchGameReportResponse']['errors'] = results['validation_errors']
    
    # COMPLETION LOGGING
    logger.info(f"=== LAMBDA HANDLER COMPLETION ===")
    logger.info(f"Processing time: {int(total_time * 1000)}ms")
    logger.info(f"Items processed: {results['total_processed']}/{request_data['totalItems']}")
    
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Cache-Control': 'no-cache'
        },
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