# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
getPlayerStatsAndScores.py -- Retrieve Player Game Stats and Scores Lambda Function

Retrieves player game statistics and scores from DynamoDB:
- Validates player stats retrieval requests
- Queries DynamoDB for player statistics with flexible filtering
- Supports timestamp range filtering and leaderboard filtering
- Provides comprehensive statistics summaries and pagination
- Optimized for high-performance data retrieval

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
GAME_STATS_TABLE_NAME = os.environ.get('gameStatsAndScoresTablename')
MEMORYDB_CLUSTER_NAME = os.environ.get('gameLeaderboardsMemoryDBName')
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()

# Valkey configuration
VALKEY_USE_TLS = os.environ.get('VALKEY_USE_TLS', 'true').lower() == 'true'
VALKEY_CLUSTER_MODE = os.environ.get('VALKEY_CLUSTER_MODE', 'true').lower() == 'true'

# Performance optimization settings
MAX_CONCURRENT_OPERATIONS = int(os.environ.get('MAX_CONCURRENT_OPERATIONS', '10'))
CONNECTION_TIMEOUT = int(os.environ.get('GLIDE_CONNECTION_TIMEOUT_MS', '2000'))  # Optimized: 2000ms
REQUEST_TIMEOUT = int(os.environ.get('GLIDE_REQUEST_TIMEOUT_MS', '5000'))        # Increased for failover resilience (was 2500ms)
MAX_QUERY_LIMIT = int(os.environ.get('MAX_QUERY_LIMIT', '100'))

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

# Global resources with connection health tracking
valkey_client: Optional[Union[GlideClusterClient, GlideClient]] = None
client_created_at: Optional[float] = None
thread_pool: Optional[ThreadPoolExecutor] = None
CLIENT_MAX_AGE = 300  # 5 minutes - refresh connections periodically

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


class PlayerIdentityMismatchError(Exception):
    """
    Raised when the playerID in the request does not match the authenticated
    player's identity (auth_context['playerId']). A player's stats history is
    private, so a caller may only read their OWN stats. Mapped to HTTP 403
    (Forbidden) — authenticated, but not permitted to read another player's
    data. See the identity check in lambda_handler().
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
                'body': json.dumps({'playerStatsAndScoresResponse': {
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
                'body': json.dumps({'playerStatsAndScoresResponse': {
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
                'body': json.dumps({'playerStatsAndScoresResponse': {
                    'error': 'Unauthorized',
                    'message': str(e),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }})
            }
        except PlayerIdentityMismatchError as e:
            # 403 Forbidden: authenticated, but requesting another player's stats.
            # The PLAYER_ID_MISMATCH WARNING is logged at the detection site below;
            # the attempted ID is not echoed back to the caller.
            logger.error(f"Player identity mismatch: {str(e)}")
            return {
                'statusCode': 403,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'playerStatsAndScoresResponse': {
                    'error': 'Forbidden',
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
                'body': json.dumps({'playerStatsAndScoresResponse': {
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
                'body': json.dumps({'playerStatsAndScoresResponse': {
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
                'body': json.dumps({'playerStatsAndScoresResponse': {
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
    Retrieve Valkey/MemoryDB credentials and configuration from AWS Secrets Manager.
    Uses the MEMORYDB_CLUSTER_NAME environment variable as the base key with fallback support.
    
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
        # Try alternative environment variable names
        endpoint = os.environ.get('VALKEY_CLUSTER_ENDPOINT') or os.environ.get('MEMORYDB_ENDPOINT')
        
    if not endpoint:
        # Try to get from Secrets Manager using cluster name
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
    
    # Get port from environment variable or Secrets Manager
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
        # Use the unified secret approach (from leaderboardsConfig.py)
        try:
            logger.info(f"Retrieving unified secret from: {secret_arn}")
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
                raise ValueError("Password not found in unified secret. Checked keys: password, Password, pass, Pass")
            
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

            logger.info(f"Successfully retrieved credentials from unified secret for user: {username}")
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'ResourceNotFoundException':
                logger.warning(f"Unified secret not found: {secret_arn}, trying individual secrets")
                secret_arn = None  # Fall through to individual secret approach
            else:
                logger.error(f"AWS Secrets Manager error: {error_code} - {e.response['Error']['Message']}")
                raise ValueError(f"Failed to retrieve Valkey credentials: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse unified secret JSON: {str(e)}")
            raise ValueError(f"Invalid unified secret format: {str(e)}")
    
    # Fallback to individual secrets approach (original method)
    if not secret_arn and memorydb_cluster_name:
        # Define secret keys based on the cluster name
        secret_keys = {
            'username': f"{memorydb_cluster_name}-username",
            'password': f"{memorydb_cluster_name}-password"
        }
        
        try:
            # Retrieve each secret
            for config_key, secret_id in secret_keys.items():
                try:
                    logger.debug(f"Retrieving individual secret: {secret_id}")
                    response = secretsmanager.get_secret_value(SecretId=secret_id)
                    
                    # Parse the secret value
                    try:
                        secret_data = json.loads(response['SecretString'])
                        # Extract the value - assume the JSON has a key matching the config type
                        if config_key in secret_data:
                            credentials_config[config_key] = secret_data[config_key]
                        else:
                            # Fallback: use the first value if key doesn't match
                            credentials_config[config_key] = list(secret_data.values())[0]
                    except json.JSONDecodeError:
                        # If it's not JSON, treat as plain text
                        credentials_config[config_key] = response['SecretString']
                        
                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    if error_code == 'ResourceNotFoundException':
                        logger.warning(f"Individual secret {secret_id} not found, checking for fallback environment variable")
                        
                        # Fallback to environment variables for backward compatibility
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
        raise ValueError(f"Invalid credentials retrieved - Username: {'present' if credentials_config['username'] else 'missing'}, Password: {'present' if credentials_config['password'] else 'missing'}")
    
    # Log successful retrieval (without sensitive data)
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
    # Get credentials and configuration from Secrets Manager
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
        raise ValueError(f"Invalid credentials - Username: {'present' if VALKEY_USERNAME else 'missing'}, Password: {'present' if VALKEY_PASSWORD else 'missing'}")

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
            # For MemoryDB cluster mode with optimized settings
            addresses = [NodeAddress(VALKEY_CLUSTER_ENDPOINT, VALKEY_PORT)]
            
            # Create cluster configuration with only valid GLIDE parameters
            config = ClusterClientConfiguration(
                addresses=addresses,
                use_tls=VALKEY_USE_TLS,
                credentials=credentials,
                request_timeout=REQUEST_TIMEOUT,
                client_name="leaderboard-stats-retrieval-lambda-optimized",
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
                client_name="leaderboard-stats-retrieval-lambda-optimized",
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
            thread_name_prefix="leaderboard-stats-retrieval-worker"
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
    
    if score_type != 'time':
        return score
    
    time_format = leaderboard_config.get('timeFormat', 'seconds')
    time_precision = leaderboard_config.get('timePrecision', 3)
    
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
        
        if not GAME_STATS_TABLE_NAME:
            raise ValueError("Required environment variable gameStatsAndScoresTablename is not set")
        
        try:
            valkey_config = get_valkey_credentials_and_config()
            logger.info("Successfully validated Valkey configuration from Secrets Manager")
        except Exception as e:
            logger.warning(f"Valkey configuration validation failed (non-critical for stats retrieval): {str(e)}")
        
        try:
            stats_table_desc = game_stats_table.meta.client.describe_table(
                TableName=GAME_STATS_TABLE_NAME
            )
            
            if stats_table_desc['Table']['TableStatus'] != 'ACTIVE':
                raise ValueError(f"Game stats table is not active: {stats_table_desc['Table']['TableStatus']}")
            
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
    
    # Check if playerStatsAndScoresRequest exists
    if 'playerStatsAndScoresRequest' not in body:
        raise ValueError("playerStatsAndScoresRequest is missing in request body")
    
    request_params = body['playerStatsAndScoresRequest']
    
    # Validate required fields
    required_fields = ["playerID", "gameID", "gameMode"]
    
    for field in required_fields:
        if field not in request_params:
            raise ValueError(f"Required field '{field}' is missing in playerStatsAndScoresRequest")
    
    # Enhanced field validation
    if not isinstance(request_params['playerID'], str) or not request_params['playerID'].strip():
        raise ValueError("playerID must be a non-empty string")
    
    # Validate playerID format (alphanumeric with optional hyphens/underscores)
    if not re.match(r'^[a-zA-Z0-9_-]+$', request_params['playerID']):
        raise ValueError("playerID contains invalid characters")
    
    if not isinstance(request_params['gameID'], str) or not request_params['gameID'].strip():
        raise ValueError("gameID must be a non-empty string")
    
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', request_params['gameID'].strip()):
        raise ValueError("gameID contains invalid characters")
    
    if not isinstance(request_params['gameMode'], str) or not request_params['gameMode'].strip():
        raise ValueError("gameMode must be a non-empty string")
    
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', request_params['gameMode'].strip()):
        raise ValueError("gameMode contains invalid characters")
    
    # Validate optional timestamp filters
    for timestamp_field in ['startTimestamp', 'endTimestamp']:
        if timestamp_field in request_params and request_params[timestamp_field]:
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
    
    # Validate limit
    if 'limit' in request_params:
        try:
            limit = int(request_params['limit'])
            if limit <= 0:
                raise ValueError("limit must be a positive integer")
            if limit > MAX_QUERY_LIMIT:
                raise ValueError(f"limit cannot exceed {MAX_QUERY_LIMIT}")
            request_params['limit'] = limit
        except (ValueError, TypeError):
            raise ValueError("limit must be a valid positive integer")
    else:
        request_params['limit'] = 50  # Default limit
    
    # Validate leaderboardName filter
    if 'leaderboardName' in request_params and request_params['leaderboardName']:
        if not isinstance(request_params['leaderboardName'], str):
            raise ValueError("leaderboardName must be a string")
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', request_params['leaderboardName'].strip()):
            raise ValueError("leaderboardName contains invalid characters")
        request_params['leaderboardName'] = request_params['leaderboardName'].strip()
    
    # Validate lastEvaluatedKey for pagination
    if 'lastEvaluatedKey' in request_params and request_params['lastEvaluatedKey']:
        if not isinstance(request_params['lastEvaluatedKey'], dict):
            raise ValueError("lastEvaluatedKey must be a dictionary")
    
    return request_params


# ============================================================================
# PLAYER STATS RETRIEVAL
# ============================================================================

@tracer.capture_method
async def get_player_stats_async(
    player_id: str,
    game_id: str,
    game_mode: str,
    start_timestamp: Optional[int] = None,
    end_timestamp: Optional[int] = None,
    leaderboard_name: Optional[str] = None,
    limit: int = 50,
    last_evaluated_key: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Asynchronously retrieve player stats from DynamoDB Game Stats table using primary key.
    """
    try:
        # Build sort key condition for gameID#gameMode prefix
        sort_key_prefix = f"{game_id}#{game_mode}#"
        
        # Build key condition expression (primary key + sort key condition)
        key_condition = 'playerID = :playerID AND begins_with(sortKey, :sortKeyPrefix)'
        expression_attribute_values = {
            ':playerID': player_id,
            ':sortKeyPrefix': sort_key_prefix
        }
        
        # Build filter expressions for additional filtering
        filter_expressions = []
        expression_attribute_names = {}
        
        # Add timestamp filtering if provided
        if start_timestamp is not None:
            filter_expressions.append("#timestamp >= :startTimestamp")
            expression_attribute_values[':startTimestamp'] = start_timestamp
            expression_attribute_names['#timestamp'] = 'timestamp'
        
        if end_timestamp is not None:
            filter_expressions.append("#timestamp <= :endTimestamp")
            expression_attribute_values[':endTimestamp'] = end_timestamp
            expression_attribute_names['#timestamp'] = 'timestamp'
        
        # Add leaderboard name filtering if provided
        if leaderboard_name:
            filter_expressions.append("leaderboardName = :leaderboardName")
            expression_attribute_values[':leaderboardName'] = leaderboard_name
        
        # Build query parameters for primary table query
        query_params = {
            'KeyConditionExpression': key_condition,
            'ExpressionAttributeValues': expression_attribute_values,
            'Limit': limit,
            'ScanIndexForward': False  # Sort by sort key descending (most recent first)
        }
        
        # Add filter expression if we have any filters
        if filter_expressions:
            query_params['FilterExpression'] = " AND ".join(filter_expressions)
        
        # Add expression attribute names if we have any
        if expression_attribute_names:
            query_params['ExpressionAttributeNames'] = expression_attribute_names
        
        # Add pagination support
        if last_evaluated_key:
            query_params['ExclusiveStartKey'] = last_evaluated_key
        
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            get_thread_pool(),
            lambda: game_stats_table.query(**query_params)
        )
        
        items = response.get('Items', [])
        
        # Filter by playerID (since it's in the filter expression, not key condition)
        player_stats = [item for item in items if item.get('playerID') == player_id]
        
        # Sort by timestamp descending (most recent first)
        player_stats.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        
        # Calculate statistics
        stats_summary = {
            'totalRecords': len(player_stats),
            'dateRange': {},
            'scoreRange': {},
            'leaderboards': set()
        }
        
        if player_stats:
            timestamps = [float(item.get('timestamp', 0)) for item in player_stats]
            scores = [float(item.get('playerScore', 0)) for item in player_stats if item.get('playerScore') is not None]
            
            stats_summary['dateRange'] = {
                'earliest': min(timestamps),
                'latest': max(timestamps),
                'earliestISO': datetime.fromtimestamp(min(timestamps), timezone.utc).isoformat(),
                'latestISO': datetime.fromtimestamp(max(timestamps), timezone.utc).isoformat()
            }
            
            if scores:
                stats_summary['scoreRange'] = {
                    'minimum': min(scores),
                    'maximum': max(scores),
                    'average': sum(scores) / len(scores)
                }
            
            # Collect unique leaderboards
            for item in player_stats:
                if item.get('leaderboardName'):
                    stats_summary['leaderboards'].add(item['leaderboardName'])
        
        # Convert set to list for JSON serialization
        stats_summary['leaderboards'] = list(stats_summary['leaderboards'])
        
        logger.info(f"Successfully retrieved {len(player_stats)} stats records for player {player_id}")
        
        result = {
            'Items': player_stats,
            'Count': len(player_stats),
            'ScannedCount': response.get('ScannedCount', 0),
            'StatsSummary': stats_summary
        }
        if response.get('LastEvaluatedKey'):
            result['LastEvaluatedKey'] = response['LastEvaluatedKey']
        return result
        
    except ClientError as e:
        logger.error(f"Error querying player stats: {str(e)}")
        raise


# ============================================================================
# MAIN PROCESSING LOGIC
# ============================================================================

async def process_player_stats_retrieval(request_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process the player stats retrieval request.
    
    Returns:
        Dictionary containing the stats retrieval results
    """
    # Extract parameters
    player_id = request_params['playerID']
    game_id = request_params['gameID']
    game_mode = request_params['gameMode']
    start_timestamp = request_params.get('startTimestamp')
    end_timestamp = request_params.get('endTimestamp')
    leaderboard_name = request_params.get('leaderboardName')
    limit = request_params.get('limit', 50)
    last_evaluated_key = request_params.get('lastEvaluatedKey')
    
    # Get player stats from DynamoDB
    stats_result = await get_player_stats_async(
        player_id,
        game_id,
        game_mode,
        start_timestamp,
        end_timestamp,
        leaderboard_name,
        limit,
        last_evaluated_key
    )
    
    return stats_result


# ============================================================================
# MAIN LAMBDA HANDLER
# ============================================================================

@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
@handle_errors
@log_execution
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    High-performance Lambda handler for retrieving player stats and scores from DynamoDB.
    
    Optimized for:
    - Data integrity (consistent reads where possible)
    - Performance (efficient queries, concurrent operations)
    - Robustness (error handling, comprehensive validation)
    """
    # ENTRY LOGGING - Always log function entry
    logger.info("=== LAMBDA HANDLER ENTRY ===")
    logger.info(f"Function: {context.function_name}")
    logger.info(f"Request ID: {context.aws_request_id}")
    
    start_time = time.perf_counter()
    
    # Validate HTTP method - API Gateway only allows POST
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
            'body': json.dumps({'playerStatsAndScoresResponse': {
                'error': 'Method Not Allowed',
                'message': f'HTTP method {http_method} not allowed. Only POST is supported.',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }})
        }
    
    # Check if Valkey is available (non-critical for stats retrieval)
    if not VALKEY_AVAILABLE:
        logger.warning("Valkey-GLIDE is not available - stats retrieval will continue without caching")
    else:
        logger.info("Valkey is available for potential caching operations")
    
    # Authenticate request
    try:
        auth_context = validate_authenticated_context(event, 'read')
        logger.info(f"Stats retrieval for studio: {auth_context['studioId']}, game: {auth_context['gameId']}")
    except LeaderboardAuthenticationError as e:
        logger.error(f"Authentication failed: {str(e)}")
        raise
    
    # Validate AWS resources
    validate_aws_resources()
    
    # Validate request
    request_params = validate_request(event)

    # =========================================================================
    # PRIVACY: a player's stats history is personal data — a caller may only read
    # THEIR OWN stats. Compare the requested playerID against the authenticated
    # player's id (auth_context['playerId'], set by your Lambda authorizer from
    # the validated token). A mismatch means the caller is trying to read another
    # player's history, so we reject with HTTP 403 and log it as potential abuse.
    #
    # Enforce-when-present: skipped if your authorizer does not set 'playerId'
    # (documented as recommended, not mandatory), so the deployment keeps working.
    # To make it MANDATORY, drop the `authenticated_player_id and` guard and have
    # the authorizer always populate 'playerId'.
    # =========================================================================
    authenticated_player_id = auth_context.get('playerId', '')
    if authenticated_player_id and authenticated_player_id != request_params['playerID']:
        logger.warning(
            "PLAYER_ID_MISMATCH: authenticated player '%s' attempted to read stats of '%s' "
            "(studio=%s, game=%s, requestId=%s)",
            authenticated_player_id, request_params['playerID'],
            auth_context['studioId'], auth_context['gameId'], context.aws_request_id,
        )
        raise PlayerIdentityMismatchError(
            "playerID does not match the authenticated player"
        )

    # Execute the async processing
    stats_result = run_async(process_player_stats_retrieval(request_params))
    
    # Calculate total processing time
    total_time = time.perf_counter() - start_time
    
    # Prepare response data
    response_data = {
        'playerStatsAndScoresResponse': {
            'playerID': request_params['playerID'],
            'gameID': request_params['gameID'],
            'gameMode': request_params['gameMode'],
            'filters': {
                'startTimestamp': request_params.get('startTimestamp'),
                'endTimestamp': request_params.get('endTimestamp'),
                'startTimestampISO': request_params.get('startTimestampISO'),
                'endTimestampISO': request_params.get('endTimestampISO'),
                'leaderboardName': request_params.get('leaderboardName'),
                'limit': request_params.get('limit', 50)
            },
            'results': {
                'totalRecords': stats_result['Count'],
                'scannedRecords': stats_result['ScannedCount'],
                'playerStats': stats_result['Items'],
                'statsSummary': stats_result['StatsSummary'],
                'hasMoreResults': 'LastEvaluatedKey' in stats_result
            }
        },
        'metadata': {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'requestId': context.aws_request_id,
            'processingTimeMs': int(total_time * 1000),
            'dataSource': 'DynamoDB'
        },
        'success': True
    }
    
    # Add pagination info if available
    if 'LastEvaluatedKey' in stats_result:
        response_data['playerStatsAndScoresResponse']['pagination'] = {
            'lastEvaluatedKey': stats_result['LastEvaluatedKey'],
            'hasMoreResults': True
        }
    
    # Generate ETag for caching
    stats_hash = hash(str(stats_result['Items']))
    etag = f'"{abs(stats_hash)}"'
    
    # COMPLETION LOGGING
    logger.info(f"=== LAMBDA HANDLER COMPLETION ===")
    logger.info(f"Status: 200, Processing time: {int(total_time * 1000)}ms")
    logger.info(f"Records retrieved: {stats_result['Count']}, Response size: {len(json.dumps(response_data, default=decimal_serializer))} bytes")
    
    # Return success response with comprehensive details
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Cache-Control': 'max-age=60',  # Cache for 1 minute (stats can change frequently)
            'ETag': etag,
            'X-Total-Records': str(stats_result['Count'])
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