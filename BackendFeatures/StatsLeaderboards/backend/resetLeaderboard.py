# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
resetLeaderboard.py -- Reset Leaderboard Lambda Function

Resets leaderboards by clearing all scores while maintaining structure:
- Validates reset requests with studio authentication
- Creates time-limited backups before reset
- Clears leaderboard and adds init placeholder to prevent deletion
- Supports scheduled expiry resets (via direct invocation or future EventBridge Scheduler)
- Handles large leaderboards with Lambda self-invoke relay capability

Updated for Python 3.13 and Valkey-GLIDE 2.4.1
High-performance, robust implementation optimized for data integrity and performance.
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
MEMORYDB_CLUSTER_NAME = os.environ.get('gameLeaderboardsMemoryDBName')
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()

# Valkey configuration
VALKEY_USE_TLS = os.environ.get('VALKEY_USE_TLS', 'true').lower() == 'true'
VALKEY_CLUSTER_MODE = os.environ.get('VALKEY_CLUSTER_MODE', 'true').lower() == 'true'

# Reset-specific settings
BACKUP_BEFORE_RESET = os.environ.get('BACKUP_BEFORE_RESET', 'true').lower() == 'true'
ENABLE_RESET_LOCK = os.environ.get('ENABLE_RESET_LOCK', 'true').lower() == 'true'
BACKUP_TTL_HOURS = int(os.environ.get('BACKUP_TTL_HOURS', '24'))
SCHEDULED_BACKUP_TTL_DAYS = int(os.environ.get('SCHEDULED_BACKUP_TTL_DAYS', '30'))

# Performance optimization settings
MAX_CONCURRENT_OPERATIONS = int(os.environ.get('MAX_CONCURRENT_OPERATIONS', '10'))
CONNECTION_TIMEOUT = int(os.environ.get('GLIDE_CONNECTION_TIMEOUT_MS', '2000'))
REQUEST_TIMEOUT = int(os.environ.get('GLIDE_REQUEST_TIMEOUT_MS', '5000'))

# Lambda Relay configuration for long-running operations
MAX_EXECUTION_TIME = int(os.environ.get('MAX_EXECUTION_TIME', '780'))  # 13 minutes
TIME_BUFFER = int(os.environ.get('TIME_BUFFER', '120'))  # 2-minute safety buffer
BATCH_SIZE = int(os.environ.get('RESET_BATCH_SIZE', '1000'))

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
logger.info(f"MemoryDB cluster: {MEMORYDB_CLUSTER_NAME}")

# Initialize DynamoDB tables
leaderboards_config_table = dynamodb.Table(LEADERBOARDS_CONFIG_TABLE_NAME) if LEADERBOARDS_CONFIG_TABLE_NAME else None

# Constants
VALID_LEADERBOARD_TYPES = ["DESCENDING_LB", "ASCENDING_LB"]
VALID_SCORE_TYPES = ["score", "time", "distance", "points", "rank", "level"]
RESET_LOCK_PREFIX = "reset_lock"

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
            if 'resetLeaderboardRequest' in params:
                if 'continuationToken' in params['resetLeaderboardRequest']:
                    params['resetLeaderboardRequest']['continuationToken'] = "[REDACTED]"
        
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


class ResetInProgressError(Exception):
    """Custom exception for concurrent reset attempts"""
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
                'body': json.dumps({'resetLeaderboardResponse': {
                    'error': 'Not Found',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except ResetInProgressError as e:
            logger.error(f"Reset conflict: {str(e)}")
            return {
                'statusCode': 409,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'resetLeaderboardResponse': {
                    'error': 'Conflict',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'resetLeaderboardResponse': {
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
                'body': json.dumps({'resetLeaderboardResponse': {
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
                'body': json.dumps({'resetLeaderboardResponse': {
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
                'body': json.dumps({'resetLeaderboardResponse': {
                    'error': 'Service Unavailable',
                    'message': 'Database connection error',
                    'note': 'Reset operation may be partially complete',
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
                'body': json.dumps({'resetLeaderboardResponse': {
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
    Reset operations require write permissions.
    
    Args:
        event: Lambda event containing authorizer context
        required_permission: Required permission level ('write' for reset)
        
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
                client_name="leaderboard-reset-lambda",
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
                client_name="leaderboard-reset-lambda",
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
            thread_name_prefix="leaderboard-reset-worker"
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
def validate_aws_resources() -> None:
    """
    Enhanced AWS resource validation with health checks.
    """
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
    Enhanced request validation with support for scheduled expiry and continuation.
    """
    # Handle continuation requests
    if 'continuationToken' in event:
        logger.info("Processing continuation request")
        return event
    
    # Handle scheduled expiry requests (from external scheduler or direct invocation)
    if event.get('scheduledExpiry', False):
        # Scheduler passes parameters directly
        if 'leaderboardName' not in event:
            raise ValueError("leaderboardName is missing in scheduled expiry event")
        
        return {
            'leaderboardName': event['leaderboardName'],
            'confirmReset': True,  # Auto-confirm for scheduled expiry
            'createBackup': True,  # Always backup for scheduled expiry
            'isScheduledExpiry': True
        }
    
    # Handle regular API requests
    # Check if body exists
    if 'body' not in event:
        raise ValueError("Request body is missing")
    
    # Parse body
    try:
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in request body")
    
    # Check if resetLeaderboardRequest exists
    if 'resetLeaderboardRequest' not in body:
        raise ValueError("resetLeaderboardRequest is missing in request body")
    
    request_params = body['resetLeaderboardRequest']
    
    # Validate required fields
    if 'leaderboardName' not in request_params:
        raise ValueError("leaderboardName is missing in resetLeaderboardRequest")
    
    if not isinstance(request_params['leaderboardName'], str) or not request_params['leaderboardName'].strip():
        raise ValueError("leaderboardName must be a non-empty string")
    
    # Validate leaderboard name format
    leaderboard_name = request_params['leaderboardName'].strip()
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', leaderboard_name):
        raise ValueError("leaderboardName contains invalid characters")
    
    # Validate confirmation
    if 'confirmReset' in request_params:
        if not isinstance(request_params['confirmReset'], bool):
            raise ValueError("confirmReset must be a boolean")
    else:
        request_params['confirmReset'] = False
    
    # Validate backup option
    if 'createBackup' in request_params:
        if not isinstance(request_params['createBackup'], bool):
            raise ValueError("createBackup must be a boolean")
    else:
        request_params['createBackup'] = BACKUP_BEFORE_RESET
    
    request_params['isScheduledExpiry'] = False
    
    return request_params


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
async def acquire_reset_lock_async(leaderboard_name: str, operation_id: str) -> bool:
    """
    Acquire a reset lock to prevent concurrent operations.
    """
    if not ENABLE_RESET_LOCK:
        return True
    
    try:
        client = await get_valkey_client()
        lock_key = f"{RESET_LOCK_PREFIX}:{leaderboard_name}"
        lock_value = json.dumps({
            'operationId': operation_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'operation': 'reset'
        })
        
        # Set lock with 5-minute expiration
        lock_acquired = await client.set(
            lock_key,
            lock_value,
            conditional_set="onlyIfDoesNotExist",
            expiry=ExpirySet(ExpiryType.SEC, 300)
        )
        
        if lock_acquired:
            logger.info(f"Successfully acquired reset lock for {leaderboard_name}")
            return True
        else:
            logger.warning(f"Could not acquire reset lock for {leaderboard_name}")
            return False
            
    except Exception as e:
        logger.error(f"Error acquiring reset lock: {str(e)}")
        return False


@tracer.capture_method
async def release_reset_lock_async(leaderboard_name: str) -> None:
    """
    Release the reset lock.
    """
    if not ENABLE_RESET_LOCK:
        return
    
    try:
        client = await get_valkey_client()
        lock_key = f"{RESET_LOCK_PREFIX}:{leaderboard_name}"
        await client.delete([lock_key])
        logger.info(f"Released reset lock for {leaderboard_name}")
    except Exception as e:
        logger.warning(f"Error releasing reset lock: {str(e)}")


@tracer.capture_method
async def backup_leaderboard_async(sorted_list_name: str, operation_id: str, is_scheduled_expiry: bool = False) -> Optional[Dict[str, Any]]:
    """
    Backup the current leaderboard before resetting.
    Extended retention for scheduled expiry backups.
    """
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
        
        # Process entries based on GLIDE response format
        entries_list = []
        if all_entries:
            if isinstance(all_entries, dict):
                # GLIDE returns dict format {member: score}
                entries_list = [(member, score) for member, score in all_entries.items()]
            else:
                # Handle other possible formats
                entries_list = all_entries
        
        backup_data = {
            'sortedListName': sorted_list_name,
            'entries': entries_list,
            'ttl': ttl if ttl > 0 else None,
            'backupTimestamp': datetime.now(timezone.utc).isoformat(),
            'entryCount': len(entries_list),
            'operationId': operation_id,
            'backupType': 'scheduled_expiry' if is_scheduled_expiry else 'manual_reset',
            'isScheduledExpiry': is_scheduled_expiry
        }
        
        # Store backup in Valkey with extended expiration for scheduled expiry
        backup_key = f"backup:{sorted_list_name}:{operation_id}"
        backup_expiry_seconds = (SCHEDULED_BACKUP_TTL_DAYS * 86400) if is_scheduled_expiry else (BACKUP_TTL_HOURS * 3600)
        
        await client.set(
            backup_key,
            json.dumps(backup_data, default=decimal_serializer),
            expiry=ExpirySet(ExpiryType.SEC, backup_expiry_seconds)
        )
        
        retention_days = SCHEDULED_BACKUP_TTL_DAYS if is_scheduled_expiry else (BACKUP_TTL_HOURS / 24)
        logger.info(f"Successfully backed up leaderboard {sorted_list_name} with {backup_data['entryCount']} entries "
                   f"(retention: {retention_days} days)")
        return backup_data
        
    except Exception as e:
        logger.warning(f"Failed to backup leaderboard {sorted_list_name}: {str(e)}")
        return None


@tracer.capture_method
async def reset_leaderboard_async(sorted_list_name: str, score_type: str = "score") -> Dict[str, Any]:
    """
    Reset the leaderboard by clearing all scores and adding init placeholder.
    CRITICAL: Maintains init placeholder to prevent MemoryDB from auto-deleting empty sorted set.
    """
    try:
        client = await get_valkey_client()
        
        # Get current size and TTL before reset
        current_size = await client.zcard(sorted_list_name)
        current_ttl = await client.ttl(sorted_list_name)
        
        logger.info(f"Resetting leaderboard {sorted_list_name} with {current_size} entries, TTL: {current_ttl}")
        
        # Delete the sorted list
        await client.delete([sorted_list_name])
        logger.info(f"Deleted sorted list {sorted_list_name}")
        
        # CRITICAL: Create new sorted list with score type-specific init placeholder
        # This prevents MemoryDB from auto-deleting the empty sorted set
        init_config = INIT_CONFIG.get(score_type, INIT_CONFIG["score"])
        init_key = init_config["key"]
        init_value = init_config["value"]
        
        await client.zadd(sorted_list_name, {init_key: init_value})
        logger.info(f"Added init placeholder '{init_key}' with value {init_value} to maintain sorted set")
        
        # Verify the placeholder was added
        placeholder_exists = await client.zscore(sorted_list_name, init_key)
        if placeholder_exists is None:
            logger.error(f"CRITICAL: Failed to add init placeholder '{init_key}' to {sorted_list_name}, retrying")
            # Retry with the same score-type-specific placeholder (not a generic one,
            # which would not be recognized by the read Lambdas' filter logic)
            await client.zadd(sorted_list_name, {init_key: init_value})
            logger.info(f"Retried init placeholder '{init_key}' with value {init_value}")
        
        # Restore TTL if it existed
        if current_ttl > 0:
            await client.expire(sorted_list_name, current_ttl)
            logger.info(f"Restored TTL of {current_ttl} seconds to reset leaderboard")
        
        # Generate timestamps
        current_timestamp = int(time.time())
        current_datetime = datetime.fromtimestamp(current_timestamp, timezone.utc).isoformat()
        
        reset_info = {
            'sortedListName': sorted_list_name,
            'entriesRemoved': current_size,
            'ttlRestored': current_ttl if current_ttl > 0 else None,
            'resetTimestamp': current_timestamp,
            'resetTimestampISO': current_datetime,
            'scoreType': score_type,
            'initPlaceholder': init_key,
            'initPlaceholderValue': init_value
        }
        
        logger.info(f"Successfully reset leaderboard {sorted_list_name}, removed {current_size} entries, added init placeholder")
        return reset_info
        
    except Exception as e:
        logger.error(f"Error resetting leaderboard {sorted_list_name}: {str(e)}")
        raise ConnectionError(f"Failed to reset leaderboard: {str(e)}")


@tracer.capture_method
async def reset_leaderboard_with_relay_async(
    sorted_list_name: str,
    score_type: str,
    state: Dict[str, Any],
    execution_start_time: float
) -> Union[Dict[str, Any], Dict[str, Any]]:
    """
    Reset leaderboard with Lambda Relay support for large datasets.
    Ensures init placeholder is maintained even for batch operations.
    """
    try:
        client = await get_valkey_client()
        
        # Get current size and TTL before reset (if not already done)
        if 'totalEntries' not in state or state['totalEntries'] == 0:
            current_size = await client.zcard(sorted_list_name)
            current_ttl = await client.ttl(sorted_list_name)
            state['totalEntries'] = current_size
            state['originalTTL'] = current_ttl if current_ttl > 0 else None
        
        # For small leaderboards, do direct reset
        if state['totalEntries'] <= BATCH_SIZE:
            return await reset_leaderboard_async(sorted_list_name, score_type)
        
        # For large leaderboards, process in batches
        processed = state.get('processedEntries', 0)
        total = state['totalEntries']
        
        logger.info(f"Processing large leaderboard reset: {processed}/{total} entries processed")
        
        # Process entries in batches.
        # IMPORTANT: Always read from index 0 because after each ZREM the remaining
        # entries shift down. Using an incrementing offset would skip entries.
        while processed < total:
            # Check execution time
            if time.time() - execution_start_time > MAX_EXECUTION_TIME - TIME_BUFFER:
                # Save state and continue
                state['processedEntries'] = processed
                state['phase'] = 'reset_in_progress'
                return continue_execution_async(state)

            # Always read from index 0 — entries shift down after each removal
            entries_to_remove = await client.zrange(sorted_list_name, RangeByIndex(0, BATCH_SIZE - 1))

            if entries_to_remove:
                # Convert to list of members if dict format
                if isinstance(entries_to_remove, dict):
                    members_to_remove = list(entries_to_remove.keys())
                else:
                    members_to_remove = entries_to_remove

                await client.zrem(sorted_list_name, members_to_remove)
                processed += len(members_to_remove)
                logger.info(f"Removed batch: {len(members_to_remove)} entries, progress: {processed}/{total}")
            else:
                # No more entries to remove
                break
        
        # CRITICAL: Add init placeholder to maintain sorted set
        init_config = INIT_CONFIG.get(score_type, INIT_CONFIG["score"])
        init_key = init_config["key"]
        init_value = init_config["value"]
        
        await client.zadd(sorted_list_name, {init_key: init_value})
        logger.info(f"Added init placeholder '{init_key}' with value {init_value} after batch reset")
        
        # Restore TTL if it existed
        if state.get('originalTTL'):
            await client.expire(sorted_list_name, state['originalTTL'])
            logger.info(f"Restored TTL of {state['originalTTL']} seconds to reset leaderboard")
        
        # Generate reset info
        current_timestamp = int(time.time())
        current_datetime = datetime.fromtimestamp(current_timestamp, timezone.utc).isoformat()
        
        reset_info = {
            'sortedListName': sorted_list_name,
            'entriesRemoved': total,
            'ttlRestored': state.get('originalTTL'),
            'resetTimestamp': current_timestamp,
            'resetTimestampISO': current_datetime,
            'batchProcessed': True,
            'totalBatches': (total + BATCH_SIZE - 1) // BATCH_SIZE,
            'scoreType': score_type,
            'initPlaceholder': init_key,
            'initPlaceholderValue': init_value
        }
        
        logger.info(f"Successfully completed batch reset of leaderboard {sorted_list_name}, removed {total} entries")
        return reset_info
        
    except Exception as e:
        logger.error(f"Error in batch reset of leaderboard {sorted_list_name}: {str(e)}")
        raise ConnectionError(f"Failed to reset leaderboard: {str(e)}")


@tracer.capture_method
async def renew_leaderboard_expiry_async(leaderboard_name: str, leaderboard_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Renew leaderboard expiry after reset by clearing the expiry datetime.
    This makes the leaderboard available for manual renewal via updateLeaderboardConfig.
    """
    try:
        # Only renew if the leaderboard had an expiry datetime
        if 'optionalLBExpiryDateTimeStamp' not in leaderboard_config:
            logger.info(f"Leaderboard {leaderboard_name} has no expiry datetime, no renewal needed")
            return None
        
        # Remove the expiry datetime to make the leaderboard indefinite
        loop = asyncio.get_event_loop()
        
        update_response = await loop.run_in_executor(
            get_thread_pool(),
            lambda: leaderboards_config_table.update_item(
                Key={'leaderboardName': leaderboard_name},
                UpdateExpression='REMOVE optionalLBExpiryDateTimeStamp, optionalLBReadOnlyOnExpiry SET updatedAt = :timestamp, updatedAtISO = :timestamp_iso',
                ExpressionAttributeValues={
                    ':timestamp': int(time.time()),
                    ':timestamp_iso': datetime.now(timezone.utc).isoformat()
                },
                ReturnValues='ALL_NEW'
            )
        )
        
        renewal_info = {
            'leaderboardName': leaderboard_name,
            'expiryRemoved': True,
            'renewedAt': datetime.now(timezone.utc).isoformat(),
            'message': 'Leaderboard expiry cleared - use updateLeaderboardConfig to set new expiry'
        }
        
        logger.info(f"Successfully renewed leaderboard {leaderboard_name} - expiry datetime cleared")
        return renewal_info
        
    except Exception as e:
        logger.error(f"Failed to renew leaderboard expiry for {leaderboard_name}: {str(e)}")
        return {
            'leaderboardName': leaderboard_name,
            'expiryRemoved': False,
            'error': str(e),
            'message': 'Failed to clear expiry datetime'
        }


@tracer.capture_method
def continue_execution_async(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Continue execution by invoking this Lambda function again with state.
    """
    try:
        # Get current Lambda function name
        function_name = os.environ.get('AWS_LAMBDA_FUNCTION_NAME')
        if not function_name:
            raise ValueError("Could not determine the current Lambda function name")
        
        # Add continuation token
        state['continuationToken'] = str(uuid.uuid4())
        state['continuedAt'] = datetime.now(timezone.utc).isoformat()
        
        # Invoke function asynchronously
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='Event',
            Payload=json.dumps(state, default=decimal_serializer)
        )
        
        logger.info(f"Successfully triggered continuation for operation {state['operationId']}")
        
        # Return continuation response
        return {
            'statusCode': 202,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'resetLeaderboardResponse': {
                    'message': 'Reset operation in progress',
                    'operationId': state['operationId'],
                    'leaderboardName': state['leaderboardName'],
                    'phase': state['phase'],
                    'processedEntries': state.get('processedEntries', 0),
                    'totalEntries': state.get('totalEntries', 0),
                    'isComplete': False,
                    'continuationTriggered': True
                }
            })
        }
        
    except Exception as e:
        logger.error(f"Failed to continue execution: {str(e)}")
        raise


# ============================================================================
# MAIN PROCESSING LOGIC
# ============================================================================

async def process_reset(state: Dict[str, Any], execution_start_time: float, auth_context: Dict[str, str]) -> Dict[str, Any]:
    """
    Main async processing function for reset operations.
    """
    # Get leaderboard name and operation ID
    leaderboard_name = state['leaderboardName']
    operation_id = state.get('operationId', str(uuid.uuid4()))
    is_scheduled_expiry = state.get('isScheduledExpiry', False)
    is_continuation = 'continuationToken' in state
    
    if not is_continuation:
        state['operationId'] = operation_id
        state['phase'] = 'initialization'
        state['lockAcquired'] = False
        state['backupCompleted'] = False
        state['resetCompleted'] = False
    
    # Get leaderboard configuration
    leaderboard_config = await get_leaderboard_config_async(leaderboard_name)
    sorted_list_name = leaderboard_config['sortedListName']
    score_type = leaderboard_config.get('scoreType', 'score')
    
    # Validate that the authenticated game matches the leaderboard (skip for scheduled expiry)
    if not is_scheduled_expiry and leaderboard_config['gameID'] != auth_context['gameId']:
        raise LeaderboardAuthenticationError(f"Leaderboard belongs to a different game")
    
    # Track reset results
    reset_results = {
        'leaderboardName': leaderboard_name,
        'operationId': operation_id,
        'sortedListName': sorted_list_name,
        'scoreType': score_type,
        'isScheduledExpiry': is_scheduled_expiry,
        'isContinuation': is_continuation,
        'lockAcquired': state.get('lockAcquired', False),
        'backupCreated': state.get('backupCompleted', False),
        'resetCompleted': state.get('resetCompleted', False),
        'backupData': None,
        'resetInfo': None
    }
    
    try:
        # Phase 1: Acquire lock (if not already acquired)
        if not state.get('lockAcquired', False):
            lock_acquired = await acquire_reset_lock_async(leaderboard_name, operation_id)
            if not lock_acquired:
                raise ResetInProgressError(f"Another reset operation is already in progress for leaderboard {leaderboard_name}")
            
            state['lockAcquired'] = True
            reset_results['lockAcquired'] = True
        
        # Phase 2: Backup (if not already completed and requested)
        if not state.get('backupCompleted', False) and state.get('createBackup', BACKUP_BEFORE_RESET):
            # Check execution time before backup
            if time.time() - execution_start_time > MAX_EXECUTION_TIME - TIME_BUFFER:
                return continue_execution_async(state)
            
            backup_data = await backup_leaderboard_async(
                sorted_list_name,
                operation_id,
                is_scheduled_expiry
            )
            state['backupCompleted'] = True
            reset_results['backupCreated'] = backup_data is not None
            reset_results['backupData'] = backup_data
        
        # Phase 3: Reset leaderboard (with potential continuation)
        if not state.get('resetCompleted', False):
            # Check execution time before reset
            if time.time() - execution_start_time > MAX_EXECUTION_TIME - TIME_BUFFER:
                return continue_execution_async(state)
            
            # Check if this is a large leaderboard that needs batch processing
            client = await get_valkey_client()
            leaderboard_size = await client.zcard(sorted_list_name)
            
            if leaderboard_size > BATCH_SIZE:
                # Use batch reset with relay
                reset_info = await reset_leaderboard_with_relay_async(
                    sorted_list_name,
                    score_type,
                    state,
                    execution_start_time
                )
                
                # If reset_info contains continuation, return it
                if isinstance(reset_info, dict) and reset_info.get('statusCode') == 202:
                    return reset_info
            else:
                # Use direct reset for small leaderboards
                reset_info = await reset_leaderboard_async(sorted_list_name, score_type)
            
            state['resetCompleted'] = True
            reset_results['resetCompleted'] = True
            reset_results['resetInfo'] = reset_info
        
        # Phase 4: Renew expiry (only for scheduled expiry resets)
        if state.get('resetCompleted', False) and is_scheduled_expiry:
            renewal_info = await renew_leaderboard_expiry_async(leaderboard_name, leaderboard_config)
            reset_results['renewalInfo'] = renewal_info
            if renewal_info and renewal_info.get('expiryRemoved'):
                logger.info(f"Successfully renewed leaderboard {leaderboard_name} after scheduled reset")
        
        return reset_results
        
    finally:
        # Always release the lock if we acquired it
        if state.get('lockAcquired', False):
            await release_reset_lock_async(leaderboard_name)


# ============================================================================
# MAIN LAMBDA HANDLER
# ============================================================================

@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
@handle_errors
@log_execution
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    High-performance Lambda handler for resetting leaderboards with Lambda Relay support.
    """
    # ENTRY LOGGING
    logger.info("=== LAMBDA HANDLER ENTRY ===")
    logger.info(f"Function: {context.function_name}")
    logger.info(f"Request ID: {context.aws_request_id}")
    
    start_time = time.perf_counter()
    execution_start_time = time.time()
    
    # Determine if this is a scheduled expiry or manual reset
    is_scheduled_expiry = event.get('scheduledExpiry', False)
    is_continuation = 'continuationToken' in event
    
    # Validate HTTP method for API requests (skip for scheduled/continuation)
    if not is_scheduled_expiry and not is_continuation:
        http_method = event.get('httpMethod', '').upper()
        if http_method != 'POST':
            logger.error(f"Invalid HTTP method: {http_method}")
            return {
                'statusCode': 405,
                'headers': {
                    'Content-Type': 'application/json',
                    'Allow': 'POST',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'resetLeaderboardResponse': {
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
                'resetLeaderboardResponse': {
                    'success': False,
                    'error': 'Service temporarily unavailable - Valkey dependency not found',
                    'message': 'The leaderboard service is currently unavailable due to a dependency issue'
                }
            })
        }
    
    logger.info("Valkey is available, proceeding with request processing")
    
    # Validate authentication (skip for scheduled expiry and continuation)
    if not is_scheduled_expiry and not is_continuation:
        try:
            auth_context = validate_authenticated_context(event, 'write')
            logger.info(f"Reset operation for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
        except LeaderboardAuthenticationError as e:
            logger.error(f"Authentication failed: {str(e)}")
            raise
    else:
        # For scheduled expiry and continuation, use system context
        auth_context = {
            'studioId': 'system',
            'gameId': event.get('gameId', 'system'),
            'studioName': 'System',
            'gameTitle': 'System',
            'contactEmail': '',
            'permissions': ['write']
        }
    
    # Validate AWS resources
    validate_aws_resources()
    
    # Validate request and get state
    if is_continuation:
        state = event
    else:
        state = validate_request(event)
        
        # For manual resets, check confirmation
        if not is_scheduled_expiry and not state['confirmReset']:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'resetLeaderboardResponse': {
                        'error': 'Confirmation Required',
                        'message': 'Please set confirmReset to true to confirm this operation',
                        'warning': 'This will clear all scores from the leaderboard',
                        'leaderboardName': state['leaderboardName']
                    }
                })
            }
    
    logger.info(f"Starting reset: leaderboard={state['leaderboardName']}, "
               f"scheduled={is_scheduled_expiry}, continuation={is_continuation}")
    
    # Execute the async processing
    try:
        result = run_async(process_reset(state, execution_start_time, auth_context))
        
        # If result is a response dict (continuation), return it directly
        if isinstance(result, dict) and 'statusCode' in result:
            return result
        
        # Calculate total processing time
        total_time = time.perf_counter() - start_time
        
        # Prepare response message
        response_message = 'Leaderboard reset completed successfully'
        if is_scheduled_expiry and result.get('renewalInfo', {}).get('expiryRemoved'):
            response_message += ' and expiry cleared for renewal'
        
        # COMPLETION LOGGING
        logger.info(f"=== LAMBDA HANDLER COMPLETION ===")
        logger.info(f"Status: 200, Processing time: {int(total_time * 1000)}ms")
        logger.info(f"Entries removed: {result.get('resetInfo', {}).get('entriesRemoved', 0)}")
        
        # Return success response
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache'
            },
            'body': json.dumps({
                'resetLeaderboardResponse': {
                    'message': response_message,
                    'resetResults': result,
                    'metadata': {
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'requestId': context.aws_request_id,
                        'processingTimeMs': int(total_time * 1000),
                        'executionType': 'scheduled_expiry' if is_scheduled_expiry else 'manual_reset',
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
            if 'leaderboardName' in state:
                run_async(release_reset_lock_async(state['leaderboardName']))
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