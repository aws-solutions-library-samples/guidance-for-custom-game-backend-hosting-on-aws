# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
leaderboardsConfig.py -- Unified Leaderboard Configuration Management Lambda Function

All leaderboard configuration operations:
- Create configuration
- Get single configuration  
- Get all configurations
- Update configuration
- Delete configuration

Supports multiple path patterns for flexibility:
- POST /config or /config/create - Create configuration
- GET/POST /config/get - Get single configuration
- GET/POST /configs or /config/all - Get all configurations
- PUT /config or /config/update - Update configuration
- DELETE /config or /config/delete - Delete configuration

Updated for Python 3.13 and Valkey-GLIDE 2.0.1
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

# Operation-specific settings
VALIDATE_VALKEY = os.environ.get('VALIDATE_VALKEY', 'true').lower() == 'true'
INCLUDE_LEADERBOARD_DATA = os.environ.get('INCLUDE_LEADERBOARD_DATA', 'true').lower() == 'true'
FORCE_DELETE = os.environ.get('FORCE_DELETE', 'false').lower() == 'true'
BACKUP_BEFORE_DELETE = os.environ.get('BACKUP_BEFORE_DELETE', 'true').lower() == 'true'
BACKUP_BEFORE_MIGRATION = os.environ.get('BACKUP_BEFORE_MIGRATION', 'true').lower() == 'true'
ENABLE_ROLLBACK = os.environ.get('ENABLE_ROLLBACK', 'true').lower() == 'true'
MIGRATION_TIMEOUT = int(os.environ.get('MIGRATION_TIMEOUT', '300'))
MIGRATION_BATCH_SIZE = int(os.environ.get('MIGRATION_BATCH_SIZE', '1000'))

# Performance optimization settings
MAX_CONCURRENT_OPERATIONS = int(os.environ.get('MAX_CONCURRENT_OPERATIONS', '10'))
CONNECTION_TIMEOUT = int(os.environ.get('GLIDE_CONNECTION_TIMEOUT_MS', '2000'))  # Optimized: 2000ms
REQUEST_TIMEOUT = int(os.environ.get('GLIDE_REQUEST_TIMEOUT_MS', '5000'))        # Increased for failover resilience (was 2500ms)

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

def sanitize_xray_data(data):
    """
    Sanitize data for X-Ray tracing to prevent serialization errors.
    Converts bytes to strings and ensures all keys are strings.
    """
    if isinstance(data, bytes):
        return data.decode('utf-8', errors='ignore')
    elif isinstance(data, dict):
        return {str(k): sanitize_xray_data(v) for k, v in data.items()}
    elif isinstance(data, (list, tuple)):
        return [sanitize_xray_data(item) for item in data]
    elif data is None:
        return None
    else:
        return str(data) if not isinstance(data, (int, float, bool)) else data

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
VALID_SORT_ORDERS = ["asc", "desc"]
VALID_TIME_FORMATS = ["seconds", "milliseconds", "minutes_seconds", "hours_minutes_seconds"]
VALID_SCORE_STRATEGIES = ["replace", "best", "cumulative"]
RESERVED_PREFIXES = ['aws', 'amazon']

# Score type-specific initialization configuration
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
                'body': json.dumps({'gameLeaderboardConfigResponse': {
                    'error': 'Not Found',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'gameLeaderboardConfigResponse': {
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
                'body': json.dumps({'gameLeaderboardConfigResponse': {
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
            elif error_code == 'ConditionalCheckFailedException':
                status_code = 409
                error_message = "A leaderboard with this name already exists or has been modified concurrently"
            
            return {
                'statusCode': status_code,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'gameLeaderboardConfigResponse': {
                    'error': error_code,
                    'message': error_message,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except RuntimeError as e:
            logger.error(f"Runtime error: {str(e)}")
            # Determine appropriate status code based on error type
            if "lock" in str(e).lower():
                status_code = 409  # Conflict - another operation in progress
                error_type = "Conflict"
            elif "permission" in str(e).lower():
                status_code = 403  # Forbidden
                error_type = "Forbidden"
            else:
                status_code = 500  # Internal Server Error
                error_type = "Internal Server Error"
                
            return {
                'statusCode': status_code,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'gameLeaderboardConfigResponse': {
                    'error': error_type,
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except ValueError as e:
            logger.error(f"API parameter error: {str(e)}")
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'gameLeaderboardConfigResponse': {
                    'error': 'Bad Request',
                    'message': f'API parameter error: {str(e)}',
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
                'body': json.dumps({'gameLeaderboardConfigResponse': {
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
                'body': json.dumps({'gameLeaderboardConfigResponse': {
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
    Validate authenticated context from API Gateway authorizer and ensure request matches authenticated credentials.
    
    Args:
        event: Lambda event containing authorizer context
        required_permission: Required permission level ('read' or 'write')
        
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
    
    # Validate request body gameID matches authenticated gameId
    if event.get('body'):
        try:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
            if 'gameLeaderboardConfigRequest' in body:
                request_game_id = body['gameLeaderboardConfigRequest'].get('gameID')
                if request_game_id and request_game_id != authenticated_game_id:
                    logger.error(f"Request gameID '{request_game_id}' does not match authenticated gameId '{authenticated_game_id}'")
                    raise LeaderboardAuthenticationError(f"Access denied - gameID mismatch. API key is authorized for gameID '{authenticated_game_id}' but request contains '{request_game_id}'")
        except json.JSONDecodeError:
            # JSON validation will be handled elsewhere, skip gameID validation for malformed JSON
            pass
    
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
    Get or create a high-performance Valkey client using GLIDE 2.0.1+ internal connection pooling.
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
                client_name="leaderboard-lambda",
                protocol=ProtocolVersion.RESP3
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
                client_name="leaderboard-lambda",
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
            thread_name_prefix="leaderboard-worker"
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
def validate_leaderboard_name(leaderboard_name: str) -> None:
    """
    Enhanced leaderboard name validation.
    """
    if len(leaderboard_name) < 3 or len(leaderboard_name) > 64:
        raise ValueError("Leaderboard name must be between 3 and 64 characters")
    
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', leaderboard_name):
        raise ValueError("Leaderboard name must start with a letter or number and contain only letters, numbers, hyphens, and underscores")
    
    for prefix in RESERVED_PREFIXES:
        if leaderboard_name.lower().startswith(prefix):
            raise ValueError(f"Leaderboard name cannot start with reserved prefix: {prefix}")


@tracer.capture_method
def validate_expiry_datetime(datetime_str: str) -> Tuple[str, int]:
    """
    Validate ISO datetime string for leaderboard expiry.
    """
    try:
        # Ensure input is a string, not bytes
        if isinstance(datetime_str, bytes):
            datetime_str = datetime_str.decode('utf-8')
        elif not isinstance(datetime_str, str):
            datetime_str = str(datetime_str)
        
        parsed_dt = dateutil.parser.isoparse(datetime_str)
        
        if parsed_dt.tzinfo is None:
            raise ValueError("DateTime must include timezone information")
        
        utc_dt = parsed_dt.astimezone(timezone.utc)
        current_utc = datetime.now(timezone.utc)
        
        if utc_dt <= current_utc:
            raise ValueError("Expiry datetime must be in the future")
        
        ttl_seconds = int((utc_dt - current_utc).total_seconds())
        
        max_ttl = 10 * 365 * 24 * 60 * 60
        if ttl_seconds > max_ttl:
            raise ValueError(f"Expiry datetime cannot be more than 10 years in the future")
        
        return utc_dt.isoformat(), ttl_seconds
        
    except dateutil.parser.ParserError as e:
        raise ValueError(f"Invalid ISO datetime format: {str(e)}")
    except Exception as e:
        raise ValueError(f"Invalid ISO datetime format - processing error: {str(e)}")


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
# SHARED ASYNC OPERATIONS
# ============================================================================

@tracer.capture_method
async def backup_sorted_list_async(sorted_list_name: str, for_operation: str = "migration") -> Optional[Dict[str, Any]]:
    """
    Asynchronously backup sorted list data.
    """
    should_backup = (
        (for_operation == "migration" and BACKUP_BEFORE_MIGRATION) or
        (for_operation == "deletion" and BACKUP_BEFORE_DELETE)
    )
    
    if not should_backup:
        return None
    
    try:
        client = await get_valkey_client()
        
        exists = await client.exists([sorted_list_name])
        if exists == 0:
            logger.info(f"Sorted list {sorted_list_name} does not exist, no backup needed")
            return None
        
        all_entries = await client.zrange_withscores(sorted_list_name, RangeByIndex(0, -1))
        ttl = await client.ttl(sorted_list_name)
        
        backup_data = {
            'sortedListName': sorted_list_name,
            'entries': all_entries,
            'ttl': ttl if ttl > 0 else None,
            'backupTimestamp': datetime.now(timezone.utc).isoformat(),
            'entryCount': len(all_entries) if all_entries else 0
        }
        
        logger.info(f"Successfully backed up sorted list {sorted_list_name} with {backup_data['entryCount']} entries")
        return backup_data
        
    except Exception as e:
        logger.warning(f"Failed to backup sorted list {sorted_list_name}: {str(e)}")
        if for_operation == "deletion" and not FORCE_DELETE:
            raise ConnectionError(f"Backup failed and FORCE_DELETE is disabled: {str(e)}")
        return None


# ============================================================================
# CREATE OPERATION
# ============================================================================

@tracer.capture_method
def validate_create_request(event: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    Validate request for creating leaderboard configuration.
    Returns tuple of (config, warnings_list)
    """
    warnings = []
    
    if 'body' not in event or event['body'] is None:
        raise ValueError("Request body is missing")
    
    try:
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in request body")
    
    if 'gameLeaderboardConfigRequest' not in body:
        raise ValueError("gameLeaderboardConfigRequest is missing in request body")
    
    config = body['gameLeaderboardConfigRequest']
    
    required_fields = [
        "gameID", "gameMode", "leaderboardName", 
        "statAttributeForLeaderboard", "leaderboardType", "scoreStrategy"
    ]
    
    for field in required_fields:
        if field not in config:
            raise ValueError(f"Required field '{field}' is missing in gameLeaderboardConfigRequest")
        if not isinstance(config[field], str) or not config[field].strip():
            raise ValueError(f"{field} must be a non-empty string")

    # Validate gameID format and length
    if len(config['gameID']) > 255:
        raise ValueError("gameID must not exceed 255 characters")
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', config['gameID']):
        raise ValueError("gameID must start with a letter or number and contain only letters, numbers, hyphens, and underscores")

    # Validate gameMode format and length
    if len(config['gameMode']) > 255:
        raise ValueError("gameMode must not exceed 255 characters")
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', config['gameMode']):
        raise ValueError("gameMode must start with a letter or number and contain only letters, numbers, hyphens, and underscores")

    validate_leaderboard_name(config['leaderboardName'])
    
    if config['leaderboardType'] not in VALID_LEADERBOARD_TYPES:
        raise ValueError(f"leaderboardType must be one of {VALID_LEADERBOARD_TYPES}")
    
    # Validate scoreStrategy (mandatory field)
    if config['scoreStrategy'] not in VALID_SCORE_STRATEGIES:
        raise ValueError(f"scoreStrategy must be one of {VALID_SCORE_STRATEGIES}. Options: 'replace' (always replace score), 'best' (keep better score), 'cumulative' (add to existing score for DESCENDING_LB or keep best for ASCENDING_LB)")
    
    # Validate time-based configurations
    if 'scoreType' in config:
        if not isinstance(config['scoreType'], str):
            raise ValueError("scoreType must be a string")
        
        if config['scoreType'] not in VALID_SCORE_TYPES:
            raise ValueError(f"scoreType must be one of {VALID_SCORE_TYPES}")
        
        if config['scoreType'] == 'time':
            if config['leaderboardType'] != 'ASCENDING_LB':
                warning_msg = f"Time-based leaderboard '{config['leaderboardName']}' should typically use ASCENDING_LB type (lower times are better)"
                logger.warning(warning_msg)
                warnings.append(warning_msg)
        
        if config['scoreType'] == 'rank':
            if config['leaderboardType'] != 'ASCENDING_LB':
                warning_msg = f"Rank-based leaderboard '{config['leaderboardName']}' should typically use ASCENDING_LB type (lower ranks are better)"
                logger.warning(warning_msg)
                warnings.append(warning_msg)
        
        # Time-specific field validation for both 'time' and 'rank' score types
        if config['scoreType'] in ['time', 'rank']:
            if 'timePrecision' in config:
                if not isinstance(config['timePrecision'], int) or config['timePrecision'] < 0 or config['timePrecision'] > 6:
                    raise ValueError("timePrecision must be an integer between 0 and 6")
            else:
                config['timePrecision'] = 3
            
            if 'timeFormat' in config:
                if config['timeFormat'] not in VALID_TIME_FORMATS:
                    raise ValueError(f"timeFormat must be one of {VALID_TIME_FORMATS}")
            else:
                config['timeFormat'] = "seconds"
            
            # IMPORTANT: minValidTimeInSeconds and maxValidTimeInSeconds are ALWAYS in seconds, regardless of timeFormat
            # The timeFormat only affects the format of the score input/display
            # The backend converts all time scores to seconds before validation (see batchStoreStatsAndScores.py)
            # Therefore, validation ranges must be in seconds
            # Maximum allowed: 24 hours = 86,400 seconds
            max_allowed_time = 86400.0
            default_min_time = '0.001'  # 0.001 seconds
            default_max_time = '86400.0'  # 24 hours in seconds
            
            if 'minValidTimeInSeconds' in config:
                try:
                    min_time = float(config['minValidTimeInSeconds'])
                    if min_time < 0:
                        raise ValueError("minValidTimeInSeconds must be non-negative")
                    if min_time > max_allowed_time:
                        raise ValueError(f"minValidTimeInSeconds cannot exceed {max_allowed_time:,.0f} seconds (24 hours)")
                    config['minValidTimeInSeconds'] = Decimal(str(min_time))
                except (ValueError, TypeError) as e:
                    if "minValidTimeInSeconds must be" in str(e) or "minValidTimeInSeconds cannot exceed" in str(e):
                        raise
                    raise ValueError("minValidTimeInSeconds must be a valid number")
            else:
                config['minValidTimeInSeconds'] = Decimal(default_min_time)
            
            if 'maxValidTimeInSeconds' in config:
                try:
                    max_time = float(config['maxValidTimeInSeconds'])
                    if max_time <= float(config['minValidTimeInSeconds']):
                        raise ValueError("maxValidTimeInSeconds must be greater than minValidTimeInSeconds")
                    if max_time > max_allowed_time:
                        raise ValueError(f"maxValidTimeInSeconds cannot exceed {max_allowed_time:,.0f} seconds (24 hours)")
                    config['maxValidTimeInSeconds'] = Decimal(str(max_time))
                except (ValueError, TypeError) as e:
                    if "maxValidTimeInSeconds must be" in str(e) or "maxValidTimeInSeconds cannot exceed" in str(e):
                        raise
                    raise ValueError("maxValidTimeInSeconds must be a valid number")
            else:
                config['maxValidTimeInSeconds'] = Decimal(default_max_time)
    
    if 'sortOrder' in config:
        if not isinstance(config['sortOrder'], str):
            raise ValueError("sortOrder must be a string")
        
        if config['sortOrder'] not in VALID_SORT_ORDERS:
            raise ValueError(f"sortOrder must be one of {VALID_SORT_ORDERS}")
        
        if config.get('scoreType') == 'time' and config['leaderboardType'] == 'ASCENDING_LB':
            if config['sortOrder'] != 'asc':
                warning_msg = f"Time-based ASCENDING_LB leaderboard '{config['leaderboardName']}' typically uses ascending sort order"
                logger.warning(warning_msg)
                warnings.append(warning_msg)
    
    if 'optionalLBExpiryDateTimeStamp' in config:
        try:
            normalized_datetime, ttl_seconds = validate_expiry_datetime(config['optionalLBExpiryDateTimeStamp'])
            config['_normalized_expiry_datetime'] = normalized_datetime
            config['_calculated_ttl_seconds'] = ttl_seconds
        except ValueError as e:
            raise ValueError(f"Invalid optionalLBExpiryDateTimeStamp: {str(e)}")
    
    if 'optionalLBReadOnlyOnExpiry' in config:
        if not isinstance(config['optionalLBReadOnlyOnExpiry'], bool):
            raise ValueError("optionalLBReadOnlyOnExpiry must be a boolean")
        
        # Only warn if user explicitly set this without expiry
        if 'optionalLBExpiryDateTimeStamp' not in config:
            warning_msg = f"optionalLBReadOnlyOnExpiry specified without expiry datetime for leaderboard '{config['leaderboardName']}'"
            logger.warning(warning_msg)
            warnings.append(warning_msg)
    else:
        # Only set default if expiry is provided
        if 'optionalLBExpiryDateTimeStamp' in config:
            config['optionalLBReadOnlyOnExpiry'] = True
            logger.info(f"Setting default optionalLBReadOnlyOnExpiry=True for leaderboard with expiry: {config['leaderboardName']}")
    
    # Validate maxEntries if provided
    if 'maxEntries' in config:
        if not isinstance(config['maxEntries'], int) or config['maxEntries'] <= 0:
            raise ValueError("maxEntries must be a positive integer")
        if config['maxEntries'] > 1000000:
            raise ValueError("maxEntries cannot exceed 1,000,000")
    
    # Validate score range for non-time score types
    if config.get('scoreType', 'score') != 'time':
        if 'minValidScore' in config:
            try:
                min_score = float(config['minValidScore'])
                
                # Validate score type-specific constraints
                score_type = config.get('scoreType', 'score')
                if score_type == 'rank':
                    if min_score < 1:
                        raise ValueError("minValidScore for rank type must be >= 1 (ranks start at 1)")
                    if min_score > 1000000000:  # 1 billion max rank
                        raise ValueError("minValidScore for rank type cannot exceed 1,000,000,000 (1 Billion)")
                elif score_type in ['score', 'distance', 'points', 'level']:
                    if min_score < 0:
                        raise ValueError(f"minValidScore for {score_type} type must be non-negative")
                    if min_score > 1000000000000:  # 1 trillion max score
                        raise ValueError(f"minValidScore for {score_type} type cannot exceed 1,000,000,000,000 (1 Trillion)")
                
                config['minValidScore'] = Decimal(str(min_score))
            except (ValueError, TypeError):
                raise ValueError("minValidScore must be a valid number")
        
        if 'maxValidScore' in config:
            try:
                max_score = float(config['maxValidScore'])
                if 'minValidScore' in config and max_score <= float(config['minValidScore']):
                    raise ValueError("maxValidScore must be greater than minValidScore")
                
                # Add upper bound validation for maxValidScore
                score_type = config.get('scoreType', 'score')
                if score_type == 'rank':
                    if max_score > 1000000000:  # 1 billion max rank
                        raise ValueError("maxValidScore for rank type cannot exceed 1,000,000,000")
                elif score_type in ['score', 'distance', 'points', 'level']:
                    if max_score > 1000000000000:  # 1 trillion max score
                        raise ValueError(f"maxValidScore for {score_type} type cannot exceed 1,000,000,000,000")
                
                config['maxValidScore'] = Decimal(str(max_score))
            except (ValueError, TypeError):
                raise ValueError("maxValidScore must be a valid number")
    
    return config, warnings


@tracer.capture_method
async def check_leaderboard_exists_async(leaderboard_name: str) -> bool:
    """
    Asynchronously check if a leaderboard configuration already exists.
    """
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            get_thread_pool(),
            lambda: leaderboards_config_table.get_item(
                Key={'leaderboardName': leaderboard_name}
            )
        )
        return 'Item' in response
    except ClientError as e:
        logger.error(f"Error checking leaderboard existence: {str(e)}")
        raise


@tracer.capture_method
async def create_sorted_set_async(sorted_list_name: str, score_type: str = "score", ttl: Optional[int] = None) -> None:
    logger.info(f"=== CREATE_SORTED_SET_ASYNC START === Key: {sorted_list_name}, ScoreType: {score_type}")
    
    try:
        logger.info("Attempting to get Valkey client...")
        client = await get_valkey_client()
        logger.info("Successfully obtained Valkey client")
        
        # Test connection first
        logger.info("Testing Valkey connection with PING...")
        ping_result = await client.ping()
        logger.info(f"PING result: {ping_result}")
        
        # Check current key count
        logger.info("Checking current DBSIZE...")
        dbsize_before = await client.dbsize()
        logger.info(f"DBSIZE before creation: {dbsize_before}")
        
        # Get score type-specific initialization config
        init_config = INIT_CONFIG.get(score_type, INIT_CONFIG["score"])
        init_key = init_config["key"]
        init_value = init_config["value"]
        
        logger.info(f"Using init config for score type '{score_type}': key='{init_key}', value={init_value}")
        
        # Create sorted set with score type-specific placeholder
        logger.info(f"Executing ZADD {sorted_list_name} {init_key} {init_value}...")
        result = await client.zadd(sorted_list_name, {init_key: init_value})
        logger.info(f"ZADD result: {result}")

        # Verify the key was created
        logger.info(f"Verifying key exists: {sorted_list_name}")
        exists_check = await client.exists([sorted_list_name])
        logger.info(f"EXISTS check result: {exists_check}")
        
        if exists_check == 0:
            logger.error(f"CRITICAL: Key {sorted_list_name} not found after ZADD")
            raise ConnectionError(f"Sorted set {sorted_list_name} was not created - EXISTS check failed")

        # Check DBSIZE after creation
        dbsize_after = await client.dbsize()
        logger.info(f"DBSIZE after creation: {dbsize_after}")

        # Remove the initialization entry
#       logger.info(f"Removing init entry with ZREM {sorted_list_name} _init_...")
#       zrem_result = await client.zrem(sorted_list_name, ["_init_"])
#       logger.info(f"ZREM result: {zrem_result}")

        # Final verification
        logger.info("Final verification that empty sorted set exists...")
        final_exists = await client.exists([sorted_list_name])
        logger.info(f"Final EXISTS check: {final_exists}")
        
        if final_exists == 0:
            logger.warning(f"Key disappeared after ZREM, recreating as empty sorted set")
            # Create with temporary member
            temp_zadd_result = await client.zadd(sorted_list_name, {"_temp_": 0})
            logger.info(f"Temporary ZADD result: {temp_zadd_result}")
            
            # Verify it was created
            verify_exists = await client.exists([sorted_list_name])
            logger.info(f"Verification EXISTS check: {verify_exists}")
            
            if verify_exists == 1:
                # Remove temporary member to create empty sorted set
                temp_zrem_result = await client.zrem(sorted_list_name, ["_temp_"])
                logger.info(f"Temporary ZREM result: {temp_zrem_result}")
                logger.info("Successfully recreated empty sorted set")
            else:
                logger.error("Failed to create sorted set with temporary member")

        # Set TTL if specified
        if ttl and ttl > 0:
            logger.info(f"Setting TTL {ttl} seconds on {sorted_list_name}")
            expire_result = await client.expire(sorted_list_name, ttl)
            logger.info(f"EXPIRE result: {expire_result}")

        # Final DBSIZE check
        final_dbsize = await client.dbsize()
        logger.info(f"Final DBSIZE: {final_dbsize}")
        
        logger.info(f"=== CREATE_SORTED_SET_ASYNC SUCCESS === Key: {sorted_list_name}")

    except Exception as e:
        logger.error(f"=== CREATE_SORTED_SET_ASYNC ERROR === Key: {sorted_list_name}, Error: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error details: {repr(e)}")
        raise

@tracer.capture_method
async def store_leaderboard_config_async(config: Dict[str, Any]) -> Tuple[str, str]:
    """
    Asynchronously store leaderboard configuration in DynamoDB.
    """
    current_timestamp = int(time.time())
    current_datetime = datetime.fromtimestamp(current_timestamp, timezone.utc).isoformat()
    
    sorted_list_name = generate_sorted_list_name(
        config['gameID'],
        config['gameMode'],
        config['leaderboardName']
    )
    
    item = {
        'leaderboardName': config['leaderboardName'],
        'gameID': config['gameID'],
        'gameMode': config['gameMode'],
        'statAttributeForLeaderboard': config['statAttributeForLeaderboard'],
        'leaderboardType': config['leaderboardType'],
        'scoreStrategy': config['scoreStrategy'],
        'sortedListName': sorted_list_name,
        'createdAt': current_timestamp,
        'createdAtISO': current_datetime
    }
    
    time_specific_fields = [
        'scoreType', 'sortOrder', 'timePrecision', 'timeFormat', 
        'minValidTimeInSeconds', 'maxValidTimeInSeconds'
    ]
    
    for field in time_specific_fields:
        if field in config:
            item[field] = config[field]
    
    if 'optionalLBExpiryDateTimeStamp' in config:
        item['optionalLBExpiryDateTimeStamp'] = config['_normalized_expiry_datetime']
        item['optionalLBReadOnlyOnExpiry'] = config['optionalLBReadOnlyOnExpiry']
    
    optional_fields = ['maxEntries', 'description', 'tags', 'studioId', 'gameId', 
                      'studioName', 'gameTitle', 'createdBy', 'minValidScore', 'maxValidScore']
    for field in optional_fields:
        if field in config:
            item[field] = config[field]
    
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            get_thread_pool(),
            lambda: leaderboards_config_table.put_item(
                Item=item,
                ConditionExpression='attribute_not_exists(leaderboardName)'
            )
        )
        
        logger.info(f"Successfully stored leaderboard configuration: {config['leaderboardName']}")
        return sorted_list_name, current_datetime
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise ValueError(f"A leaderboard with the name '{config['leaderboardName']}' already exists")
        else:
            logger.error(f"Error storing leaderboard configuration: {str(e)}")
            raise


async def handle_create_leaderboard(event: Dict[str, Any], auth_context: Dict[str, str]) -> Dict[str, Any]:
    """
    Handle create leaderboard configuration operation.
    """
    logger.info("=== HANDLE_CREATE_LEADERBOARD ENTRY ===")
    start_time = time.perf_counter()
    
    try:
        config, validation_warnings = validate_create_request(event)
        
        config['studioId'] = auth_context['studioId']
        config['gameId'] = auth_context['gameId']
        config['studioName'] = auth_context['studioName']
        config['gameTitle'] = auth_context['gameTitle']
        config['createdBy'] = auth_context['contactEmail']
        
        leaderboard_name = config['leaderboardName']
        logger.info(f"Processing leaderboard creation: {leaderboard_name}")
        
        exists = await check_leaderboard_exists_async(leaderboard_name)
        if exists:
            raise ValueError(f"A leaderboard with the name '{leaderboard_name}' already exists")
        
        logger.info("Storing leaderboard configuration in DynamoDB...")
        sorted_list_name, created_datetime = await store_leaderboard_config_async(config)
        logger.info(f"DynamoDB storage successful. Sorted list name: {sorted_list_name}")
        
        # MemoryDB sorted set creation with enhanced logging
        ttl = config.get('_calculated_ttl_seconds')
        read_only_on_expiry = config.get('optionalLBReadOnlyOnExpiry', True)
        
        # If read-only mode is enabled, don't set TTL on MemoryDB (keep data for read-only access)
        memorydb_ttl = None if read_only_on_expiry else ttl
        
        score_type = config.get('scoreType', 'score')  # Default to 'score' if not specified
        logger.info(f"=== STARTING MEMORYDB SORTED SET CREATION === Name: {sorted_list_name}, ScoreType: {score_type}, ConfigTTL: {ttl}, ReadOnlyOnExpiry: {read_only_on_expiry}, MemoryDBTTL: {memorydb_ttl}")
        
        try:
            # Create sorted set with conditional TTL based on read-only flag
            await create_sorted_set_async(sorted_list_name, score_type, memorydb_ttl)
            logger.info(f"create_sorted_set_async completed successfully")
            
            # CRITICAL: Verify sorted set was actually created
            logger.info("Starting post-creation verification...")
            client = await get_valkey_client()
            
            # Additional verification steps
            logger.info("Testing Valkey client connection...")
            ping_result = await client.ping()
            logger.info(f"Post-creation PING result: {ping_result}")
            
            logger.info(f"Checking if key exists: {sorted_list_name}")
            verification_exists = await client.exists([sorted_list_name])
            logger.info(f"Post-creation EXISTS verification: {verification_exists}")
            
            # Check current DBSIZE
            current_dbsize = await client.dbsize()
            logger.info(f"Current DBSIZE after creation: {current_dbsize}")
            
            if verification_exists == 0:
                logger.error(f"CRITICAL FAILURE: Sorted set {sorted_list_name} does not exist after creation")
                logger.error("This indicates the MemoryDB operation failed silently")
                
                # Get all keys to debug
                all_keys = await client.keys("*")
                logger.error(f"All keys in MemoryDB: {all_keys}")
                
                # Rollback DynamoDB entry
                logger.info("Rolling back DynamoDB entry...")
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    get_thread_pool(),
                    lambda: leaderboards_config_table.delete_item(
                        Key={'leaderboardName': leaderboard_name}
                    )
                )
                logger.info("DynamoDB rollback completed")
                raise ConnectionError(f"Critical: Sorted set creation verification failed for {sorted_list_name}")

            logger.info(f"=== MEMORYDB SORTED SET CREATION VERIFIED === Name: {sorted_list_name}")

        except Exception as memorydb_error:
            logger.error(f"=== MEMORYDB OPERATION FAILED === Error: {str(memorydb_error)}")
            logger.error(f"Error type: {type(memorydb_error).__name__}")
            logger.error(f"Error details: {repr(memorydb_error)}")
            
            # Enhanced error handling with specific rollback logic
            logger.error(f"Creation failed: {str(memorydb_error)}")
            try:
                logger.info("Attempting DynamoDB rollback after MemoryDB failure...")
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    get_thread_pool(),
                    lambda: leaderboards_config_table.delete_item(
                        Key={'leaderboardName': leaderboard_name}
                    )
                )
                logger.info(f"Successfully rolled back DynamoDB entry for {leaderboard_name}")
            except Exception as rollback_error:
                logger.error(f"Failed to rollback DynamoDB entry: {str(rollback_error)}")
            raise memorydb_error
        
        # Build consistent response with gameLeaderboardConfigRequest
        created_config = {
            'leaderboardName': config['leaderboardName'],
            'gameID': config['gameID'],
            'gameMode': config['gameMode'],
            'statAttributeForLeaderboard': config['statAttributeForLeaderboard'],
            'leaderboardType': config['leaderboardType'],
            'scoreStrategy': config['scoreStrategy'],
            'sortedListName': sorted_list_name,
            'createdAt': int(time.time()),
            'createdAtISO': created_datetime,
            'studioId': config['studioId'],
            'gameId': config['gameId'],
            'studioName': config['studioName'],
            'gameTitle': config['gameTitle'],
            'createdBy': config['createdBy']
        }
        
        # Add optional fields if present
        optional_fields = ['optionalLBExpiryDateTimeStamp', 'optionalLBReadOnlyOnExpiry',
                          'scoreType', 'sortOrder', 'maxEntries', 'timePrecision',
                          'timeFormat', 'minValidTimeInSeconds', 'maxValidTimeInSeconds',
                          'minValidScore', 'maxValidScore', 'description', 'tags']
        for field in optional_fields:
            if field in config:
                if field == 'optionalLBExpiryDateTimeStamp':
                    created_config[field] = config['_normalized_expiry_datetime']
                else:
                    created_config[field] = config[field]
        
        response_data = {
            'message': 'Leaderboard configuration created successfully',
            'leaderboardConfig': created_config,
            'success': True
        }
        
        # Add potential issues (warnings) if any
        if validation_warnings:
            response_data['potentialIssues'] = validation_warnings
        
        if 'optionalLBExpiryDateTimeStamp' in config:
            response_data['expiryScheduling'] = {
                'expiryDateTime': config['_normalized_expiry_datetime'],
                'readOnlyOnExpiry': config['optionalLBReadOnlyOnExpiry'],
                'resetScheduled': not config['optionalLBReadOnlyOnExpiry'],
                'memoryDbTtlSet': not config['optionalLBReadOnlyOnExpiry'],
                'behavior': 'read-only preservation' if config['optionalLBReadOnlyOnExpiry'] else 'auto-deletion on expiry',
                'explanation': {
                    'memoryDbBehavior': 'Data will be preserved in MemoryDB for continued read-only access after expiry' if config['optionalLBReadOnlyOnExpiry'] else 'Data will be automatically deleted from MemoryDB when the leaderboard expires',
                    'applicationBehavior': 'Write operations will be rejected after expiry, but leaderboard remains queryable' if config['optionalLBReadOnlyOnExpiry'] else 'Leaderboard will be completely removed and unavailable after expiry',
                    'dataRetention': 'Historical data preserved in DynamoDB for analytics' if config['optionalLBReadOnlyOnExpiry'] else 'DynamoDB configuration data preserved for analytics'
                }
            }
        
        processing_time = time.perf_counter() - start_time
        logger.info(f"=== HANDLE_CREATE_LEADERBOARD EXIT === Processing time: {int(processing_time * 1000)}ms")
        return response_data

    except Exception as e:
        processing_time = time.perf_counter() - start_time
        logger.error(f"=== HANDLE_CREATE_LEADERBOARD ERROR EXIT === Processing time: {int(processing_time * 1000)}ms, Error: {str(e)}")
        raise


# ============================================================================
# GET SINGLE CONFIGURATION OPERATION
# ============================================================================

@tracer.capture_method
def validate_get_request(event: Dict[str, Any]) -> str:
    """
    Validate request for getting single leaderboard configuration.
    """
    if 'body' not in event or event['body'] is None:
        raise ValueError("Request body is missing")
    
    try:
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in request body")
    
    if 'gameLeaderboardConfigRequest' not in body:
        raise ValueError("gameLeaderboardConfigRequest is missing in request body")
    
    config = body['gameLeaderboardConfigRequest']
    
    if 'leaderboardName' not in config:
        raise ValueError("leaderboardName is missing in gameLeaderboardConfigRequest")
    
    leaderboard_name = config['leaderboardName']
    
    if not isinstance(leaderboard_name, str) or not leaderboard_name.strip():
        raise ValueError("leaderboardName must be a non-empty string")
    
    if len(leaderboard_name) > 255:
        raise ValueError("leaderboardName must be 255 characters or less")
    
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', leaderboard_name.strip()):
        raise ValueError("leaderboardName contains invalid characters")
    
    return leaderboard_name.strip()


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
async def validate_sorted_list_existence_async(sorted_list_name: str) -> Dict[str, Any]:
    """
    Asynchronously validate that a sorted list exists in Valkey and get metadata.
    """
    if not VALIDATE_VALKEY:
        return {'validated': False, 'reason': 'Valkey validation disabled'}
    
    try:
        client = await get_valkey_client()
        
        exists_result = await client.exists([sorted_list_name])
        exists = exists_result > 0
        
        validation_info = {
            'validated': True,
            'exists': exists,
            'sortedListName': sorted_list_name
        }
        
        if exists and INCLUDE_LEADERBOARD_DATA:
            try:
                zcard_result = await client.zcard(sorted_list_name)
                validation_info['entryCount'] = zcard_result
                
                if zcard_result > 0:
                    zrange_min = await client.zrange_withscores(sorted_list_name, RangeByIndex(0, 0))
                    zrange_max = await client.zrange_withscores(sorted_list_name, RangeByIndex(-1, -1))
                    
                    if zrange_min and len(zrange_min) > 0:
                        validation_info['minScore'] = zrange_min[0][1] if len(zrange_min[0]) > 1 else None
                    if zrange_max and len(zrange_max) > 0:
                        validation_info['maxScore'] = zrange_max[0][1] if len(zrange_max[0]) > 1 else None
                        
            except Exception as e:
                logger.warning(f"Could not retrieve additional sorted list info: {str(e)}")
                validation_info['additionalInfoError'] = str(e)
        
        logger.info(f"Valkey validation for {sorted_list_name}: exists={exists}")
        return validation_info
        
    except Exception as e:
        logger.warning(f"Valkey validation failed: {str(e)}")
        return {
            'validated': True,
            'exists': None,
            'error': str(e),
            'sortedListName': sorted_list_name
        }


async def handle_get_leaderboard(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle get single leaderboard configuration operation.
    """
    logger.info("=== HANDLE_GET_LEADERBOARD ENTRY ===")
    start_time = time.perf_counter()
    
    try:
        leaderboard_name = validate_get_request(event)
        
        leaderboard_config = await get_leaderboard_config_async(leaderboard_name)
        
        valkey_validation = None
        sorted_list_name = leaderboard_config.get('sortedListName')
        
        if sorted_list_name and (VALIDATE_VALKEY or INCLUDE_LEADERBOARD_DATA):
            valkey_validation = await validate_sorted_list_existence_async(sorted_list_name)
        
        response_data = {
            'leaderboardConfig': leaderboard_config,
            'success': True
        }
        
        if valkey_validation is not None:
            response_data['valkeyValidation'] = valkey_validation
        
        response_data['metadata'] = {
            'leaderboardName': leaderboard_name,
            'valkeyValidated': VALIDATE_VALKEY,
            'includeLeaderboardData': INCLUDE_LEADERBOARD_DATA,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        processing_time = time.perf_counter() - start_time
        logger.info(f"=== HANDLE_GET_LEADERBOARD EXIT === Processing time: {int(processing_time * 1000)}ms")
        return response_data

    except Exception as e:
        processing_time = time.perf_counter() - start_time
        logger.error(f"=== HANDLE_GET_LEADERBOARD ERROR EXIT === Processing time: {int(processing_time * 1000)}ms, Error: {str(e)}")
        raise


# ============================================================================
# GET ALL CONFIGURATIONS OPERATION
# ============================================================================

@tracer.capture_method
def validate_get_all_request(event: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Validate request for getting all leaderboard configurations.
    Supports both POST (with JSON body) and GET (with query parameters).
    """
    # Check if this is a GET request with query parameters
    if event.get('httpMethod') == 'GET' and event.get('queryStringParameters'):
        query_params = event['queryStringParameters'] or {}
        
        if 'gameID' not in query_params:
            raise ValueError("gameID is missing in query parameters")
        
        game_id = query_params['gameID']
        
        if not isinstance(game_id, str) or not game_id.strip():
            raise ValueError("gameID must be a non-empty string")
        
        if len(game_id) > 255:
            raise ValueError("gameID must be 255 characters or less")
        
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', game_id.strip()):
            raise ValueError("gameID contains invalid characters")
        
        game_mode = None
        if 'gameMode' in query_params and query_params['gameMode']:
            if not isinstance(query_params['gameMode'], str):
                raise ValueError("gameMode must be a string")
            game_mode = query_params['gameMode'].strip()
            if len(game_mode) > 255:
                raise ValueError("gameMode must be 255 characters or less")
            if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', game_mode):
                raise ValueError("gameMode contains invalid characters")
        
        return {
            'gameID': game_id.strip(),
            'gameMode': game_mode
        }
    
    # Handle POST request with JSON body (existing logic)
    if 'body' not in event:
        raise ValueError("Request body is missing")
    
    try:
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in request body")
    
    if 'gameLeaderboardConfigRequest' not in body:
        raise ValueError("gameLeaderboardConfigRequest is missing in request body")
    
    config = body['gameLeaderboardConfigRequest']
    
    if 'gameID' not in config:
        raise ValueError("gameID is missing in gameLeaderboardConfigRequest")
    
    game_id = config['gameID']
    
    if not isinstance(game_id, str) or not game_id.strip():
        raise ValueError("gameID must be a non-empty string")
    
    if len(game_id) > 255:
        raise ValueError("gameID must be 255 characters or less")
    
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', game_id.strip()):
        raise ValueError("gameID contains invalid characters")
    
    game_mode = None
    if 'gameMode' in config and config['gameMode']:
        if not isinstance(config['gameMode'], str):
            raise ValueError("gameMode must be a string")
        game_mode = config['gameMode'].strip()
        if len(game_mode) > 255:
            raise ValueError("gameMode must be 255 characters or less")
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', game_mode):
            raise ValueError("gameMode contains invalid characters")
    
    return {
        'gameID': game_id.strip(),
        'gameMode': game_mode
    }


@tracer.capture_method
async def get_all_leaderboard_configs_async(game_id: str, game_mode: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Asynchronously retrieve all leaderboard configurations matching criteria.
    """
    try:
        loop = asyncio.get_event_loop()
        
        async def query_dynamodb():
            if game_mode:
                logger.info(f"Querying leaderboards for gameID: {game_id} and gameMode: {game_mode}")
                response = await loop.run_in_executor(
                    get_thread_pool(),
                    lambda: leaderboards_config_table.query(
                        IndexName='gameID-gameMode-index',
                        KeyConditionExpression='gameID = :gameID AND gameMode = :gameMode',
                        ExpressionAttributeValues={
                            ':gameID': game_id,
                            ':gameMode': game_mode
                        },
                        ConsistentRead=False,
                        ScanIndexForward=True
                    )
                )
            else:
                logger.info(f"Querying leaderboards for gameID: {game_id} (all gameModes)")
                response = await loop.run_in_executor(
                    get_thread_pool(),
                    lambda: leaderboards_config_table.query(
                        IndexName='gameID-gameMode-index',
                        KeyConditionExpression='gameID = :gameID',
                        ExpressionAttributeValues={
                            ':gameID': game_id
                        },
                        ConsistentRead=False,
                        ScanIndexForward=True
                    )
                )
            
            items = response.get('Items', [])
            
            while 'LastEvaluatedKey' in response:
                logger.info("Paginating through additional leaderboard results")
                last_key = response['LastEvaluatedKey']
                
                if game_mode:
                    response = await loop.run_in_executor(
                        get_thread_pool(),
                        lambda: leaderboards_config_table.query(
                            IndexName='gameID-gameMode-index',
                            KeyConditionExpression='gameID = :gameID AND gameMode = :gameMode',
                            ExpressionAttributeValues={
                                ':gameID': game_id,
                                ':gameMode': game_mode
                            },
                            ExclusiveStartKey=last_key,
                            ConsistentRead=False,
                            ScanIndexForward=True
                        )
                    )
                else:
                    response = await loop.run_in_executor(
                        get_thread_pool(),
                        lambda: leaderboards_config_table.query(
                            IndexName='gameID-gameMode-index',
                            KeyConditionExpression='gameID = :gameID',
                            ExpressionAttributeValues={
                                ':gameID': game_id
                            },
                            ExclusiveStartKey=last_key,
                            ConsistentRead=False,
                            ScanIndexForward=True
                        )
                    )
                
                items.extend(response.get('Items', []))
            
            return items
        
        items = await query_dynamodb()
        
        if not items:
            # Return empty list for no results - this is a valid successful query
            # HTTP 200 will be returned with empty leaderboardConfigs array
            # Include a message to inform the client
            if game_mode:
                message = f"No leaderboard configurations found for gameID '{game_id}' and gameMode '{game_mode}'"
            else:
                message = f"No leaderboard configurations found for gameID '{game_id}'"
            
            logger.info(message)
            # Return tuple: (items, message) so caller can include message in metadata
            return ([], message)
        
        logger.info(f"Successfully retrieved {len(items)} leaderboard configurations")
        # Return tuple: (items, None) for consistency
        return (items, None)
        
    except ClientError as e:
        logger.error(f"Error querying leaderboard configurations: {str(e)}")
        raise


@tracer.capture_method
async def validate_sorted_lists_existence_async(leaderboard_configs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Asynchronously validate that sorted lists exist in Valkey and get metadata.
    """
    if not VALIDATE_VALKEY:
        return leaderboard_configs
    
    try:
        client = await get_valkey_client()
        
        sorted_list_names = []
        config_map = {}
        
        for config in leaderboard_configs:
            sorted_list_name = config.get('sortedListName')
            if sorted_list_name:
                sorted_list_names.append(sorted_list_name)
                config_map[sorted_list_name] = config
        
        if not sorted_list_names:
            logger.warning("No sorted list names found in configurations")
            return leaderboard_configs
        
        logger.info(f"Checking existence of {len(sorted_list_names)} sorted lists in Valkey")
        
        # Check each key individually since EXISTS with multiple keys returns total count, not array
        existence_map = {}
        for sorted_list_name in sorted_list_names:
            try:
                exists_result = await client.exists([sorted_list_name])
                existence_map[sorted_list_name] = exists_result > 0
            except Exception as e:
                logger.warning(f"Failed to check existence of {sorted_list_name}: {str(e)}")
                existence_map[sorted_list_name] = False
        
        metadata_map = {}
        if INCLUDE_LEADERBOARD_DATA:
            for sorted_list_name in sorted_list_names:
                if existence_map.get(sorted_list_name, False):
                    try:
                        # Get comprehensive metadata about the sorted set
                        zcard_result = await client.zcard(sorted_list_name)
                        metadata = {
                            'entryCount': zcard_result,
                            'keyExists': True
                        }
                        
                        if zcard_result > 0:
                            # Get min and max scores using simple range queries
                            try:
                                # Get highest score (first element with reverse=True)
                                max_result = await client.zrange_withscores(
                                    sorted_list_name,
                                    RangeByIndex(0, 0),
                                    reverse=True
                                )
                                
                                # Handle different GLIDE return formats
                                if max_result:
                                    if isinstance(max_result, dict) and max_result:
                                        # Dictionary format: {player: score}
                                        player_id, score = next(iter(max_result.items()))
                                        metadata['maxPlayer'] = sanitize_xray_data(player_id)
                                        metadata['maxScore'] = float(score)
                                    elif isinstance(max_result, list) and len(max_result) > 0:
                                        # List format: [(player, score)]
                                        player_id, score = max_result[0]
                                        metadata['maxPlayer'] = sanitize_xray_data(player_id)
                                        metadata['maxScore'] = float(score)
                                
                                # Get lowest score (first element without reverse)
                                min_result = await client.zrange_withscores(
                                    sorted_list_name,
                                    RangeByIndex(0, 0),
                                    reverse=False
                                )
                                
                                # Handle different GLIDE return formats
                                if min_result:
                                    if isinstance(min_result, dict) and min_result:
                                        # Dictionary format: {player: score}
                                        player_id, score = next(iter(min_result.items()))
                                        metadata['minPlayer'] = sanitize_xray_data(player_id)
                                        metadata['minScore'] = float(score)
                                    elif isinstance(min_result, list) and len(min_result) > 0:
                                        # List format: [(player, score)]
                                        player_id, score = min_result[0]
                                        metadata['minPlayer'] = sanitize_xray_data(player_id)
                                        metadata['minScore'] = float(score)
                                        
                            except Exception as range_error:
                                logger.warning(f"Could not get min/max for {sorted_list_name}: {str(range_error)}, errorType: {type(range_error).__name__}")
                            
                            # Get TTL if set
                            try:
                                ttl_result = await client.ttl(sorted_list_name)
                                if ttl_result > 0:
                                    metadata['ttlSeconds'] = ttl_result
                                elif ttl_result == -1:
                                    metadata['ttlSeconds'] = None  # No expiry
                                else:
                                    metadata['ttlSeconds'] = 0  # Expired or doesn't exist
                            except Exception as ttl_error:
                                logger.warning(f"Could not get TTL for {sorted_list_name}: {str(ttl_error)}")
                        else:
                            metadata['isEmpty'] = True
                        
                        metadata_map[sorted_list_name] = metadata
                        
                    except Exception as e:
                        logger.warning(f"Could not retrieve metadata for {sorted_list_name}: {str(e)} (type: {type(e).__name__})")
                        # Ensure we maintain keyExists=True since the key was confirmed to exist
                        metadata_map[sorted_list_name] = {
                            'keyExists': True,
                            'error': str(e),
                            'errorType': type(e).__name__,
                            'entryCount': 0
                        }
                else:
                    # Key doesn't exist, but still provide metadata
                    metadata_map[sorted_list_name] = {'keyExists': False, 'entryCount': 0}
        
        enhanced_configs = []
        for config in leaderboard_configs:
            enhanced_config = dict(config)
            sorted_list_name = config.get('sortedListName')
            
            if sorted_list_name:
                enhanced_config['valkeyExists'] = existence_map.get(sorted_list_name, False)
                if INCLUDE_LEADERBOARD_DATA and sorted_list_name in metadata_map:
                    enhanced_config['valkeyMetadata'] = metadata_map[sorted_list_name]
            else:
                enhanced_config['valkeyExists'] = False
            
            enhanced_configs.append(enhanced_config)
        
        existing_count = sum(1 for config in enhanced_configs if config.get('valkeyExists', False))
        logger.info(f"Valkey validation complete: {existing_count}/{len(enhanced_configs)} sorted lists exist")
        
        return enhanced_configs
        
    except Exception as e:
        logger.warning(f"Valkey validation failed: {str(e)}")
        enhanced_configs = []
        for config in leaderboard_configs:
            enhanced_config = dict(config)
            enhanced_config['valkeyExists'] = None
            enhanced_config['valkeyValidationError'] = str(e)
            enhanced_configs.append(enhanced_config)
        return enhanced_configs


async def handle_get_all_leaderboards(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle get all leaderboard configurations operation.
    """
    logger.info("=== HANDLE_GET_ALL_LEADERBOARDS ENTRY ===")
    start_time = time.perf_counter()
    
    try:
        params = validate_get_all_request(event)
        
        game_id = params['gameID']
        game_mode = params['gameMode']
        
        # Get leaderboard configs - returns tuple (configs, message)
        result = await get_all_leaderboard_configs_async(game_id, game_mode)
        leaderboard_configs, info_message = result if isinstance(result, tuple) else (result, None)
        
        if VALIDATE_VALKEY or INCLUDE_LEADERBOARD_DATA:
            leaderboard_configs = await validate_sorted_lists_existence_async(leaderboard_configs)
        
        response_metadata = {
            'count': len(leaderboard_configs),
            'gameID': game_id,
            'gameMode': game_mode if game_mode else "all",
            'valkeyValidated': VALIDATE_VALKEY,
            'includeLeaderboardData': INCLUDE_LEADERBOARD_DATA,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Include informational message if present (e.g., "No leaderboards found")
        if info_message:
            response_metadata['message'] = info_message
        
        processing_time = time.perf_counter() - start_time
        logger.info(f"=== HANDLE_GET_ALL_LEADERBOARDS EXIT === Processing time: {int(processing_time * 1000)}ms")
        
        return {
            'leaderboardConfigs': leaderboard_configs,
            'metadata': response_metadata,
            'success': True
        }

    except Exception as e:
        processing_time = time.perf_counter() - start_time
        logger.error(f"=== HANDLE_GET_ALL_LEADERBOARDS ERROR EXIT === Processing time: {int(processing_time * 1000)}ms, Error: {str(e)}")
        raise

    processing_time = time.perf_counter() - start_time
    logger.info(f"=== HANDLE_GET_ALL_LEADERBOARDS EXIT === Processing time: {int(processing_time * 1000)}ms")


# ============================================================================
# UPDATE OPERATION
# ============================================================================

@tracer.capture_method
def validate_update_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate request for updating leaderboard configuration.
    """
    config, _ = validate_create_request(event)  # Reuse create validation as base
    
    # Additional update-specific validations
    if 'maxEntries' in config:
        if not isinstance(config['maxEntries'], int) or config['maxEntries'] <= 0:
            raise ValueError("maxEntries must be a positive integer")
        if config['maxEntries'] > 1000000:
            raise ValueError("maxEntries cannot exceed 1,000,000")
    
    # Sanitize string fields - handle both strings and tuples/lists
    for field in ['leaderboardName', 'gameID', 'gameMode', 'statAttributeForLeaderboard', 'leaderboardType', 'scoreStrategy']:
        if field in config:
            if isinstance(config[field], (list, tuple)):
                # Handle tuple/list case - take first element if it's a string
                config[field] = str(config[field][0]).strip() if config[field] else ""
            elif isinstance(config[field], str):
                config[field] = config[field].strip()
            else:
                config[field] = str(config[field]).strip()
    
    # Handle optional fields
    for field in ['scoreType', 'sortOrder', 'timeFormat']:
        if field in config:
            if isinstance(config[field], (list, tuple)):
                config[field] = str(config[field][0]).strip() if config[field] else ""
            elif isinstance(config[field], str):
                config[field] = config[field].strip()
            else:
                config[field] = str(config[field]).strip()
    
    return config


@tracer.capture_method
async def migrate_sorted_list_async(
    old_sorted_list_name: str,
    new_sorted_list_name: str,
    score_type: str = "score"
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Asynchronously migrate data from one sorted list to another.
    """
    client = await get_valkey_client()
    migration_lock_key = f"migration_lock:{old_sorted_list_name}:{int(time.time())}"
    backup_data = None
    migration_stats = {
        'startTime': datetime.now(timezone.utc).isoformat(),
        'oldListName': old_sorted_list_name,
        'newListName': new_sorted_list_name,
        'elementsToMigrate': 0,
        'elementsMigrated': 0,
        'batchesMigrated': 0,
        'migrationDurationMs': 0
    }
    
    try:
        start_time = time.perf_counter()
        
        lock_acquired = await client.set(
            migration_lock_key,
            json.dumps({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'oldList': old_sorted_list_name,
                'newList': new_sorted_list_name
            }),
            conditional_set="onlyIfDoesNotExist",
            expiry=ExpirySet(ExpiryType.SEC, MIGRATION_TIMEOUT)
        )
        
        if not lock_acquired:
            raise ConnectionError(f"Could not acquire migration lock for {old_sorted_list_name}")
        
        backup_data = await backup_sorted_list_async(old_sorted_list_name, "migration")
        
        exists_result = await client.exists([old_sorted_list_name])
        if exists_result == 0:
            logger.info(f"Old sorted list {old_sorted_list_name} does not exist, creating empty new list")
            # Use score type-specific initialization
            init_config = INIT_CONFIG.get(score_type, INIT_CONFIG["score"])
            await client.zadd(new_sorted_list_name, {init_config["key"]: init_config["value"]})
            migration_stats['migrationDurationMs'] = int((time.perf_counter() - start_time) * 1000)
            return True, backup_data, migration_stats
        
        list_size = await client.zcard(old_sorted_list_name)
        migration_stats['elementsToMigrate'] = list_size
        
        logger.info(f"Migrating {list_size} elements from {old_sorted_list_name} to {new_sorted_list_name}")
        
        if list_size == 0:
            # Use score type-specific initialization for empty lists
            init_config = INIT_CONFIG.get(score_type, INIT_CONFIG["score"])
            await client.zadd(new_sorted_list_name, {init_config["key"]: init_config["value"]})
            migration_stats['migrationDurationMs'] = int((time.perf_counter() - start_time) * 1000)
            return True, backup_data, migration_stats
        
        migrated_count = 0
        start_index = 0
        batch_count = 0
        
        while start_index < list_size:
            end_index = min(start_index + MIGRATION_BATCH_SIZE - 1, list_size - 1)
            
            batch_data = await client.zrange_withscores(
                old_sorted_list_name,
                RangeByIndex(start_index, end_index)
            )
            
            if batch_data:
                logger.debug(f"Batch data format: {type(batch_data)}, length: {len(batch_data) if hasattr(batch_data, '__len__') else 'N/A'}")
                logger.debug(f"Batch data content: {batch_data}")
                member_scores = {}
                
                # Handle GLIDE zrange_withscores returning a dictionary {member: score}
                if isinstance(batch_data, dict):
                    for member, score in batch_data.items():
                        # Keep as float - MemoryDB sorted sets support floating-point scores
                        member_scores[member] = float(score)
                else:
                    # Handle other possible formats (list of tuples)
                    for item in batch_data:
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            member, score = item[0], item[1]
                            member_scores[member] = float(score)
                        else:
                            logger.error(f"Unexpected batch data item format: {item}")
                            continue
                
                if member_scores:
                    await client.zadd(new_sorted_list_name, member_scores)
                    migrated_count += len(member_scores)
                    batch_count += 1
                    logger.debug(f"Migrated batch {batch_count}: {len(member_scores)} elements, total: {migrated_count}")
            
            
            start_index = end_index + 1
        
        migration_stats['elementsMigrated'] = migrated_count
        migration_stats['batchesMigrated'] = batch_count
        
        new_list_size = await client.zcard(new_sorted_list_name)
        if new_list_size != list_size:
            raise ConnectionError(f"Migration verification failed: {new_list_size} != {list_size}")
        
        old_ttl = await client.ttl(old_sorted_list_name)
        if old_ttl > 0:
            await client.expire(new_sorted_list_name, old_ttl)
            logger.info(f"Copied TTL of {old_ttl} seconds to new sorted list")
        
        await client.delete([old_sorted_list_name])
        
        migration_stats['migrationDurationMs'] = int((time.perf_counter() - start_time) * 1000)
        logger.info(f"Successfully migrated sorted list in {migration_stats['migrationDurationMs']}ms")
        
        return True, backup_data, migration_stats
        
    except Exception as e:
        migration_stats['migrationDurationMs'] = int((time.perf_counter() - start_time) * 1000)
        migration_stats['error'] = str(e)
        logger.error(f"Error during sorted list migration: {str(e)}")
        
        # Distinguish between different error types for better debugging
        error_str = str(e).lower()
        if "attribute" in str(e) and ("start" in str(e) or "get_cmd_args" in str(e)):
            raise ValueError(f"GLIDE API parameter error: {str(e)}")
        elif "connection" in error_str or "timeout" in error_str or "network" in error_str:
            raise ConnectionError(f"Database connection error: {str(e)}")
        elif "lock" in error_str:
            raise RuntimeError(f"Migration lock error - another migration may be in progress: {str(e)}")
        elif "verification" in error_str or "size" in error_str:
            raise RuntimeError(f"Migration data verification failed: {str(e)}")
        elif "permission" in error_str or "auth" in error_str:
            raise RuntimeError(f"Migration permission error: {str(e)}")
        else:
            raise RuntimeError(f"Migration operation failed: {str(e)}")
    finally:
        try:
            await client.delete([migration_lock_key])
        except Exception as e:
            logger.warning(f"Failed to release migration lock: {str(e)}")


@tracer.capture_method
async def rollback_migration_async(
    new_sorted_list_name: str,
    old_sorted_list_name: str,
    backup_data: Optional[Dict[str, Any]]
) -> bool:
    """
    Asynchronously rollback a migration using backup data.
    """
    if not ENABLE_ROLLBACK:
        logger.warning("Rollback is disabled, skipping rollback attempt")
        return False
    
    try:
        client = await get_valkey_client()
        
        if backup_data and backup_data.get('entries'):
            logger.info(f"Restoring {len(backup_data['entries'])} entries to {old_sorted_list_name}")
            
            member_scores = {}
            for member, score in backup_data['entries']:
                member_scores[member] = float(score)
            
            if member_scores:
                await client.zadd(old_sorted_list_name, member_scores)
            
            if backup_data.get('ttl') and backup_data['ttl'] > 0:
                await client.expire(old_sorted_list_name, backup_data['ttl'])
        
        await client.delete([new_sorted_list_name])
        
        logger.info(f"Successfully rolled back migration")
        return True
        
    except Exception as e:
        logger.error(f"Failed to rollback migration: {str(e)}")
        return False


@tracer.capture_method
async def update_leaderboard_config_async(
    current_config: Dict[str, Any],
    new_config: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Asynchronously update leaderboard configuration with migration support.
    """
    leaderboard_name = current_config['leaderboardName']
    
    sorted_list_changed = (
        current_config['gameID'] != new_config['gameID'] or
        current_config['gameMode'] != new_config['gameMode']
    )
    
    time_config_changed = False
    time_fields = ['scoreType', 'timePrecision', 'timeFormat', 'minValidTimeInSeconds', 'maxValidTimeInSeconds']
    
    for field in time_fields:
        current_value = current_config.get(field)
        new_value = new_config.get(field)
        if current_value != new_value:
            time_config_changed = True
            logger.info(f"Time configuration changed for {leaderboard_name}: {field} {current_value} -> {new_value}")
            break
    
    old_sorted_list_name = current_config['sortedListName']
    new_sorted_list_name = generate_sorted_list_name(
        new_config['gameID'],
        new_config['gameMode'],
        leaderboard_name
    )
    
    update_results = {
        'migrationRequired': sorted_list_changed,
        'timeConfigChanged': time_config_changed,
        'oldSortedListName': old_sorted_list_name,
        'newSortedListName': new_sorted_list_name,
        'migrationPerformed': False,
        'backupCreated': False,
        'configUpdated': False,
        'rollbackPerformed': False,
        'migrationStats': None,
        'backupData': None
    }
    
    current_timestamp = int(time.time())
    current_datetime = datetime.fromtimestamp(current_timestamp, timezone.utc).isoformat()
    
    update_expression_parts = []
    expression_attribute_values = {}
    expression_attribute_names = {}
    
    update_fields = [
        ('gameID', new_config['gameID']),
        ('gameMode', new_config['gameMode']),
        ('statAttributeForLeaderboard', new_config['statAttributeForLeaderboard']),
        ('leaderboardType', new_config['leaderboardType']),
        ('scoreStrategy', new_config['scoreStrategy']),
        ('updatedAt', current_timestamp),
        ('updatedAtISO', current_datetime)
    ]
    
    if sorted_list_changed:
        update_fields.append(('sortedListName', new_sorted_list_name))
    
    optional_fields = [
        'optionalLBExpiryDateTimeStamp', 'optionalLBReadOnlyOnExpiry', 
        'scoreType', 'sortOrder', 'maxEntries',
        'timePrecision', 'timeFormat', 'minValidTimeInSeconds', 'maxValidTimeInSeconds'
    ]
    
    for field in optional_fields:
        if field in new_config:
            if field == 'optionalLBExpiryDateTimeStamp':
                update_fields.append((field, new_config['_normalized_expiry_datetime']))
            else:
                update_fields.append((field, new_config[field]))
    
    for field_name, value in update_fields:
        update_expression_parts.append(f"#{field_name} = :{field_name}")
        expression_attribute_values[f":{field_name}"] = value
        expression_attribute_names[f"#{field_name}"] = field_name
    
    update_expression = "SET " + ", ".join(update_expression_parts)
    
    if sorted_list_changed:
        logger.info(f"Migrating sorted list data from {old_sorted_list_name} to {new_sorted_list_name}")
        try:
            # Get score type for proper initialization
            score_type = new_config.get('scoreType', 'score')
            migration_success, backup_data, migration_stats = await migrate_sorted_list_async(
                old_sorted_list_name,
                new_sorted_list_name,
                score_type
            )
            
            if not migration_success:
                raise ConnectionError(f"Failed to migrate sorted list")
            
            update_results['migrationPerformed'] = True
            update_results['backupCreated'] = backup_data is not None
            update_results['migrationStats'] = migration_stats
            update_results['backupData'] = backup_data
            
        except Exception as migration_error:
            logger.error(f"Migration failed: {str(migration_error)}")
            raise migration_error
    
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            get_thread_pool(),
            lambda: leaderboards_config_table.update_item(
                Key={'leaderboardName': leaderboard_name},
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values,
                ConditionExpression='attribute_exists(leaderboardName)',
                ReturnValues='ALL_NEW'
            )
        )
        
        updated_config = response.get('Attributes', {})
        update_results['configUpdated'] = True
        
        if time_config_changed:
            logger.info(f"Updated time-based configuration for leaderboard {leaderboard_name}")
        
        logger.info(f"Successfully updated leaderboard configuration for {leaderboard_name}")
        return updated_config, update_results
        
    except ClientError as e:
        if sorted_list_changed and update_results['migrationPerformed']:
            logger.error("DynamoDB update failed, attempting to rollback migration")
            try:
                rollback_success = await rollback_migration_async(
                    new_sorted_list_name,
                    old_sorted_list_name,
                    update_results['backupData']
                )
                update_results['rollbackPerformed'] = rollback_success
                
                if rollback_success:
                    logger.info("Successfully rolled back migration")
                else:
                    logger.critical(f"CRITICAL: Failed to rollback migration. Data inconsistency may exist")
                    
            except Exception as rollback_error:
                logger.critical(f"CRITICAL: Rollback failed: {str(rollback_error)}. Manual intervention required.")
        
        raise


async def handle_update_leaderboard(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle update leaderboard configuration operation.
    """
    logger.info("=== HANDLE_UPDATE_LEADERBOARD ENTRY ===")
    start_time = time.perf_counter()
    
    try:
        new_config = validate_update_request(event)
        leaderboard_name = new_config['leaderboardName']
        
        current_config = await get_leaderboard_config_async(leaderboard_name)
    
        if 'optionalLBExpiryDateTimeStamp' in current_config:
            if is_leaderboard_expired(current_config['optionalLBExpiryDateTimeStamp']):
                expiry_time = current_config['optionalLBExpiryDateTimeStamp']
                raise ValueError(
                    f"Cannot update leaderboard '{leaderboard_name}' as it has expired. "
                    f"Expiry time: {expiry_time}. "
                    f"This leaderboard is now frozen and cannot be modified."
                )
    
        updated_config, update_results = await update_leaderboard_config_async(
            current_config,
            new_config
        )
        
        response_data = {
            'message': 'Leaderboard configuration updated successfully',
            'leaderboardConfig': updated_config,
            'updateResults': {
                'migrationRequired': update_results['migrationRequired'],
                'migrationPerformed': update_results['migrationPerformed'],
                'backupCreated': update_results['backupCreated'],
                'configUpdated': update_results['configUpdated'],
                'rollbackPerformed': update_results['rollbackPerformed']
            },
            'metadata': {
                'leaderboardName': leaderboard_name,
                'oldSortedListName': update_results['oldSortedListName'],
                'newSortedListName': update_results['newSortedListName'],
                'timestamp': datetime.now(timezone.utc).isoformat()
            },
            'success': True
        }
        
        if 'optionalLBExpiryDateTimeStamp' in new_config:
            read_only_on_expiry = new_config.get('optionalLBReadOnlyOnExpiry', True)
            response_data['expiryScheduling'] = {
                'expiryDateTime': new_config['_normalized_expiry_datetime'],
                'readOnlyOnExpiry': read_only_on_expiry,
                'resetScheduled': not read_only_on_expiry,
                'memoryDbTtlSet': not read_only_on_expiry,
                'behavior': 'read-only preservation' if read_only_on_expiry else 'auto-deletion on expiry',
                'explanation': {
                    'memoryDbBehavior': 'Data will be preserved in MemoryDB for continued read-only access after expiry' if read_only_on_expiry else 'Data will be automatically deleted from MemoryDB when the leaderboard expires',
                    'applicationBehavior': 'Write operations will be rejected after expiry, but leaderboard remains queryable' if read_only_on_expiry else 'Leaderboard will be completely removed and unavailable after expiry',
                    'dataRetention': 'Historical data preserved in DynamoDB for analytics' if read_only_on_expiry else 'DynamoDB configuration data preserved for analytics'
                }
            }
        
        if update_results['migrationStats']:
            response_data['migrationStats'] = update_results['migrationStats']
        
        if update_results['backupCreated'] and update_results['backupData']:
            response_data['backup'] = {
                'created': True,
                'entryCount': update_results['backupData']['entryCount'],
                'backupTimestamp': update_results['backupData']['backupTimestamp']
            }
        
        processing_time = time.perf_counter() - start_time
        logger.info(f"=== HANDLE_UPDATE_LEADERBOARD EXIT === Processing time: {int(processing_time * 1000)}ms")
        return response_data

    except Exception as e:
        processing_time = time.perf_counter() - start_time
        logger.error(f"=== HANDLE_UPDATE_LEADERBOARD ERROR EXIT === Processing time: {int(processing_time * 1000)}ms, Error: {str(e)}")
        raise


# ============================================================================
# DELETE OPERATION
# ============================================================================

@tracer.capture_method
def validate_delete_request(event: Dict[str, Any]) -> str:
    """
        Validate request for deleting leaderboard configuration.
    """
    return validate_get_request(event)  # Same validation as get single


@tracer.capture_method
@tracer.capture_method
async def delete_sorted_list_async(sorted_list_name: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Asynchronously delete sorted list from Valkey with optional backup.
    """
    backup_data = None
    
    try:
        client = await get_valkey_client()
        
        backup_data = await backup_sorted_list_async(sorted_list_name, "deletion")
        
        exists = await client.exists([sorted_list_name])
        if exists == 0:
            logger.info(f"Sorted list {sorted_list_name} does not exist, nothing to delete")
            return True, backup_data
        
        delete_result = await client.delete([sorted_list_name])
        
        # Also clean up configuration cache keys
        leaderboard_name = sorted_list_name.split(':')[-1]  # Extract leaderboard name from sorted_list_name
        cache_keys_to_delete = [
            f"config:{leaderboard_name}",
            f"config_cache:{leaderboard_name}"
        ]
        
        # Delete cache keys (ignore errors as they might not exist)
        try:
            cache_delete_result = await client.delete(cache_keys_to_delete)
            logger.info(f"Cleaned up {cache_delete_result} configuration cache keys for {leaderboard_name}")
        except Exception as cache_error:
            logger.warning(f"Failed to clean up cache keys for {leaderboard_name}: {cache_error}")
        
        if delete_result >= 1:
            logger.info(f"Successfully deleted sorted list {sorted_list_name}")
            return True, backup_data
        else:
            logger.warning(f"Sorted list {sorted_list_name} deletion returned unexpected result: {delete_result}")
            if FORCE_DELETE:
                logger.info("FORCE_DELETE enabled, treating as successful")
                return True, backup_data
            return False, backup_data
            
    except Exception as e:
        logger.error(f"Error deleting sorted list {sorted_list_name}: {str(e)}")
        if FORCE_DELETE:
            logger.warning("FORCE_DELETE enabled, ignoring Valkey deletion error")
            return True, backup_data
        raise ConnectionError(f"Failed to delete sorted list: {str(e)}")


@tracer.capture_method
async def delete_leaderboard_config_async(leaderboard_name: str) -> Tuple[Dict[str, Any], str]:
    """
    Asynchronously delete leaderboard configuration from DynamoDB.
    """
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            get_thread_pool(),
            lambda: leaderboards_config_table.delete_item(
                Key={'leaderboardName': leaderboard_name},
                ConditionExpression='attribute_exists(leaderboardName)',
                ReturnValues='ALL_OLD'
            )
        )
        
        if 'Attributes' not in response:
            raise LeaderboardNotFoundError(f"Leaderboard with name '{leaderboard_name}' not found")
        
        deleted_config = response['Attributes']
        deleted_datetime = datetime.now(timezone.utc).isoformat()
        
        logger.info(f"Successfully deleted leaderboard configuration: {leaderboard_name}")
        return deleted_config, deleted_datetime
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise LeaderboardNotFoundError(f"Leaderboard with name '{leaderboard_name}' not found")
        else:
            logger.error(f"Error deleting leaderboard configuration: {str(e)}")
            raise


async def handle_delete_leaderboard(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle delete leaderboard configuration operation.
    """
    logger.info("=== HANDLE_DELETE_LEADERBOARD ENTRY ===")
    start_time = time.perf_counter()
    
    try:
        leaderboard_name = validate_delete_request(event)
        
        leaderboard_config = await get_leaderboard_config_async(leaderboard_name)
        sorted_list_name = leaderboard_config.get('sortedListName')
        
        if not sorted_list_name:
            raise ValueError(f"Leaderboard configuration for '{leaderboard_name}' is missing sortedListName")
        
        deletion_results = {
            'leaderboardName': leaderboard_name,
            'sortedListName': sorted_list_name,
            'valkeyDeleted': False,
            'dynamodbDeleted': False,
            'backupData': None,
            'deletedConfig': None,
            'deletedAt': None
        }
        
        try:
            valkey_deleted, backup_data = await delete_sorted_list_async(sorted_list_name)
            deletion_results['valkeyDeleted'] = valkey_deleted
            deletion_results['backupData'] = backup_data
            
            deleted_config, deleted_datetime = await delete_leaderboard_config_async(leaderboard_name)
            deletion_results['dynamodbDeleted'] = True
            deletion_results['deletedConfig'] = deleted_config
            deletion_results['deletedAt'] = deleted_datetime
            
        except Exception as e:
            logger.error(f"Deletion failed with state: {deletion_results}")
            
            if deletion_results['valkeyDeleted'] and not deletion_results['dynamodbDeleted']:
                logger.critical(
                    f"CRITICAL DATA INCONSISTENCY: Sorted list {sorted_list_name} was deleted "
                    f"from Valkey but DynamoDB deletion failed. Manual cleanup may be required."
                )
            
            raise e

        response_data = {
            'message': 'Leaderboard configuration deleted successfully',
            'leaderboardConfig': deletion_results['deletedConfig'],
            'success': True
        }
        
        if deletion_results['backupData']:
            response_data['backup'] = {
                'created': True,
                'entryCount': deletion_results['backupData']['entryCount'],
                'backupTimestamp': deletion_results['backupData']['backupTimestamp']
            }
        
        if deletion_results['deletedConfig']:
            response_data['deletedConfiguration'] = {
                'gameID': deletion_results['deletedConfig'].get('gameID'),
                'gameMode': deletion_results['deletedConfig'].get('gameMode'),
                'leaderboardType': deletion_results['deletedConfig'].get('leaderboardType'),
                'createdAt': deletion_results['deletedConfig'].get('createdAtISO')
            }
        
        processing_time = time.perf_counter() - start_time
        logger.info(f"=== HANDLE_DELETE_LEADERBOARD EXIT === Processing time: {int(processing_time * 1000)}ms")
        return response_data

    except Exception as e:
        processing_time = time.perf_counter() - start_time
        logger.error(f"=== HANDLE_DELETE_LEADERBOARD ERROR EXIT === Processing time: {int(processing_time * 1000)}ms, Error: {str(e)}")
        raise
        raise


# ============================================================================
# MAIN LAMBDA HANDLER
def safe_xray_wrapper(func):
    """
    Wrapper to handle X-Ray serialization errors safely.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # Check if it's an X-Ray serialization error
            if "keys must be str" in str(e) or "TypeError" in str(type(e).__name__):
                logger.warning(f"X-Ray serialization error caught and handled: {str(e)}")
                # Clear any problematic trace data and retry
                try:
                    from aws_xray_sdk.core import xray_recorder
                    if xray_recorder.current_segment():
                        # Clear metadata that might contain bytes
                        xray_recorder.current_segment().metadata = {}
                        logger.info("Cleared X-Ray metadata to prevent serialization errors")
                except Exception as xray_error:
                    logger.warning(f"Could not clear X-Ray metadata: {str(xray_error)}")
                
                # Re-run the function without X-Ray tracing if needed
                return func(*args, **kwargs)
            else:
                # Re-raise other exceptions
                raise
    return wrapper

# ============================================================================

@safe_xray_wrapper
@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
@handle_errors
@log_execution
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    Unified Lambda handler for all leaderboard configuration operations.
    Routes requests based on HTTP method and path.
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
    
    # Check if Valkey is available
    if not VALKEY_AVAILABLE:
        logger.error("Valkey-GLIDE is not available - cannot process requests")
        return {
            'statusCode': 503,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
            },
            'body': json.dumps({'gameLeaderboardConfigResponse': {
                'success': False,
                'error': 'Service temporarily unavailable - Valkey dependency not found',
                'message': 'The leaderboard service is currently unavailable due to a dependency issue'
            }})
        }
    
    logger.info("Valkey is available, proceeding with request routing")
    
    # Route based on method and path
    if http_method == 'POST':
        if path.endswith('/config/create') or path.endswith('/config'):
            # Create operation - authenticate first
            auth_context = validate_authenticated_context(event, 'write')
            logger.info(f"Create operation for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
            
            # Validate AWS resources after authentication
            validate_aws_resources()
            
            response_data = run_async(handle_create_leaderboard(event, auth_context))
            status_code = 201
            
        elif path.endswith('/config/get'):
            # Get single configuration - authenticate first
            auth_context = validate_authenticated_context(event, 'read')
            logger.info(f"Get operation for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
            
            # Validate AWS resources after authentication
            validate_aws_resources()
            
            response_data = run_async(handle_get_leaderboard(event))
            status_code = 200
            
        elif path.endswith('/configs') or path.endswith('/config/all'):
            # Get all configurations - authenticate first
            auth_context = validate_authenticated_context(event, 'read')
            logger.info(f"Get all operation for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
            
            # Validate AWS resources after authentication
            validate_aws_resources()
            
            response_data = run_async(handle_get_all_leaderboards(event))
            status_code = 200
            
        elif path.endswith('/config/update'):
            # Update operation via POST - authenticate first
            auth_context = validate_authenticated_context(event, 'write')
            logger.info(f"Update operation (POST) for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
            
            # Validate AWS resources after authentication
            validate_aws_resources()
            
            response_data = run_async(handle_update_leaderboard(event))
            status_code = 200
            
        elif path.endswith('/config/delete'):
            # Delete operation via POST - authenticate first
            auth_context = validate_authenticated_context(event, 'write')
            logger.info(f"Delete operation (POST) for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
            
            # Validate AWS resources after authentication
            validate_aws_resources()
            
            response_data = run_async(handle_delete_leaderboard(event))
            status_code = 200
            
        else:
            raise ValueError(f"Unsupported POST path: {path}")
            
    elif http_method == 'GET':
        # Alternative GET endpoints
        if path.endswith('/config'):
            auth_context = validate_authenticated_context(event, 'read')
            validate_aws_resources()
            response_data = run_async(handle_get_leaderboard(event))
            status_code = 200
            
        elif path.endswith('/configs'):
            auth_context = validate_authenticated_context(event, 'read')
            validate_aws_resources()
            response_data = run_async(handle_get_all_leaderboards(event))
            status_code = 200
            
        else:
            raise ValueError(f"Unsupported GET path: {path}")
            
    elif http_method == 'PUT':
        if path.endswith('/config/update') or path.endswith('/config'):
            # Update operation - authenticate first
            auth_context = validate_authenticated_context(event, 'write')
            logger.info(f"Update operation for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
            
            # Validate AWS resources after authentication
            validate_aws_resources()
            
            response_data = run_async(handle_update_leaderboard(event))
            status_code = 200
            
        else:
            raise ValueError(f"Unsupported PUT path: {path}")
            
    elif http_method == 'DELETE':
        if path.endswith('/config/delete') or path.endswith('/config'):
            # Delete operation - authenticate first
            auth_context = validate_authenticated_context(event, 'write')
            logger.info(f"Delete operation for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
            
            # Validate AWS resources after authentication
            validate_aws_resources()
            
            response_data = run_async(handle_delete_leaderboard(event))
            status_code = 200
            
        else:
            raise ValueError(f"Unsupported DELETE path: {path}")
            
    else:
        raise ValueError(f"Unsupported HTTP method: {http_method}")
    
    # Calculate total processing time
    total_time = time.perf_counter() - start_time
    
    # Add common metadata
    if 'processingTimeMs' not in response_data:
        response_data['processingTimeMs'] = int(total_time * 1000)
    
    if 'metadata' in response_data:
        response_data['metadata']['requestId'] = context.aws_request_id
        response_data['metadata']['processingTimeMs'] = int(total_time * 1000)
    
    # Generate ETag for GET operations
    etag = None
    cache_control = 'no-cache'
    if http_method in ['GET', 'POST'] and status_code == 200:
        if 'leaderboardConfig' in response_data or 'leaderboardConfigs' in response_data:
            config_hash = hash(str(response_data))
            etag = f'"{abs(config_hash)}"'
            cache_control = 'max-age=300'
    
    # Build response headers
    headers = {
        'Content-Type': 'application/json',
        'Cache-Control': cache_control
    }
    
    if etag:
        headers['ETag'] = etag
    
    if 'leaderboardConfigs' in response_data and 'metadata' in response_data:
        headers['X-Total-Count'] = str(response_data['metadata'].get('count', 0))
    
    # COMPLETION LOGGING
    logger.info(f"=== LAMBDA HANDLER COMPLETION ===")
    logger.info(f"Status: {status_code}, Processing time: {int(total_time * 1000)}ms")
    logger.info(f"Response size: {len(json.dumps(response_data, default=decimal_serializer))} bytes")
    
    # Return response wrapped in gameLeaderboardConfigResponse
    return {
        'statusCode': status_code,
        'headers': headers,
        'body': json.dumps({'gameLeaderboardConfigResponse': response_data}, default=decimal_serializer)
    }


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