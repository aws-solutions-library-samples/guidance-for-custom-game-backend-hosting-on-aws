# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
rebuildLeaderboard.py -- Rebuild Leaderboard from Game Stats Lambda Function

Rebuilds leaderboards from stored game statistics in DynamoDB:
- Validates rebuild requests with studio authentication
- Creates time-limited backups before rebuild
- Clears and rebuilds leaderboard from DynamoDB stats
- Supports continuation for long-running operations
- Handles multiple score strategies and time-based scores

Updated for Python 3.13 and Valkey-GLIDE 2.0.1
High-performance, robust implementation with Lambda relay capability.
"""

import os
import sys
import json
import time
import uuid
import asyncio
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

# Rebuild-specific settings
MAX_BATCH_SIZE = int(os.environ.get('REBUILD_BATCH_SIZE', '500'))
MAX_EXECUTION_TIME = int(os.environ.get('MAX_EXECUTION_TIME', '780'))  # 13 minutes
TIME_BUFFER = int(os.environ.get('TIME_BUFFER', '60'))  # 1 minute buffer
ENABLE_PAUSE_UPDATES = os.environ.get('ENABLE_PAUSE_UPDATES', 'true').lower() == 'true'
BACKUP_BEFORE_REBUILD = os.environ.get('BACKUP_BEFORE_REBUILD', 'true').lower() == 'true'
BACKUP_TTL_HOURS = int(os.environ.get('BACKUP_TTL_HOURS', '24'))

# Performance optimization settings
MAX_CONCURRENT_OPERATIONS = int(os.environ.get('MAX_CONCURRENT_OPERATIONS', '10'))
CONNECTION_TIMEOUT = int(os.environ.get('GLIDE_CONNECTION_TIMEOUT_MS', '2000'))
REQUEST_TIMEOUT = int(os.environ.get('GLIDE_REQUEST_TIMEOUT_MS', '5000'))

# Initialize AWS clients
secretsmanager = boto3.client('secretsmanager', config=boto3.session.Config(
    max_pool_connections=10,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
))

dynamodb = boto3.resource('dynamodb', config=boto3.session.Config(
    max_pool_connections=50,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
))

lambda_client = boto3.client('lambda', config=boto3.session.Config(
    max_pool_connections=10,
    retries={'max_attempts': 2, 'mode': 'adaptive'}
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
VALID_SCORE_TYPES = ["score", "time", "distance", "points", "rank", "level"]
VALID_TIME_FORMATS = ["seconds", "milliseconds", "minutes_seconds", "hours_minutes_seconds"]
REBUILD_LOCK_PREFIX = "rebuild_lock"
PAUSE_LOCK_PREFIX = "pause_lock"

# Init placeholder keys for removal (must match leaderboardsConfig.py)
INIT_PLACEHOLDER_KEYS = [
    "_init_topscore_", "_init_time_", "_init_distance_", 
    "_init_points_", "_init_rank_", "_init_level_"
]

# Score type-specific initialization configuration
INIT_CONFIG = {
    "score": {"key": "_init_topscore_", "value": -999999.0},
    "time": {"key": "_init_time_", "value": 999999999.0},
    "distance": {"key": "_init_distance_", "value": -1.0},
    "points": {"key": "_init_points_", "value": -999999.0},
    "rank": {"key": "_init_rank_", "value": 999999999.0},
    "level": {"key": "_init_level_", "value": -1.0}
}

# Global resources with connection health tracking
valkey_client: Optional[Union[GlideClusterClient, GlideClient]] = None
client_created_at: Optional[float] = None
thread_pool: Optional[ThreadPoolExecutor] = None
CLIENT_MAX_AGE = 300  # 5 minutes - refresh connections periodically


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
            if 'rebuildLeaderboardRequest' in params:
                if 'continuationToken' in params['rebuildLeaderboardRequest']:
                    params['rebuildLeaderboardRequest']['continuationToken'] = "[REDACTED]"
        
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


class RebuildInProgressError(Exception):
    """Custom exception for concurrent rebuild attempts"""
    pass


class LeaderboardExpiredError(Exception):
    """Custom exception for operations on expired leaderboards"""
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
                'body': json.dumps({'rebuildLeaderboardResponse': {
                    'error': 'Not Found',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except RebuildInProgressError as e:
            logger.error(f"Rebuild conflict: {str(e)}")
            return {
                'statusCode': 409,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'rebuildLeaderboardResponse': {
                    'error': 'Conflict',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except LeaderboardExpiredError as e:
            logger.error(f"Leaderboard expired: {str(e)}")
            error_msg = str(e)
            if "read-only" in error_msg:
                return {
                    'statusCode': 423,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({'rebuildLeaderboardResponse': {
                        'error': 'Leaderboard Locked',
                        'errorCode': 'LEADERBOARD_EXPIRED_READONLY',
                        'message': 'Cannot rebuild an expired leaderboard in read-only mode',
                        'details': error_msg,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }})
                }
            else:
                return {
                    'statusCode': 423,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({'rebuildLeaderboardResponse': {
                        'error': 'Leaderboard Expired',
                        'errorCode': 'LEADERBOARD_EXPIRED_AUTODELETE',
                        'message': 'Cannot rebuild an expired leaderboard scheduled for deletion',
                        'details': error_msg,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }})
                }
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'rebuildLeaderboardResponse': {
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
                'body': json.dumps({'rebuildLeaderboardResponse': {
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
                'body': json.dumps({'rebuildLeaderboardResponse': {
                    'error': error_code,
                    'message': error_message,
                    'note': 'Rebuild operation may be partially complete',
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
                'body': json.dumps({'rebuildLeaderboardResponse': {
                    'error': 'Service Unavailable',
                    'message': 'Database connection error',
                    'note': 'Rebuild operation may be partially complete',
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
                'body': json.dumps({'rebuildLeaderboardResponse': {
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
    Backend rebuild operations require write permissions.
    
    Args:
        event: Lambda event containing authorizer context
        required_permission: Required permission level ('write' for rebuild)
        
    Returns:
        Dictionary containing authenticated context fields
        
    Raises:
        LeaderboardAuthenticationError: If authentication validation fails
    """
    logger.info("=== AUTHENTICATION VALIDATION START ===")
    logger.info(f"Required permission: {required_permission}")
    
    auth_context = event.get('requestContext', {}).get('authorizer', {})
    
    logger.info(f"RequestContext keys: {list(event.get('requestContext', {}).keys())}")
    logger.info(f"Authorizer context keys: {list(auth_context.keys()) if auth_context else 'None'}")
    
    if not auth_context:
        logger.error("No authorizer context found in request")
        logger.error(f"Full requestContext: {event.get('requestContext', {})}")
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
    Retrieve Valkey/MemoryDB credentials and configuration from AWS Secrets Manager.
    Uses the unified secret approach with fallback to individual secrets.
    
    Returns:
        Dictionary containing endpoint, port, username, and password
        
    Raises:
        ValueError: If required environment variables are not set
        ClientError: If there's an issue retrieving secrets
    """
    credentials_config = {}
    
    # Get endpoint from environment variable first
    endpoint = os.environ.get('MEMORYDB_CLUSTER_ENDPOINT')
    if not endpoint:
        endpoint = os.environ.get('VALKEY_CLUSTER_ENDPOINT') or os.environ.get('MEMORYDB_ENDPOINT')
        
    if not endpoint:
        memorydb_cluster_name = os.environ.get('gameLeaderboardsMemoryDBName')
        if memorydb_cluster_name:
            try:
                secret_id = f"{memorydb_cluster_name}-endpoint"
                logger.debug(f"Retrieving endpoint secret: {secret_id}")
                response = secretsmanager.get_secret_value(SecretId=secret_id)
                
                try:
                    secret_data = json.loads(response['SecretString'])
                    endpoint = secret_data.get('endpoint') or list(secret_data.values())[0]
                except json.JSONDecodeError:
                    endpoint = response['SecretString']
                    
            except ClientError as e:
                if e.response['Error']['Code'] != 'ResourceNotFoundException':
                    logger.error(f"Error retrieving endpoint secret: {str(e)}")
                    raise ValueError(f"Failed to retrieve endpoint configuration: {str(e)}")
    
    if not endpoint:
        raise ValueError("Required environment variable MEMORYDB_CLUSTER_ENDPOINT is not set and not found in Secrets Manager")
    
    credentials_config['endpoint'] = endpoint
    logger.info(f"Using MemoryDB endpoint: {endpoint}")
    
    # Get port
    port = os.environ.get('MEMORYDB_PORT', '6379')
    memorydb_cluster_name = os.environ.get('gameLeaderboardsMemoryDBName')
    
    if memorydb_cluster_name:
        try:
            secret_id = f"{memorydb_cluster_name}-port"
            logger.debug(f"Retrieving port secret: {secret_id}")
            response = secretsmanager.get_secret_value(SecretId=secret_id)
            
            try:
                secret_data = json.loads(response['SecretString'])
                port = secret_data.get('port') or list(secret_data.values())[0]
            except json.JSONDecodeError:
                port = response['SecretString']
                
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                logger.info(f"Port secret not found, using environment variable or default: {port}")
            else:
                logger.warning(f"Error retrieving port secret: {str(e)}")
    
    try:
        credentials_config['port'] = int(port)
    except (ValueError, TypeError):
        logger.warning(f"Invalid port value '{port}', using default 6379")
        credentials_config['port'] = 6379
    
    # Get credentials from Secrets Manager
    secret_arn = os.environ.get('MEMORYDB_SECRET_ARN')
    if secret_arn:
        try:
            logger.info(f"Retrieving unified secret from: {secret_arn}")
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
                raise ValueError("Password not found in unified secret")
            
            password = str(password).strip()
            
            if '\n' in password or '\r' in password:
                logger.warning("Password contains newline characters, removing them")
                password = password.replace('\n', '').replace('\r', '')

            credentials_config['username'] = username
            credentials_config['password'] = password
            credentials_config['tls'] = secret_data.get('tls', True)

            logger.info(f"Successfully retrieved credentials from unified secret for user: {username}")
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'ResourceNotFoundException':
                logger.warning(f"Unified secret not found: {secret_arn}, trying individual secrets")
                secret_arn = None
            else:
                logger.error(f"AWS Secrets Manager error: {error_code} - {e.response['Error']['Message']}")
                raise ValueError(f"Failed to retrieve Valkey credentials: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse unified secret JSON: {str(e)}")
            raise ValueError(f"Invalid unified secret format: {str(e)}")
    
    # Fallback to individual secrets approach
    if not secret_arn and memorydb_cluster_name:
        secret_keys = {
            'username': f"{memorydb_cluster_name}-username",
            'password': f"{memorydb_cluster_name}-password"
        }
        
        try:
            for config_key, secret_id in secret_keys.items():
                try:
                    logger.debug(f"Retrieving individual secret: {secret_id}")
                    response = secretsmanager.get_secret_value(SecretId=secret_id)
                    
                    try:
                        secret_data = json.loads(response['SecretString'])
                        if config_key in secret_data:
                            credentials_config[config_key] = secret_data[config_key]
                        else:
                            credentials_config[config_key] = list(secret_data.values())[0]
                    except json.JSONDecodeError:
                        credentials_config[config_key] = response['SecretString']
                        
                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    if error_code == 'ResourceNotFoundException':
                        logger.warning(f"Individual secret {secret_id} not found, checking for fallback environment variable")
                        
                        fallback_env_vars = {
                            'username': 'VALKEY_USERNAME', 
                            'password': 'VALKEY_PASSWORD'
                        }
                        
                        if config_key in fallback_env_vars:
                            env_value = os.environ.get(fallback_env_vars[config_key])
                            if env_value:
                                credentials_config[config_key] = env_value
                                logger.info(f"Using fallback environment variable for {config_key}")
                            else:
                                logger.error(f"Neither individual secret {secret_id} nor environment variable {fallback_env_vars[config_key]} found")
                                raise ValueError(f"Missing required configuration: {config_key}")
                        else:
                            raise ValueError(f"Individual secret {secret_id} not found and no fallback available")
                    else:
                        logger.error(f"Error retrieving individual secret {secret_id}: {error_code} - {e.response['Error']['Message']}")
                        raise
                        
        except Exception as e:
            logger.error(f"Failed to retrieve individual secrets: {str(e)}")
            raise
    
    # Final fallback to environment variables
    if 'username' not in credentials_config:
        username = os.environ.get('VALKEY_USERNAME')
        if username:
            credentials_config['username'] = username
            logger.info("Using fallback environment variable for username")
        else:
            raise ValueError("Username not found in secrets or environment variables")
    
    if 'password' not in credentials_config:
        password = os.environ.get('VALKEY_PASSWORD')
        if password:
            credentials_config['password'] = password
            logger.info("Using fallback environment variable for password")
        else:
            raise ValueError("Password not found in secrets or environment variables")
    
    # Validate required fields
    required_fields = ['endpoint', 'username', 'password']
    for field in required_fields:
        if field not in credentials_config or not credentials_config[field]:
            raise ValueError(f"Missing or empty required field: {field}")
    
    # Validate credentials format
    if not credentials_config['username'] or not credentials_config['password']:
        raise ValueError(f"Invalid credentials retrieved")
    
    logger.info(f"Successfully retrieved Valkey configuration - Endpoint: {credentials_config['endpoint']}, Port: {credentials_config['port']}, Username: {credentials_config['username']}, TLS: {credentials_config.get('tls', True)}")
    
    return credentials_config


async def get_valkey_client() -> Union[GlideClusterClient, GlideClient]:
    """
    Initialize and return a high-performance Valkey client using GLIDE with Secrets Manager.
    Enhanced with connection health checking and age management for optimal performance.
    """
    global valkey_client, client_created_at
    
    # Check if we need a new client (health + age management)
    needs_new_client = (
        valkey_client is None or
        client_created_at is None or
        (time.time() - client_created_at) > CLIENT_MAX_AGE
    )
    
    # Quick health check for existing client
    if not needs_new_client and valkey_client is not None:
        try:
            await asyncio.wait_for(valkey_client.ping(), timeout=0.5)
            logger.debug("Reusing existing Valkey client connection")
            return valkey_client
        except:
            logger.info("Existing client unhealthy, creating new connection")
            needs_new_client = True
    
    if needs_new_client:
        logger.info("Creating optimized Valkey client")
        valkey_client = await _create_optimized_valkey_client()
        client_created_at = time.time()
    
    return valkey_client


async def _create_optimized_valkey_client() -> Union[GlideClusterClient, GlideClient]:
    """Create Valkey client with performance-optimized settings."""
    try:
        valkey_config = get_valkey_credentials_and_config()
        VALKEY_CLUSTER_ENDPOINT = valkey_config['endpoint']
        VALKEY_PORT = valkey_config['port']
        VALKEY_USERNAME = valkey_config['username']
        VALKEY_PASSWORD = valkey_config['password']
        VALKEY_USE_TLS = valkey_config.get('tls', True)
    except Exception as e:
        logger.error(f"Failed to retrieve Valkey configuration: {str(e)}")
        raise ValueError(f"Unable to retrieve Valkey configuration from Secrets Manager: {str(e)}")
    
    # Validate credentials
    if not VALKEY_USERNAME or not VALKEY_PASSWORD:
        raise ValueError(f"Invalid credentials")

    logger.info(f"Initializing Valkey connection - Endpoint: {VALKEY_CLUSTER_ENDPOINT}:{VALKEY_PORT}, Username: {VALKEY_USERNAME}, TLS: {VALKEY_USE_TLS}, ClusterMode: {VALKEY_CLUSTER_MODE}")
    
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
                client_name="leaderboard-rebuild-lambda",
                protocol=ProtocolVersion.RESP3
            )
            
            logger.info(f"Creating GLIDE cluster client with optimized settings...")            
            valkey_client = await GlideClusterClient.create(config)
            logger.info(f"Successfully connected to Valkey cluster")
            
        else:
            address = NodeAddress(VALKEY_CLUSTER_ENDPOINT, VALKEY_PORT)
            
            config = BaseClientConfiguration(
                addresses=[address],
                use_tls=VALKEY_USE_TLS,
                credentials=credentials,
                request_timeout=REQUEST_TIMEOUT,
                client_name="leaderboard-rebuild-lambda",
                protocol=ProtocolVersion.RESP3
            )

            logger.info("Creating GLIDE standalone client with optimized settings...")
            valkey_client = await GlideClient.create(config)
            logger.info("Successfully connected to Valkey standalone")
                
        # Test connection
        await valkey_client.ping()
        return valkey_client
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to connect to Valkey: {error_msg}")
        
        if "WRONGPASS" in error_msg or "invalid username-password" in error_msg:
            troubleshooting = (
                f"Authentication failed for user '{VALKEY_USERNAME}'. "
                f"Verify ACL user exists and is enabled in MemoryDB."
            )
            logger.error(troubleshooting)
        elif "Connection refused" in error_msg or "timeout" in error_msg.lower():
            troubleshooting = (
                f"Connection failed to {VALKEY_CLUSTER_ENDPOINT}:{VALKEY_PORT}. "
                f"Check Lambda is in correct VPC/subnets."
            )
            logger.error(troubleshooting)
        elif "TLS" in error_msg or "SSL" in error_msg:
            troubleshooting = (
                f"TLS/SSL error connecting to MemoryDB. "
                f"Current TLS setting: {VALKEY_USE_TLS}."
            )
            logger.error(troubleshooting)
        
        raise ConnectionError(f"Unable to connect to Valkey: {error_msg}")


def get_thread_pool() -> ThreadPoolExecutor:
    """
    Get or create a thread pool for concurrent operations.
    """
    global thread_pool
    if thread_pool is None:
        thread_pool = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_OPERATIONS,
            thread_name_prefix="leaderboard-rebuild-worker"
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


@tracer.capture_method
def parse_and_validate_time_score(
    score_value: Union[str, int, float], 
    leaderboard_config: Dict[str, Any]
) -> Decimal:
    """
    Parse and validate time-based scores with format conversion and validation.
    """
    score_type = leaderboard_config.get('scoreType', 'score')
    
    if score_type != 'time':
        try:
            score = Decimal(str(score_value))
            if score < 0:
                raise ValueError("Score must be non-negative")
            
            min_valid_score = leaderboard_config.get('minValidScore')
            max_valid_score = leaderboard_config.get('maxValidScore')
            
            if min_valid_score is not None and score < Decimal(str(min_valid_score)):
                raise ValueError(f"Score {score} is below minimum valid score {min_valid_score}")
            
            if max_valid_score is not None and score > Decimal(str(max_valid_score)):
                raise ValueError(f"Score {score} exceeds maximum valid score {max_valid_score}")
            
            return score
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Score must be a valid number")
    
    # Time-specific parsing and validation
    time_format = leaderboard_config.get('timeFormat', 'seconds')
    # DynamoDB returns Decimal; ensure time_precision is always int for arithmetic
    try:
        time_precision = int(leaderboard_config.get('timePrecision', 3))
    except (ValueError, TypeError):
        time_precision = 3
    min_valid_time = leaderboard_config.get('minValidTimeInSeconds', Decimal('0.001'))
    max_valid_time = leaderboard_config.get('maxValidTimeInSeconds', Decimal('86400.0'))
    
    # Convert to Decimal for comparison
    if not isinstance(min_valid_time, Decimal):
        min_valid_time = Decimal(str(min_valid_time))
    if not isinstance(max_valid_time, Decimal):
        max_valid_time = Decimal(str(max_valid_time))
    
    try:
        if isinstance(score_value, str):
            if time_format == "minutes_seconds":
                if ':' not in score_value:
                    raise ValueError("Time format must be MM:SS.mmm for minutes_seconds format")
                
                parts = score_value.split(':')
                if len(parts) != 2:
                    raise ValueError("Time format must be MM:SS.mmm")
                
                minutes = int(parts[0])
                seconds = float(parts[1])
                
                if minutes < 0 or seconds < 0 or seconds >= 60:
                    raise ValueError("Invalid time values: minutes >= 0, seconds < 60")
                
                total_seconds = minutes * 60 + seconds
                
            elif time_format == "hours_minutes_seconds":
                if score_value.count(':') != 2:
                    raise ValueError("Time format must be HH:MM:SS.mmm for hours_minutes_seconds format")
                
                parts = score_value.split(':')
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
                
                if hours < 0 or minutes < 0 or minutes >= 60 or seconds < 0 or seconds >= 60:
                    raise ValueError("Invalid time values: hours >= 0, minutes < 60, seconds < 60")
                
                total_seconds = hours * 3600 + minutes * 60 + seconds
                
            else:
                total_seconds = float(score_value)
                
                if time_format == "milliseconds":
                    total_seconds = total_seconds / 1000.0
        else:
            total_seconds = float(score_value)
            
            if time_format == "milliseconds":
                total_seconds = total_seconds / 1000.0
        
        total_seconds_decimal = Decimal(str(total_seconds))
        
        if total_seconds_decimal < min_valid_time:
            raise ValueError(f"Time {total_seconds:.{time_precision}f}s is below minimum valid time {min_valid_time}s")
        
        if total_seconds_decimal > max_valid_time:
            raise ValueError(f"Time {total_seconds:.{time_precision}f}s exceeds maximum valid time {max_valid_time}s")
        
        precision_factor = 10 ** time_precision
        rounded_seconds = round(total_seconds * precision_factor) / precision_factor
        
        logger.info(f"Parsed time: {score_value} -> {rounded_seconds:.{time_precision}f}s")
        return Decimal(str(rounded_seconds))
        
    except (ValueError, TypeError, OverflowError) as e:
        raise ValueError(f"Invalid time format '{score_value}': {str(e)}")


@tracer.capture_method
def validate_aws_resources() -> None:
    """
    Enhanced AWS resource validation with health checks.
    """
    if not LEADERBOARDS_CONFIG_TABLE_NAME:
        raise ValueError("Required environment variable gameLeaderboardsConfigTablename is not set")
    
    if not GAME_STATS_TABLE_NAME:
        raise ValueError("Required environment variable gameStatsAndScoresTablename is not set")
    
    try:
        valkey_config = get_valkey_credentials_and_config()
        logger.info("Successfully validated Valkey configuration from Secrets Manager")
    except Exception as e:
        raise ValueError(f"Failed to retrieve Valkey configuration from Secrets Manager: {str(e)}")
    
    try:
        config_table_desc = leaderboards_config_table.meta.client.describe_table(
            TableName=LEADERBOARDS_CONFIG_TABLE_NAME
        )
        stats_table_desc = game_stats_table.meta.client.describe_table(
            TableName=GAME_STATS_TABLE_NAME
        )
        
        if config_table_desc['Table']['TableStatus'] != 'ACTIVE':
            raise ValueError(f"Leaderboards config table is not active: {config_table_desc['Table']['TableStatus']}")
        
        if stats_table_desc['Table']['TableStatus'] != 'ACTIVE':
            raise ValueError(f"Game stats table is not active: {stats_table_desc['Table']['TableStatus']}")
        
        logger.info("DynamoDB tables validated successfully")
        
    except ClientError as e:
        logger.error(f"Error validating DynamoDB tables: {str(e)}")
        raise


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
def validate_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enhanced request validation with comprehensive checks.
    """
    # Check if this is a continuation of a previous execution
    if 'continuationToken' in event:
        logger.info("Processing continuation request")
        return event
    
    # Check if body exists
    if 'body' not in event:
        raise ValueError("Request body is missing")
    
    # Parse body
    try:
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in request body")
    
    # Check if rebuildLeaderboardRequest exists
    if 'rebuildLeaderboardRequest' not in body:
        raise ValueError("rebuildLeaderboardRequest is missing in request body")
    
    request_params = body['rebuildLeaderboardRequest']
    
    # Validate required fields
    if 'leaderboardName' not in request_params:
        raise ValueError("leaderboardName is missing in rebuildLeaderboardRequest")
    
    if not isinstance(request_params['leaderboardName'], str) or not request_params['leaderboardName'].strip():
        raise ValueError("leaderboardName must be a non-empty string")
    
    # Validate leaderboard name format
    leaderboard_name = request_params['leaderboardName'].strip()
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', leaderboard_name):
        raise ValueError("leaderboardName contains invalid characters")
    
    # Validate confirmation
    if 'confirmRebuild' in request_params:
        if not isinstance(request_params['confirmRebuild'], bool):
            raise ValueError("confirmRebuild must be a boolean")
    else:
        request_params['confirmRebuild'] = False
    
    # Validate scoreStrategy override (optional - will use config default if not provided)
    if 'scoreStrategy' in request_params:
        if request_params['scoreStrategy'] not in VALID_SCORE_STRATEGIES:
            raise ValueError(f"scoreStrategy must be one of {VALID_SCORE_STRATEGIES}")
    
    # Validate timestamps
    for timestamp_field in ['startTimestamp', 'endTimestamp']:
        if timestamp_field in request_params:
            try:
                timestamp_value = request_params[timestamp_field]
                if isinstance(timestamp_value, str) and not timestamp_value.isdigit():
                    # Parse as ISO format
                    dt = dateutil.parser.parse(timestamp_value)
                    if dt.tzinfo is not None:
                        dt = dt.astimezone(timezone.utc)
                    else:
                        dt = dt.replace(tzinfo=timezone.utc)
                    timestamp = int(dt.timestamp())
                else:
                    timestamp = int(float(timestamp_value))
                
                if timestamp < 0:
                    raise ValueError(f"{timestamp_field} must be a non-negative value")
                
                request_params[timestamp_field] = timestamp
                request_params[f"{timestamp_field}ISO"] = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
                
            except (ValueError, TypeError) as e:
                raise ValueError(f"{timestamp_field} must be a valid ISO datetime or Unix timestamp: {str(e)}")
    
    # Validate timestamp order
    if 'startTimestamp' in request_params and 'endTimestamp' in request_params:
        if request_params['startTimestamp'] >= request_params['endTimestamp']:
            raise ValueError("startTimestamp must be less than endTimestamp")
    
    return {
        'rebuildLeaderboardRequest': request_params,
        'processedItems': 0,
        'totalItems': 0,
        'lastEvaluatedKey': None,
        'startTime': int(time.time()),
        'jobId': str(uuid.uuid4()),
        'isComplete': False,
        'phase': 'initialization',
        'batchesProcessed': 0,
        'errors': []
    }


# ============================================================================
# LEADERBOARD OPERATIONS
# ============================================================================

@tracer.capture_method
async def get_leaderboard_config_async(leaderboard_name: str) -> Dict[str, Any]:
    """
    Asynchronously retrieve leaderboard configuration from DynamoDB.
    """
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
        
        logger.info(f"Successfully retrieved leaderboard configuration: {leaderboard_name}")
        return response['Item']
        
    except ClientError as e:
        logger.error(f"Error retrieving leaderboard configuration: {str(e)}")
        raise


@tracer.capture_method
async def acquire_rebuild_lock_async(leaderboard_name: str, job_id: str) -> bool:
    """
    Acquire a rebuild lock to prevent concurrent rebuilds.
    """
    try:
        client = await get_valkey_client()
        lock_key = f"{REBUILD_LOCK_PREFIX}:{leaderboard_name}"
        lock_value = json.dumps({
            'jobId': job_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'maxExecutionTime': MAX_EXECUTION_TIME
        })
        
        # Set lock with expiration (double the max execution time for safety)
        lock_acquired = await client.set(
            lock_key,
            lock_value,
            conditional_set="onlyIfDoesNotExist",
            expiry=ExpirySet(ExpiryType.SEC, MAX_EXECUTION_TIME * 2)
        )
        
        if lock_acquired:
            logger.info(f"Successfully acquired rebuild lock for {leaderboard_name}")
            return True
        else:
            logger.warning(f"Could not acquire rebuild lock for {leaderboard_name}")
            return False
            
    except Exception as e:
        logger.error(f"Error acquiring rebuild lock: {str(e)}")
        return False


@tracer.capture_method
async def release_rebuild_lock_async(leaderboard_name: str) -> None:
    """
    Release the rebuild lock.
    """
    try:
        client = await get_valkey_client()
        lock_key = f"{REBUILD_LOCK_PREFIX}:{leaderboard_name}"
        await client.delete([lock_key])
        logger.info(f"Released rebuild lock for {leaderboard_name}")
    except Exception as e:
        logger.warning(f"Error releasing rebuild lock: {str(e)}")


@tracer.capture_method
async def pause_leaderboard_updates_async(leaderboard_name: str, job_id: str) -> bool:
    """
    Pause updates to the leaderboard during rebuild.
    """
    if not ENABLE_PAUSE_UPDATES:
        return True
    
    try:
        client = await get_valkey_client()
        pause_key = f"{PAUSE_LOCK_PREFIX}:{leaderboard_name}"
        pause_value = json.dumps({
            'jobId': job_id,
            'pausedAt': datetime.now(timezone.utc).isoformat(),
            'reason': 'rebuild_in_progress'
        })
        
        # Set pause lock with expiration
        await client.set(
            pause_key,
            pause_value,
            expiry=ExpirySet(ExpiryType.SEC, MAX_EXECUTION_TIME * 2)
        )
        
        logger.info(f"Paused updates for leaderboard {leaderboard_name}")
        return True
        
    except Exception as e:
        logger.error(f"Error pausing leaderboard updates: {str(e)}")
        return False


@tracer.capture_method
async def resume_leaderboard_updates_async(leaderboard_name: str) -> None:
    """
    Resume updates to the leaderboard after rebuild.
    """
    if not ENABLE_PAUSE_UPDATES:
        return
    
    try:
        client = await get_valkey_client()
        pause_key = f"{PAUSE_LOCK_PREFIX}:{leaderboard_name}"
        await client.delete([pause_key])
        logger.info(f"Resumed updates for leaderboard {leaderboard_name}")
    except Exception as e:
        logger.warning(f"Error resuming leaderboard updates: {str(e)}")


@tracer.capture_method
async def backup_leaderboard_async(sorted_list_name: str, job_id: str) -> Optional[Dict[str, Any]]:
    """
    Backup the current leaderboard before rebuilding.
    """
    if not BACKUP_BEFORE_REBUILD:
        return None
    
    try:
        client = await get_valkey_client()
        
        # Check if leaderboard exists
        exists = await client.exists([sorted_list_name])
        if exists == 0:
            logger.info(f"Leaderboard {sorted_list_name} does not exist, no backup needed")
            return None
        
        # Get all entries with scores
        all_entries = await client.zrange_withscores(sorted_list_name, RangeByIndex(0, -1))
        
        # Get TTL if set
        ttl = await client.ttl(sorted_list_name)
        
        backup_data = {
            'sortedListName': sorted_list_name,
            'entries': all_entries if all_entries else {},
            'ttl': ttl if ttl > 0 else None,
            'backupTimestamp': datetime.now(timezone.utc).isoformat(),
            'entryCount': len(all_entries) if all_entries else 0,
            'jobId': job_id
        }
        
        # Store backup in Valkey with expiration
        backup_key = f"backup:{sorted_list_name}:{job_id}"
        await client.set(
            backup_key,
            json.dumps(backup_data, default=decimal_serializer),
            expiry=ExpirySet(ExpiryType.SEC, BACKUP_TTL_HOURS * 3600)
        )
        
        logger.info(f"Successfully backed up leaderboard {sorted_list_name} with {backup_data['entryCount']} entries")
        return backup_data
        
    except Exception as e:
        logger.warning(f"Failed to backup leaderboard {sorted_list_name}: {str(e)}")
        return None


@tracer.capture_method
async def reset_leaderboard_async(sorted_list_name: str, score_type: str = "score") -> int:
    """
    Reset the leaderboard by clearing all scores and adding init placeholder.
    """
    try:
        client = await get_valkey_client()
        
        # Get current size
        current_size = await client.zcard(sorted_list_name)
        
        # Delete the sorted list
        await client.delete([sorted_list_name])
        
        # Create new sorted list with score type-specific init placeholder
        init_config = INIT_CONFIG.get(score_type, INIT_CONFIG["score"])
        init_key = init_config["key"]
        init_value = init_config["value"]
        
        await client.zadd(sorted_list_name, {init_key: init_value})
        
        logger.info(f"Reset leaderboard {sorted_list_name} with init placeholder '{init_key}', removed {current_size} entries")
        return current_size
        
    except Exception as e:
        logger.error(f"Error resetting leaderboard {sorted_list_name}: {str(e)}")
        raise ConnectionError(f"Failed to reset leaderboard: {str(e)}")


@tracer.capture_method
async def remove_init_placeholder_if_exists(client, sorted_list_name: str) -> None:
    """
    Remove any init placeholder from the sorted set when first real score is added.
    """
    try:
        # Check sorted set size first - only search for placeholders if small
        set_size = await client.zcard(sorted_list_name)
        if set_size <= 10:
            removed_count = await client.zrem(sorted_list_name, INIT_PLACEHOLDER_KEYS)
            if removed_count > 0:
                logger.debug(f"Removed {removed_count} init placeholder(s) from {sorted_list_name}")
    except Exception as e:
        # Non-critical operation - log but don't fail
        logger.warning(f"Failed to remove init placeholder from {sorted_list_name}: {str(e)}")


@tracer.capture_method
async def query_game_stats_async(
    leaderboard_config: Dict[str, Any],
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    last_evaluated_key: Optional[Dict[str, Any]] = None,
    limit: int = MAX_BATCH_SIZE
) -> Dict[str, Any]:
    """
    Asynchronously query game stats for a specific leaderboard.
    """
    game_id = leaderboard_config['gameID']
    game_mode = leaderboard_config['gameMode']
    leaderboard_name = leaderboard_config['leaderboardName']
    
    # Build filter expression
    filter_expressions = ["leaderboardName = :leaderboardName"]
    expression_attribute_values = {
        ':leaderboardName': leaderboard_name,
        ':gameID': game_id,
        ':gameMode': game_mode
    }
    
    if start_timestamp is not None:
        filter_expressions.append("#timestamp >= :startTimestamp")
        expression_attribute_values[':startTimestamp'] = start_timestamp
    
    if end_timestamp is not None:
        filter_expressions.append("#timestamp <= :endTimestamp")
        expression_attribute_values[':endTimestamp'] = end_timestamp
    
    filter_expression = " AND ".join(filter_expressions)
    
    # Query parameters
    query_params = {
        'IndexName': 'gameID-gameMode-index',
        'KeyConditionExpression': 'gameID = :gameID AND gameMode = :gameMode',
        'FilterExpression': filter_expression,
        'ExpressionAttributeValues': expression_attribute_values,
        'Limit': limit,
        'ScanIndexForward': True
    }

    # Only include ExpressionAttributeNames when timestamp filters are used
    # (DynamoDB rejects unused attribute names)
    if start_timestamp is not None or end_timestamp is not None:
        query_params['ExpressionAttributeNames'] = {'#timestamp': 'timestamp'}
    
    if last_evaluated_key:
        query_params['ExclusiveStartKey'] = last_evaluated_key
    
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            get_thread_pool(),
            lambda: game_stats_table.query(**query_params)
        )
        
        return {
            'Items': response.get('Items', []),
            'LastEvaluatedKey': response.get('LastEvaluatedKey'),
            'Count': response.get('Count', 0),
            'ScannedCount': response.get('ScannedCount', 0)
        }
        
    except ClientError as e:
        logger.error(f"Error querying game stats: {str(e)}")
        raise


@tracer.capture_method
async def update_leaderboard_with_stats_async(
    sorted_list_name: str,
    leaderboard_type: str,
    leaderboard_config: Dict[str, Any],
    stats_items: List[Dict[str, Any]],
    score_strategy: str
) -> Tuple[int, List[str]]:
    """
    Update leaderboard with game stats using optimized batch operations.
    """
    client = await get_valkey_client()
    processed_count = 0
    errors = []
    
    # Remove init placeholder when adding real scores
    await remove_init_placeholder_if_exists(client, sorted_list_name)
    
    try:
        # Group updates by player to handle duplicates
        player_scores = {}
        
        for item in stats_items:
            try:
                player_id = item['playerID']
                
                # Validate score from DynamoDB.
                # IMPORTANT: DynamoDB stores scores already normalized to seconds
                # (the original store Lambda parsed and converted the raw input).
                # We must NOT re-apply format conversions (e.g., ms÷1000) during
                # rebuild. Override timeFormat to "seconds" so the parser treats
                # the stored value as-is.
                rebuild_config = dict(leaderboard_config)
                if rebuild_config.get('scoreType') == 'time':
                    rebuild_config['timeFormat'] = 'seconds'
                validated_score = parse_and_validate_time_score(
                    item['playerScore'],
                    rebuild_config
                )
                player_score = float(validated_score)
                
                if player_id not in player_scores:
                    player_scores[player_id] = []
                # Store score with sortKey for chronological ordering.
                # sortKey has millisecond-precision ISO timestamp (e.g., "gameID#mode#2026-02-24T06:05:52.573+00:00")
                # which is more precise than the integer `timestamp` field (second-level).
                player_scores[player_id].append((player_score, item.get('sortKey', item.get('timestamp', 0))))
                
            except (KeyError, ValueError, TypeError) as e:
                error_msg = f"Invalid stats item: {str(e)}"
                errors.append(error_msg)
                logger.warning(error_msg)
                continue
        
        # Process each player's scores
        for player_id, score_tuples in player_scores.items():
            try:
                # Sort by timestamp (ascending) so scores[-1] is the chronologically latest.
                # Each tuple is (score, timestamp).
                # Sort by sortKey/timestamp — string comparison works for both ISO timestamps
                # (lexicographic sort of ISO-8601 is chronological) and numeric timestamps
                score_tuples.sort(key=lambda x: str(x[1]) if x[1] else "")
                scores = [s[0] for s in score_tuples]

                # Calculate final score based on strategy
                if score_strategy == "replace":
                    final_score = scores[-1]  # Use the chronologically latest score
                elif score_strategy == "best":
                    if leaderboard_type == "DESCENDING_LB":
                        final_score = max(scores)  # Higher is better
                    else:  # ASCENDING_LB
                        final_score = min(scores)  # Lower is better
                elif score_strategy == "cumulative":
                    final_score = sum(scores)
                else:
                    # Use config default or fallback to best
                    final_score = max(scores) if leaderboard_type == "DESCENDING_LB" else min(scores)
                
                # Handle existing scores for best and cumulative strategies.
                # This merges the current batch's result with a score already in Valkey
                # (from a previous DynamoDB batch within this rebuild).
                # NOTE: current_score is NEGATIVE in Valkey for ASCENDING_LB.
                # final_score is always POSITIVE here (raw value). The ASCENDING negation
                # happens at the ZADD step (lines below), so all comparisons here use
                # positive (real-world) values.
                if score_strategy in ["best", "cumulative"]:
                    current_score = await client.zscore(sorted_list_name, player_id)
                    if current_score is not None:
                        current_score = float(current_score)

                        # Convert stored score to positive for comparison
                        existing_positive = -current_score if leaderboard_type == "ASCENDING_LB" else current_score

                        if score_strategy == "best":
                            if leaderboard_type == "DESCENDING_LB":
                                final_score = max(final_score, existing_positive)
                            else:  # ASCENDING_LB — lower is better
                                final_score = min(final_score, existing_positive)
                        elif score_strategy == "cumulative":
                            # Add new batch total to existing cumulative total
                            final_score = final_score + existing_positive
                
                # Store score (invert for ASCENDING_LB)
                if leaderboard_type == "ASCENDING_LB":
                    await client.zadd(sorted_list_name, {player_id: -final_score})
                else:
                    await client.zadd(sorted_list_name, {player_id: final_score})
                
                processed_count += 1
                
            except Exception as e:
                error_msg = f"Error processing player {player_id}: {str(e)}"
                errors.append(error_msg)
                logger.warning(error_msg)
                continue
        
        return processed_count, errors
        
    except Exception as e:
        logger.error(f"Error updating leaderboard with stats: {str(e)}")
        raise ConnectionError(f"Failed to update leaderboard: {str(e)}")


@tracer.capture_method
def continue_execution_async(state: Dict[str, Any]) -> None:
    """
    Continue execution by invoking this Lambda function again.
    """
    function_name = os.environ.get('AWS_LAMBDA_FUNCTION_NAME')
    if not function_name:
        raise ValueError("Could not determine the current Lambda function name")
    
    try:
        # Add continuation token
        state['continuationToken'] = str(uuid.uuid4())
        state['continuedAt'] = datetime.now(timezone.utc).isoformat()
        
        # Invoke function asynchronously
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='Event',
            Payload=json.dumps(state, default=decimal_serializer)
        )
        
        logger.info(f"Successfully invoked continuation with token: {state['continuationToken']}")
        
    except ClientError as e:
        logger.error(f"Error invoking continuation: {str(e)}")
        raise


# ============================================================================
# MAIN PROCESSING LOGIC
# ============================================================================

async def process_rebuild(state: Dict[str, Any], execution_start_time: float, auth_context: Dict[str, str]) -> Dict[str, Any]:
    """
    Main async processing function for rebuild operations.
    """
    # Get leaderboard name and job ID
    leaderboard_name = state['rebuildLeaderboardRequest']['leaderboardName']
    job_id = state['jobId']
    is_continuation = 'continuationToken' in state
    
    # Get leaderboard configuration
    leaderboard_config = await get_leaderboard_config_async(leaderboard_name)
    sorted_list_name = leaderboard_config['sortedListName']
    leaderboard_type = leaderboard_config['leaderboardType']
    score_type = leaderboard_config.get('scoreType', 'score')
    
    # Validate leaderboard type
    if leaderboard_type not in VALID_LEADERBOARD_TYPES:
        raise ValueError(f"Invalid leaderboard type: {leaderboard_type}")
    
    # Validate that the authenticated game matches the leaderboard
    if leaderboard_config['gameID'] != auth_context['gameId']:
        raise LeaderboardAuthenticationError(f"Leaderboard belongs to a different game")

    # Reject rebuild on expired leaderboards
    expiry_str = leaderboard_config.get('optionalLBExpiryDateTimeStamp')
    if expiry_str and not is_continuation:
        try:
            expiry_dt = dateutil.parser.isoparse(expiry_str)
            if expiry_dt <= datetime.now(timezone.utc):
                is_readonly = leaderboard_config.get('optionalLBReadOnlyOnExpiry', True)
                if is_readonly:
                    raise LeaderboardExpiredError(
                        f"Leaderboard '{leaderboard_name}' has expired and is in read-only mode. "
                        f"Expired at: {expiry_str}. Rebuild is not permitted on expired read-only leaderboards."
                    )
                else:
                    raise LeaderboardExpiredError(
                        f"Leaderboard '{leaderboard_name}' has expired and is scheduled for deletion. "
                        f"Expired at: {expiry_str}. Rebuild is not permitted on expired leaderboards pending deletion."
                    )
        except LeaderboardExpiredError:
            raise  # re-raise our own exception
        except Exception as e:
            logger.warning(f"Could not check leaderboard expiry: {str(e)}")

    # Get rebuild parameters
    score_strategy = state['rebuildLeaderboardRequest'].get('scoreStrategy', leaderboard_config.get('scoreStrategy', 'best'))
    start_timestamp = state['rebuildLeaderboardRequest'].get('startTimestamp')
    end_timestamp = state['rebuildLeaderboardRequest'].get('endTimestamp')
    
    # Initialize rebuild results
    rebuild_results = {
        'leaderboardName': leaderboard_name,
        'jobId': job_id,
        'sortedListName': sorted_list_name,
        'leaderboardType': leaderboard_type,
        'scoreType': score_type,
        'scoreStrategy': score_strategy,
        'lockAcquired': False,
        'updatesPaused': False,
        'backupCreated': False,
        'leaderboardReset': False,
        'processedItems': state['processedItems'],
        'batchesProcessed': state['batchesProcessed'],
        'errors': state['errors'],
        'phase': state['phase']
    }
    
    # For new executions, perform initialization
    if not is_continuation:
        # Check confirmation
        if not state['rebuildLeaderboardRequest'].get('confirmRebuild', False):
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'rebuildLeaderboardResponse': {
                        'error': 'Confirmation Required',
                        'message': 'Please set confirmRebuild to true to confirm this operation',
                        'warning': 'This will clear and rebuild the entire leaderboard from stored game stats',
                        'jobId': job_id,
                        'leaderboardName': leaderboard_name
                    }
                })
            }
        
        # Acquire rebuild lock
        lock_acquired = await acquire_rebuild_lock_async(leaderboard_name, job_id)
        if not lock_acquired:
            raise RebuildInProgressError(f"Another rebuild is already in progress for leaderboard {leaderboard_name}")
        
        rebuild_results['lockAcquired'] = True
        
        # Pause updates
        updates_paused = await pause_leaderboard_updates_async(leaderboard_name, job_id)
        rebuild_results['updatesPaused'] = updates_paused
        
        # Backup current leaderboard
        backup_data = await backup_leaderboard_async(sorted_list_name, job_id)
        rebuild_results['backupCreated'] = backup_data is not None
        if backup_data:
            rebuild_results['backupEntryCount'] = backup_data['entryCount']
        
        # Reset leaderboard with proper init placeholder
        entries_removed = await reset_leaderboard_async(sorted_list_name, score_type)
        rebuild_results['leaderboardReset'] = True
        rebuild_results['entriesRemoved'] = entries_removed
        
        state['phase'] = 'processing'
        logger.info(f"Initialization complete, removed {entries_removed} entries")
    
    # Process items in batches
    last_evaluated_key = state.get('lastEvaluatedKey')
    total_processed = state['processedItems']
    batches_processed = state['batchesProcessed']
    all_errors = state['errors']
    
    while True:
        # Check execution time
        current_time = time.time()
        execution_time = current_time - execution_start_time
        
        if execution_time > (MAX_EXECUTION_TIME - TIME_BUFFER):
            logger.info(f"Approaching timeout after {execution_time}s, continuing execution")
            
            # Update state for continuation
            state['processedItems'] = total_processed
            state['batchesProcessed'] = batches_processed
            state['lastEvaluatedKey'] = last_evaluated_key
            state['errors'] = all_errors
            state['phase'] = 'processing'
            
            # Continue execution
            continue_execution_async(state)
            
            return {
                'statusCode': 202,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'rebuildLeaderboardResponse': {
                        'message': 'Leaderboard rebuild in progress',
                        'leaderboardName': leaderboard_name,
                        'jobId': job_id,
                        'processedItems': total_processed,
                        'batchesProcessed': batches_processed,
                        'isComplete': False,
                        'phase': 'processing',
                        'executionTimeSeconds': int(execution_time)
                    }
                })
            }
        
        # Query next batch
        query_result = await query_game_stats_async(
            leaderboard_config,
            start_timestamp,
            end_timestamp,
            last_evaluated_key,
            MAX_BATCH_SIZE
        )
        
        items = query_result['Items']
        last_evaluated_key = query_result.get('LastEvaluatedKey')

        # Process items if any matched the filter
        if items:
            processed_count, batch_errors = await update_leaderboard_with_stats_async(
                sorted_list_name,
                leaderboard_type,
                leaderboard_config,
                items,
                score_strategy
            )

            total_processed += processed_count
            batches_processed += 1
            all_errors.extend(batch_errors)

            logger.info(f"Processed batch {batches_processed}: {processed_count} items, {total_processed} total")
        else:
            # Empty page after filtering — DynamoDB scanned items that didn't match
            # the leaderboardName filter. This is normal when the GSI contains items
            # from multiple leaderboards. Continue to next page if LastEvaluatedKey exists.
            logger.info(f"Empty page after filter (scanned items from other leaderboards), continuing...")

        # If no more pages, we're done
        if not last_evaluated_key:
            logger.info(f"Completed processing with {total_processed} total items")
            state['phase'] = 'completion'
            break
    
    # Finalization
    client = await get_valkey_client()
    final_size = await client.zcard(sorted_list_name)

    # Restore TTL if the leaderboard has an expiry configured.
    # The DELETE + ZADD during reset cleared the original TTL, so we recalculate
    # it from the config's optionalLBExpiryDateTimeStamp.
    try:
        expiry_str = leaderboard_config.get('optionalLBExpiryDateTimeStamp')
        if expiry_str:
            expiry_dt = dateutil.parser.isoparse(expiry_str)
            remaining_seconds = int((expiry_dt - datetime.now(timezone.utc)).total_seconds())
            if remaining_seconds > 0:
                await client.expire(sorted_list_name, remaining_seconds)
                logger.info(f"Restored TTL of {remaining_seconds}s on rebuilt leaderboard (expires: {expiry_str})")
            else:
                logger.info(f"Leaderboard expiry {expiry_str} is in the past, no TTL set on rebuilt leaderboard")
    except Exception as e:
        logger.warning(f"Could not restore TTL after rebuild: {str(e)}")

    # Resume updates and release lock
    await resume_leaderboard_updates_async(leaderboard_name)
    await release_rebuild_lock_async(leaderboard_name)
    
    rebuild_results.update({
        'processedItems': total_processed,
        'batchesProcessed': batches_processed,
        'finalLeaderboardSize': final_size,
        'errors': all_errors,
        'errorCount': len(all_errors),
        'phase': 'completed',
        'success': True
    })
    
    return rebuild_results


# ============================================================================
# MAIN LAMBDA HANDLER
# ============================================================================

@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
@handle_errors
@log_execution
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    High-performance Lambda handler for rebuilding leaderboards with relay capability.
    """
    # ENTRY LOGGING
    logger.info("=== LAMBDA HANDLER ENTRY ===")
    logger.info(f"Function: {context.function_name}")
    logger.info(f"Request ID: {context.aws_request_id}")
    
    start_time = time.perf_counter()
    execution_start_time = time.time()
    
    # Validate HTTP method - API Gateway only allows POST for rebuild
    http_method = event.get('httpMethod', '').upper()
    if http_method != 'POST' and 'continuationToken' not in event:
        logger.error(f"Invalid HTTP method: {http_method}")
        return {
            'statusCode': 405,
            'headers': {
                'Content-Type': 'application/json',
                'Allow': 'POST',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'rebuildLeaderboardResponse': {
                    'error': 'Method Not Allowed',
                    'message': f'HTTP method {http_method} not allowed. Only POST is supported.',
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
            })
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
            'body': json.dumps({
                'rebuildLeaderboardResponse': {
                    'success': False,
                    'error': 'Service temporarily unavailable - Valkey dependency not found',
                    'message': 'The leaderboard service is currently unavailable due to a dependency issue'
                }
            })
        }
    
    logger.info("Valkey is available, proceeding with request processing")
    
    # Validate authentication (unless this is a continuation)
    if 'continuationToken' not in event:
        try:
            auth_context = validate_authenticated_context(event, 'write')
            logger.info(f"Rebuild operation for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
        except LeaderboardAuthenticationError as e:
            logger.error(f"Authentication failed: {str(e)}")
            raise
    else:
        # For continuations, extract auth context from state
        auth_context = {
            'studioId': event.get('studioId', 'unknown'),
            'gameId': event.get('gameId', 'unknown'),
            'studioName': event.get('studioName', ''),
            'gameTitle': event.get('gameTitle', ''),
            'contactEmail': event.get('contactEmail', ''),
            'permissions': ['write']
        }
    
    # Validate AWS resources
    validate_aws_resources()
    
    # Validate request and get state
    state = validate_request(event)
    
    # Store auth context in state for continuations
    if 'continuationToken' not in event:
        state['studioId'] = auth_context['studioId']
        state['gameId'] = auth_context['gameId']
        state['studioName'] = auth_context['studioName']
        state['gameTitle'] = auth_context['gameTitle']
        state['contactEmail'] = auth_context['contactEmail']
    
    # Execute the async processing
    try:
        result = run_async(process_rebuild(state, execution_start_time, auth_context))
        
        # If result is a response dict, return it directly
        if isinstance(result, dict) and 'statusCode' in result:
            return result
        
        # Calculate total processing time
        total_time = time.perf_counter() - start_time
        
        # COMPLETION LOGGING
        logger.info(f"=== LAMBDA HANDLER COMPLETION ===")
        logger.info(f"Status: 200, Processing time: {int(total_time * 1000)}ms")
        logger.info(f"Processed items: {result.get('processedItems', 0)}")
        
        # Return success response
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache'
            },
            'body': json.dumps({
                'rebuildLeaderboardResponse': {
                    'message': 'Leaderboard rebuild completed successfully',
                    'rebuildResults': result,
                    'metadata': {
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'requestId': context.aws_request_id,
                        'processingTimeMs': int(total_time * 1000),
                        'studioId': auth_context['studioId'],
                        'gameId': auth_context['gameId']
                    },
                    'success': True
                }
            }, default=decimal_serializer)
        }
        
    except Exception as e:
        # Ensure cleanup on error
        try:
            leaderboard_name = state['rebuildLeaderboardRequest']['leaderboardName']
            run_async(resume_leaderboard_updates_async(leaderboard_name))
            run_async(release_rebuild_lock_async(leaderboard_name))
        except:
            pass
        raise e


# ============================================================================
# CLEANUP
# ============================================================================

async def cleanup_resources():
    """
    Comprehensive cleanup function for proper resource management.
    """
    global thread_pool, valkey_client
    
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


# Register cleanup for Lambda container lifecycle
import atexit
atexit.register(lambda: run_async(cleanup_resources()))