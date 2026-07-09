# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
developerRegistration.py - Developer Registration and API Key Management - SSM Parameter Store Based
Handles studio registration updates, API key generation, and key management

Updated for Python 3.13, optimized for performance, enhanced security, and reliability. Aligned with infrastructure environment variables and SSM parameter structure.
Env variable resolution - ENV vars first, then SSM, no fallbacks. If environment detection fails, the function will error out for immediate fixing.
"""

import json
import os
import uuid
import hashlib
import secrets
import time
import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
import boto3
from botocore.exceptions import ClientError
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.metrics import MetricUnit

# Initialize AWS Lambda Powertools
logger = Logger(service="developer-registration")
tracer = Tracer(service="developer-registration")
metrics = Metrics(namespace="GameStatsLeaderboards/DeveloperManagement")

# AWS clients with optimized configurations
ssm = boto3.client('ssm', config=boto3.session.Config(
    max_pool_connections=10,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
))

apigateway = boto3.client('apigateway', config=boto3.session.Config(
    max_pool_connections=5,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
))

lambda_client = boto3.client('lambda', config=boto3.session.Config(
    max_pool_connections=5,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
))

# Strict environment variable resolution - no fallbacks, fail fast
def get_required_env_var(var_name: str, description: str = "") -> str:
    """
    Get required environment variable with strict validation.
    If not found, raise exception immediately - no fallbacks.
    """
    value = os.environ.get(var_name)
    if not value:
        error_msg = f"Required environment variable '{var_name}' not found"
        if description:
            error_msg += f" ({description})"
        error_msg += ". Fix infrastructure configuration."
        logger.error(error_msg)
        raise EnvironmentError(error_msg)
    
    logger.info(f"✅ Found required environment variable '{var_name}': '{value}'")
    return value

def get_optional_env_var(var_name: str, default_value: str = None) -> Optional[str]:
    """
    Get optional environment variable.
    Returns None if not found (no fallbacks).
    """
    value = os.environ.get(var_name)
    if value:
        logger.info(f"✅ Found optional environment variable '{var_name}': '{value}'")
        return value
    elif default_value:
        logger.info(f"⚠️ Optional environment variable '{var_name}' not found, using provided default: '{default_value}'")
        return default_value
    else:
        logger.info(f"ℹ️ Optional environment variable '{var_name}' not found, returning None")
        return None

# Initialize required environment variables at module load - fail fast if missing
try:
    # Core required variables from infrastructure
    ENVIRONMENT = get_required_env_var('ENVIRONMENT', 'Current deployment environment')
    SSM_PARAMETER_PREFIX = get_required_env_var('SSM_PARAMETER_PREFIX', 'SSM parameter prefix for configuration')
    API_ENDPOINT = get_required_env_var('API_ENDPOINT', 'API Gateway endpoint URL')
    
    # Optional variables
    AWS_DEFAULT_REGION = get_optional_env_var('AWS_DEFAULT_REGION', 'us-west-2')
    DEFAULT_RATE_LIMIT = get_optional_env_var('DEFAULT_RATE_LIMIT', '1000')
    AUTHORIZER_FUNCTION_NAME = get_optional_env_var('AUTHORIZER_FUNCTION_NAME', None)
    
    logger.info("✅ All required environment variables loaded successfully")
    logger.info(f"   Environment: {ENVIRONMENT}")
    logger.info(f"   SSM Prefix: {SSM_PARAMETER_PREFIX}")
    logger.info(f"   API Endpoint: {API_ENDPOINT}")
    
except EnvironmentError as e:
    logger.error(f"❌ Failed to load required environment variables: {e}")
    raise  # Fail fast - don't allow function to start with missing config

# Global resources
thread_pool: Optional[ThreadPoolExecutor] = None
_ssm_config_cache: Optional[Dict[str, Any]] = None

class DeveloperRegistrationError(Exception):
    """Custom exception for developer registration errors"""
    pass

class RevokedKeyError(DeveloperRegistrationError):
    """Custom exception for revoked API key access attempts"""
    pass

class EnvironmentConfigurationError(Exception):
    """Custom exception for environment configuration errors"""
    pass

def wrap_response(status_code: int, response_data: dict) -> dict:
    """
    Wrap response data in devRegResponse object for consistency.
    
    Args:
        status_code: HTTP status code
        response_data: Response data to wrap
    
    Returns:
        Formatted response with devRegResponse wrapper
    """
    return {
        'statusCode': status_code,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps({
            'devRegResponse': response_data
        })
    }

def get_consolidated_config_from_ssm() -> Dict[str, Any]:
    """
    Get consolidated configuration from SSM Parameter Store with caching.
    This is the secondary source after environment variables.
    """
    global _ssm_config_cache
    
    if _ssm_config_cache:
        logger.info("Using cached SSM configuration")
        return _ssm_config_cache
    
    try:
        parameter_name = f"{SSM_PARAMETER_PREFIX}/config/consolidated"
        logger.info(f"Fetching consolidated config from SSM: {parameter_name}")
        
        response = ssm.get_parameter(
            Name=parameter_name,
            WithDecryption=True
        )
        
        config_json = response['Parameter']['Value']
        config = json.loads(config_json)
        
        # Cache the configuration
        _ssm_config_cache = config
        
        logger.info("✅ Successfully loaded consolidated configuration from SSM")
        return config
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'ParameterNotFound':
            error_msg = f"SSM parameter not found: {parameter_name}. Infrastructure may not be properly deployed."
            logger.error(error_msg)
            raise EnvironmentConfigurationError(error_msg)
        else:
            error_msg = f"Failed to retrieve SSM configuration: {e}"
            logger.error(error_msg)
            raise EnvironmentConfigurationError(error_msg)
    
    except json.JSONDecodeError as e:
        error_msg = f"Invalid JSON in SSM configuration parameter: {e}"
        logger.error(error_msg)
        raise EnvironmentConfigurationError(error_msg)
    
    except Exception as e:
        error_msg = f"Unexpected error retrieving SSM configuration: {e}"
        logger.error(error_msg)
        raise EnvironmentConfigurationError(error_msg)

def get_config_value(category: str, key: str, use_ssm_fallback: bool = True) -> Any:
    """
    Get configuration value with strict priority:
    1. Environment variables (highest priority - performance)
    2. SSM Parameter Store (fallback)
    3. Error if not found (no other fallbacks)
    """
    # Priority 1: Check environment variables first (performance)
    env_var_name = f"{category.upper()}_{key.upper()}".replace('-', '_')
    env_value = os.environ.get(env_var_name)
    
    if env_value:
        logger.info(f"✅ Found config value in ENV: {env_var_name} = {env_value}")
        return env_value
    
    # Priority 2: Check SSM if enabled
    if use_ssm_fallback:
        try:
            ssm_config = get_consolidated_config_from_ssm()
            
            if category in ssm_config and key in ssm_config[category]:
                ssm_value = ssm_config[category][key]
                logger.info(f"✅ Found config value in SSM: {category}.{key} = {ssm_value}")
                return ssm_value
        
        except EnvironmentConfigurationError:
            # SSM config failed, continue to error
            pass
    
    # No fallbacks - error out for immediate fixing
    error_msg = f"Configuration value not found: {category}.{key}. Check environment variables or SSM parameters."
    logger.error(error_msg)
    raise EnvironmentConfigurationError(error_msg)

def log_execution(func):
    """
    Decorator to log function execution details with performance metrics.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        function_name = func.__name__
        request_id = None
        
        # Extract request_id if available
        if len(args) > 0 and isinstance(args[0], dict) and 'requestContext' in args[0]:
            request_id = args[0].get('requestContext', {}).get('requestId', 'unknown')
        
        # Log function entry with parameters (sanitized)
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

def handle_errors(func):
    """
    Enhanced error handler with specific error types and recovery strategies.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except EnvironmentConfigurationError as e:
            logger.error(f"Environment configuration error: {str(e)}")
            return wrap_response(500, {
                'error': 'Configuration Error',
                'message': 'Service configuration is invalid. Please contact support.',
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            return wrap_response(400, {
                'error': 'Bad Request',
                'message': str(e),
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            logger.error(f"AWS service error: {error_code} - {error_message}")
            
            status_code = 500
            if error_code in ['ParameterNotFound']:
                status_code = 404
                error_message = "Developer registration not found"
            elif error_code in ['AccessDeniedException', 'UnauthorizedException']:
                status_code = 403
            elif error_code in ['ThrottlingException']:
                status_code = 429
            elif error_code == 'ParameterAlreadyExists':
                status_code = 409
                error_message = "Developer already registered for this game"
            
            return wrap_response(status_code, {
                'error': error_code,
                'message': error_message,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
        except DeveloperRegistrationError as e:
            logger.warning(f"Registration validation error: {str(e)}")
            return wrap_response(400, {
                'error': 'Registration Error',
                'message': str(e),
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
        except Exception as e:
            logger.exception(f"Unexpected error in developer registration: {str(e)}")
            metrics.add_metric(name="RegistrationErrors", unit=MetricUnit.Count, value=1)
            return wrap_response(500, {
                'error': 'Internal Server Error',
                'message': 'An unexpected error occurred',
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
    
    return wrapper

def run_async(coro):
    """
    Optimized helper function to run async code in sync context.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is already running, we need to handle this differently
            # This shouldn't happen in Lambda, but just in case
            import nest_asyncio
            nest_asyncio.apply()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(coro)

def flush_authorizer_cache() -> bool:
    """
    Flush API Gateway authorizer cache to immediately invalidate old API keys.
    Called after successful registration, regeneration, or revocation operations.
    
    Returns:
        bool: True if flush succeeded, False if failed (non-critical)
    """
    try:
        # Extract API Gateway ID from the API_ENDPOINT
        # Format: https://{api-id}.execute-api.{region}.amazonaws.com/{stage}
        import re
        api_id_match = re.search(r'https://([^.]+)\.execute-api', API_ENDPOINT)
        
        if not api_id_match:
            logger.warning(f"Could not extract API Gateway ID from endpoint: {API_ENDPOINT}")
            return False
        
        api_id = api_id_match.group(1)
        stage_name = ENVIRONMENT  # Use environment as stage name
        
        logger.info(f"Flushing authorizer cache for API Gateway: {api_id}, stage: {stage_name}")
        
        apigateway.flush_stage_authorizers_cache(
            restApiId=api_id,
            stageName=stage_name
        )
        
        logger.info("✅ Authorizer cache flushed successfully - old API keys immediately invalidated")
        metrics.add_metric(name="AuthorizerCacheFlush", unit=MetricUnit.Count, value=1)
        return True
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        logger.warning(f"Failed to flush authorizer cache - Code: {error_code}, Message: {error_message}")
        metrics.add_metric(name="AuthorizerCacheFlushError", unit=MetricUnit.Count, value=1)
        return False
        
    except Exception as e:
        logger.warning(f"Unexpected error flushing authorizer cache: {str(e)}")
        metrics.add_metric(name="AuthorizerCacheFlushError", unit=MetricUnit.Count, value=1)
        return False

def generate_api_key(studio_name: str, game_id: str, environment: str) -> str:
    """Generate a secure API key for a developer"""
    # Create a unique identifier
    unique_id = secrets.token_urlsafe(16)
    
    # Create readable prefix
    studio_prefix = studio_name.lower().replace(' ', '').replace('-', '')[:8]
    game_prefix = game_id.lower().replace(' ', '').replace('-', '')[:8]
    
    # Format: studio_game_env_uniqueid
    api_key = f"{studio_prefix}_{game_prefix}_{environment}_{unique_id}"
    
    return api_key

def validate_input_characters(field_name: str, value: str, field_type: str = "text") -> None:
    """
    Validate that input contains only allowed characters with risk-based filtering.
    
    Args:
        field_name: Name of the field being validated (e.g., "studioName", "gameTitle")
        value: The value to validate
        field_type: Type of field for specific validation rules
    
    Raises:
        DeveloperRegistrationError: If invalid characters are found
    """
    if not value or not isinstance(value, str):
        raise DeveloperRegistrationError(f"{field_name} must be a non-empty string")
    
    # Define HIGH RISK characters that should always be blocked
    high_risk_chars = [
        # SQL Injection risks
        "'", '"', ';',
        # Path traversal risks  
        '/', '\\',
        # Script injection risks
        '<', '>', '{', '}', '$',
        # Command injection risks
        '`', '|',
        # Control characters
        '\n', '\r', '\t', '\0',
        # Double sequences that are risky
        '--'
    ]
    
    # Define MEDIUM RISK characters (context dependent)
    medium_risk_chars = [
        '%', '+', '#', '?', '*', '^'
    ]
    
    # Define allowed characters based on field type
    if field_type == "email":
        # Email validation is handled separately
        return
    elif field_type == "genre":
        # Allow hyphens for compound genres like "action-adventure"
        # Allow some safe punctuation for genres
        allowed_pattern = r'^[a-zA-Z0-9\s\-_.()!&@]+$'
        allowed_description = "letters, numbers, spaces, hyphens, underscores, periods, parentheses, exclamation marks, ampersands, and at signs"
    else:
        # Default for studio names, game titles - allow common business punctuation
        allowed_pattern = r'^[a-zA-Z0-9\s\-_.()!&@]+$'
        allowed_description = "letters, numbers, spaces, hyphens, underscores, periods, parentheses, exclamation marks, ampersands, and at signs"
    
    # Check for HIGH RISK characters first
    found_high_risk = []
    for char in high_risk_chars:
        if char in value:
            found_high_risk.append(char)
    
    if found_high_risk:
        error_msg = f"Security risk: {field_name} contains dangerous characters: {', '.join(repr(char) for char in found_high_risk)}. "
        error_msg += "These characters are blocked for security reasons (SQL injection, path traversal, script injection prevention)."
        raise DeveloperRegistrationError(error_msg)
    
    # Check for MEDIUM RISK characters
    found_medium_risk = []
    for char in medium_risk_chars:
        if char in value:
            found_medium_risk.append(char)
    
    if found_medium_risk:
        error_msg = f"Invalid characters in {field_name}: {', '.join(repr(char) for char in found_medium_risk)}. "
        error_msg += f"Only {allowed_description} are allowed. "
        error_msg += "Characters like %+#?*^ can cause encoding or processing issues."
        raise DeveloperRegistrationError(error_msg)
    
    # Check against allowed pattern for any other invalid characters
    if not re.match(allowed_pattern, value):
        # Find any remaining invalid characters
        if field_type == "genre":
            invalid_chars = set(re.findall(r'[^a-zA-Z0-9\s\-_.()!&@]', value))
        else:
            invalid_chars = set(re.findall(r'[^a-zA-Z0-9\s\-_.()!&@]', value))
        
        if invalid_chars:
            invalid_chars_list = sorted(list(invalid_chars))
            error_msg = f"Invalid characters in {field_name}: {', '.join(repr(char) for char in invalid_chars_list)}. "
            error_msg += f"Only {allowed_description} are allowed."
            raise DeveloperRegistrationError(error_msg)
    
    # Additional length validation
    if len(value.strip()) == 0:
        raise DeveloperRegistrationError(f"{field_name} cannot be empty or contain only whitespace")
    
    if len(value) > 100:
        raise DeveloperRegistrationError(f"{field_name} is too long (maximum 100 characters, got {len(value)})")
    
    # Check for suspicious patterns
    if value.strip() != value:
        logger.warning(f"{field_name} has leading/trailing whitespace, will be trimmed")
    
    # Check for repeated special characters that might indicate issues
    if '--' in value or '__' in value or '..' in value:
        raise DeveloperRegistrationError(f"{field_name} contains repeated special characters which are not allowed")

def sanitize_for_id(input_string: str) -> str:
    """
    Sanitize input string for use as studioId or gameId by removing special characters.
    
    Args:
        input_string: The original string to sanitize
        
    Returns:
        Sanitized string suitable for use as an ID
    """
    if not input_string:
        return ""
    
    # Remove all special characters, keep only alphanumeric, spaces, hyphens, underscores, periods
    # This will strip out @, &, !, (), etc. that we allow in display names
    sanitized = re.sub(r'[^a-zA-Z0-9\s\-_.]', '', input_string)
    
    # Convert to lowercase and replace spaces/underscores/periods with hyphens
    sanitized = sanitized.lower().replace(' ', '-').replace('_', '-').replace('.', '-')
    
    # Clean up multiple consecutive hyphens
    sanitized = re.sub(r'-+', '-', sanitized)
    
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip('-')
    
    # Ensure we have something left
    if not sanitized:
        # If everything was stripped, generate a fallback
        sanitized = "studio" if "studio" in input_string.lower() else "game"
    
    return sanitized

def validate_registration_request(request: Dict[str, Any]) -> Dict[str, str]:
    """Validate developer registration request with enhanced character validation and ID sanitization"""

    required_fields = ['studioName', 'contactEmail', 'gameTitle', 'gameGenre']
    missing_fields = [field for field in required_fields if not request.get(field)]

    if missing_fields:
        raise DeveloperRegistrationError(f"Missing required fields: {', '.join(missing_fields)}")
    
    # Validate characters in each field (allows @, &, !, () in display names)
    validate_input_characters("studioName", request['studioName'])
    validate_input_characters("gameTitle", request['gameTitle'])
    validate_input_characters("gameGenre", request['gameGenre'], "genre")
    
    # Validate email format with enhanced validation
    email = request['contactEmail']
    if '@' not in email or '.' not in email.split('@')[1]:
        raise DeveloperRegistrationError("Invalid email format. Please provide a valid email address (e.g., user@domain.com)")
    
    # Additional email character validation (emails have different rules)
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        raise DeveloperRegistrationError("Email contains invalid characters. Only letters, numbers, dots, underscores, percent signs, plus signs, and hyphens are allowed in email addresses")
    
    # Clean the input first
    clean_game_title = request['gameTitle'].strip()
    clean_studio_name = request['studioName'].strip()
    
    # Generate sanitized IDs by stripping special characters
    game_id = sanitize_for_id(clean_game_title)
    studio_id = sanitize_for_id(clean_studio_name)
    
    # Ensure we have valid IDs
    if not game_id:
        raise DeveloperRegistrationError("Game title could not be converted to a valid ID. Please include some alphanumeric characters.")
    if not studio_id:
        raise DeveloperRegistrationError("Studio name could not be converted to a valid ID. Please include some alphanumeric characters.")
    
    logger.info(f"In validate_registration_request, generated game_id={game_id}, studio_id={studio_id}")
    logger.info(f"Original names: studio='{clean_studio_name}', game='{clean_game_title}'")

    return {
        'studioName': clean_studio_name,
        'studioId': studio_id,
        'contactEmail': request['contactEmail'].strip(),
        'gameTitle': clean_game_title,
        'gameId': game_id,
        'gameGenre': request['gameGenre'].strip()
    }

def construct_ssm_parameter_name(prefix: str, studio_id: str, game_id: str) -> str:
    """
    Construct SSM parameter name with proper formatting aligned with infrastructure.
    Infrastructure uses: /{resource_prefix}/category/parameter
    For API keys: /{resource_prefix}/api-keys/{studio_id}-{game_id}
    """
    # Add comprehensive logging for debugging
    logger.info(f"construct_ssm_parameter_name called with:")
    logger.info(f"  prefix: '{prefix}' (type: {type(prefix)})")
    logger.info(f"  studio_id: '{studio_id}' (type: {type(studio_id)})")
    logger.info(f"  game_id: '{game_id}' (type: {type(game_id)})")
    
    # Validate inputs are not None or empty
    if not prefix:
        logger.error(f"SSM parameter prefix is empty or None: {prefix}")
        raise DeveloperRegistrationError("SSM parameter prefix cannot be empty")
    
    if not studio_id:
        logger.error(f"Studio ID is empty or None: {studio_id}")
        raise DeveloperRegistrationError("Studio ID cannot be empty")
    
    if not game_id:
        logger.error(f"Game ID is empty or None: {game_id}")
        raise DeveloperRegistrationError("Game ID cannot be empty")
    
    # Clean up the prefix - ensure it starts with / and doesn't end with /
    clean_prefix = str(prefix).strip()
    if not clean_prefix.startswith('/'):
        clean_prefix = '/' + clean_prefix
    
    # Remove trailing slash if present
    if clean_prefix.endswith('/'):
        clean_prefix = clean_prefix.rstrip('/')
    
    logger.info(f"After cleaning, prefix: '{clean_prefix}'")
    
    # Construct the full parameter name following infrastructure pattern
    # Infrastructure pattern: /{resource_prefix}/category/parameter
    parameter_name = f"{clean_prefix}/api-keys/{studio_id}-{game_id}"
    
    logger.info(f"Constructed SSM parameter name: '{parameter_name}'")
    logger.info(f"Parameter name length: {len(parameter_name)}")
    
    # Validate the final parameter name
    if not parameter_name or not parameter_name.strip():
        logger.error(f"Final parameter name is empty: '{parameter_name}'")
        raise DeveloperRegistrationError("Failed to construct valid SSM parameter name")
    
    # Validate parameter name length (SSM limit is 2048 characters)
    if len(parameter_name) > 2048:
        logger.error(f"SSM parameter name too long: {len(parameter_name)} characters")
        raise DeveloperRegistrationError(f"SSM parameter name too long: {len(parameter_name)} characters")
    
    return parameter_name


def store_registration_metadata(studio_id: str, game_id: str) -> None:
    """Publish the non-secret studioId/gameId to a plain SSM parameter the player
    authorizer reads.

    Best-effort: logs on failure rather than failing registration. The player
    authorizer fails closed if the value is missing.
    """
    if not studio_id or not game_id:
        logger.warning("Skipping registration metadata write: missing studioId/gameId")
        return
    parameter_name = f"{SSM_PARAMETER_PREFIX.rstrip('/')}/config/registration"
    try:
        ssm.put_parameter(
            Name=parameter_name,
            Value=json.dumps({"studioId": studio_id, "gameId": game_id}),
            Type='String',
            Overwrite=True,
            Tier='Standard',
        )
        logger.info(f"Stored registration metadata parameter: {parameter_name}")
    except ClientError as e:
        logger.error(f"Failed to write registration metadata parameter {parameter_name}: {e}")


def store_api_key_in_ssm(parameter_name: str, api_key_data: Dict[str, Any], description: str, backup_old_key: bool = True) -> bool:
    """
    Store API key data in SSM Parameter Store with optional backup of old key.
    
    Args:
        parameter_name: SSM parameter name
        api_key_data: API key data to store
        description: Parameter description
        backup_old_key: Whether to backup the old key before overwriting
    
    Returns:
        True if successful
    
    Raises:
        DeveloperRegistrationError: If storage fails
    """
    try:
        # Add comprehensive logging for debugging
        logger.info(f"store_api_key_in_ssm called with:")
        logger.info(f"  parameter_name: '{parameter_name}' (type: {type(parameter_name)})")
        logger.info(f"  description: '{description}' (type: {type(description)})")
        logger.info(f"  api_key_data keys: {list(api_key_data.keys()) if api_key_data else 'None'}")
        logger.info(f"  backup_old_key: {backup_old_key}")
        
        # Validate parameter name is not empty
        if not parameter_name or not parameter_name.strip():
            logger.error(f"SSM parameter name is empty or None: '{parameter_name}'")
            raise DeveloperRegistrationError("SSM parameter name cannot be empty")
        
        # Validate description is not empty
        if not description or not description.strip():
            logger.warning(f"SSM parameter description is empty, using default")
            description = "API key for game developer"
        
        # Validate parameter name length (SSM limit is 2048 characters)
        if len(parameter_name) > 2048:
            logger.error(f"SSM parameter name too long: {len(parameter_name)} characters")
            raise DeveloperRegistrationError(f"SSM parameter name too long: {len(parameter_name)} characters")
        
        # Backup old key if requested and parameter exists
        if backup_old_key:
            try:
                existing_response = ssm.get_parameter(
                    Name=parameter_name,
                    WithDecryption=True
                )
                
                # Create backup parameter with timestamp
                backup_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                backup_parameter_name = f"{parameter_name}-backup-{backup_timestamp}"
                
                # Parse existing data and mark as backup/inactive
                existing_data = json.loads(existing_response['Parameter']['Value'])
                existing_data['status'] = 'backup'
                existing_data['backedUpAt'] = datetime.now(timezone.utc).isoformat()
                existing_data['originalParameter'] = parameter_name
                
                # Store backup with 30-day TTL tag
                backup_tags = [
                    {'Key': 'BackupOf', 'Value': parameter_name},
                    {'Key': 'BackupDate', 'Value': backup_timestamp},
                    {'Key': 'AutoDelete', 'Value': 'true'},
                    {'Key': 'DeleteAfter', 'Value': (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()}
                ]
                
                ssm.put_parameter(
                    Name=backup_parameter_name,
                    Value=json.dumps(existing_data),
                    Type='SecureString',
                    Description=f"Backup of {parameter_name} created on {backup_timestamp}",
                    Overwrite=False,
                    Tier='Standard',
                    Tags=backup_tags
                )
                
                logger.info(f"Created backup parameter: {backup_parameter_name}")
                
            except ClientError as e:
                if e.response['Error']['Code'] == 'ParameterNotFound':
                    logger.info("No existing parameter to backup - this is a new registration")
                else:
                    logger.warning(f"Could not create backup: {e}")
                    # Continue with main operation even if backup fails
        
        # Validate parameter value size (SSM limit is 4KB for String, 8KB for SecureString)
        parameter_value = json.dumps(api_key_data, separators=(',', ':'))  # Compact JSON
        logger.info(f"Parameter value size: {len(parameter_value)} bytes")
        
        if len(parameter_value) > 4096:  # Using 4KB limit for SecureString
            logger.error(f"SSM parameter value too large: {len(parameter_value)} bytes")
            raise DeveloperRegistrationError(f"SSM parameter value too large: {len(parameter_value)} bytes")
        
        # Add validation for parameter name format
        if not parameter_name.startswith('/'):
            logger.error(f"SSM parameter name must start with '/': {parameter_name}")
            raise DeveloperRegistrationError(f"SSM parameter name must start with '/': {parameter_name}")
        
        logger.info(f"About to call SSM put_parameter with:")
        logger.info(f"  Name: '{parameter_name}'")
        logger.info(f"  Type: 'SecureString'")
        logger.info(f"  Description: '{description}'")
        logger.info(f"  Overwrite: True")
        logger.info(f"  Tier: 'Standard'")
        
        # Define tags separately to handle AWS SSM limitation
        tags = [
            {'Key': 'Environment', 'Value': ENVIRONMENT},
            {'Key': 'Service', 'Value': 'game-statsleaderboards'},
            {'Key': 'Type', 'Value': 'api-key'},
            {'Key': 'CreatedBy', 'Value': 'developer-registration'},
            {'Key': 'LastUpdated', 'Value': datetime.now(timezone.utc).isoformat()}
        ]
        
        # First, try to create/update the parameter without tags
        # AWS SSM doesn't allow tags and overwrite together
        ssm_params = {
            'Name': str(parameter_name),
            'Value': str(parameter_value),
            'Type': 'SecureString',
            'Description': str(description),
            'Overwrite': True,
            'Tier': 'Standard'
            # Note: No Tags here due to AWS limitation
        }
        
        logger.info(f"SSM parameters (without tags): {ssm_params}")
        
        # Store in SSM Parameter Store
        response = ssm.put_parameter(**ssm_params)
        
        logger.info(f"SSM parameter stored successfully, now adding tags...")
        
        # Separately add tags to the parameter
        try:
            ssm.add_tags_to_resource(
                ResourceType='Parameter',
                ResourceId=parameter_name,
                Tags=tags
            )
            logger.info(f"Tags added successfully to parameter: {parameter_name}")
        except ClientError as tag_error:
            # Log tag error but don't fail the entire operation
            logger.warning(f"Failed to add tags to SSM parameter: {tag_error}")
            # Continue - the parameter was created successfully
        
        logger.info(f"Successfully stored SSM parameter: {parameter_name}")
        logger.info(f"SSM response: {response}")

        # Publish studioId/gameId to a non-secret parameter the player authorizer reads.
        store_registration_metadata(api_key_data.get('studioId'), api_key_data.get('gameId'))

        return True
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        
        logger.error(f"SSM ClientError - Code: {error_code}, Message: {error_message}")
        logger.error(f"Failed parameter name: '{parameter_name}'")
        logger.error(f"Failed parameter value length: {len(parameter_value) if 'parameter_value' in locals() else 'unknown'}")
        
        # Handle specific SSM errors
        if error_code == 'ParameterLimitExceeded':
            raise DeveloperRegistrationError("SSM parameter limit exceeded for this account")
        elif error_code == 'ParameterPatternMismatchException':
            raise DeveloperRegistrationError(f"Invalid SSM parameter name format: {parameter_name}")
        elif error_code == 'InvalidParameterName':
            raise DeveloperRegistrationError(f"Invalid SSM parameter name: {parameter_name}")
        elif error_code == 'ParameterAlreadyExists':
            # This shouldn't happen with Overwrite=True, but handle it
            logger.warning(f"Parameter already exists but Overwrite=True: {parameter_name}")
            raise DeveloperRegistrationError("Parameter already exists and cannot be overwritten")
        elif error_code in ['AccessDeniedException', 'UnauthorizedException']:
            raise DeveloperRegistrationError("Insufficient permissions to store SSM parameter")
        else:
            raise DeveloperRegistrationError(f"Failed to store SSM parameter: {error_message}")
    
    except Exception as e:
        logger.exception(f"Unexpected error storing SSM parameter: {parameter_name}")
        raise DeveloperRegistrationError(f"Unexpected error storing API key: {str(e)}")

def update_authorizer_parameter_names():
    """
    Update the authorizer Lambda's API_KEY_PARAMETER_NAMES environment variable.
    This enables high-performance authorization at 10,000 TPS instead of 100 TPS.
    
    Called after creating/updating/rotating API keys to keep the authorizer in sync.
    """
    if not AUTHORIZER_FUNCTION_NAME:
        logger.warning("AUTHORIZER_FUNCTION_NAME not set, skipping authorizer env var update")
        logger.warning("Authorizer will use fallback discovery (100 TPS instead of 10,000 TPS)")
        return
    
    try:
        logger.info(f"Updating authorizer environment variable for high-performance authorization")
        
        # Discover all API key parameters
        parameter_names = []
        paginator = ssm.get_paginator('get_parameters_by_path')
        
        for page in paginator.paginate(
            Path=f"{SSM_PARAMETER_PREFIX}/api-keys/",
            Recursive=False,
            WithDecryption=False
        ):
            for parameter in page.get('Parameters', []):
                param_name = parameter.get('Name', '')
                if param_name:
                    parameter_names.append(param_name)
        
        logger.info(f"Found {len(parameter_names)} API key parameter(s)")
        
        # Get current Lambda configuration
        response = lambda_client.get_function_configuration(
            FunctionName=AUTHORIZER_FUNCTION_NAME
        )
        
        current_env = response.get('Environment', {}).get('Variables', {})
        
        # Update API_KEY_PARAMETER_NAMES
        current_env['API_KEY_PARAMETER_NAMES'] = json.dumps(parameter_names)
        
        # Update Lambda configuration
        lambda_client.update_function_configuration(
            FunctionName=AUTHORIZER_FUNCTION_NAME,
            Environment={'Variables': current_env}
        )
        
        logger.info(f"✅ Updated authorizer with {len(parameter_names)} parameter name(s) for 10,000 TPS performance")
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.error(f"Failed to update authorizer environment variable: {error_code}")
        logger.warning("Authorizer will use fallback discovery (100 TPS instead of 10,000 TPS)")
        # Don't fail the registration - this is an optimization, not critical
    except Exception as e:
        logger.error(f"Unexpected error updating authorizer: {str(e)}")
        logger.warning("Authorizer will use fallback discovery (100 TPS instead of 10,000 TPS)")
        # Don't fail the registration - this is an optimization, not critical

def retrieve_api_key_from_ssm(parameter_name: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve API key data from SSM Parameter Store with proper error handling.
    
    Args:
        parameter_name: SSM parameter name
    
    Returns:
        API key data if found, None if not found
    
    Raises:
        DeveloperRegistrationError: If retrieval fails (not including not found)
                                   If key is revoked, raises with specific message
    """
    try:
        logger.info(f"Retrieving SSM parameter: {parameter_name}")
        
        response = ssm.get_parameter(
            Name=parameter_name,
            WithDecryption=True
        )
        
        parameter_value = response['Parameter']['Value']
        api_key_data = json.loads(parameter_value)
        
        # Validate that the key is not revoked or backup
        key_status = api_key_data.get('status', 'unknown')
        if key_status == 'revoked':
            logger.warning(f"API key is revoked: {parameter_name}")
            raise RevokedKeyError("API key has been revoked")
        elif key_status != 'active':
            logger.warning(f"API key is not active (status: {key_status}): {parameter_name}")
            raise DeveloperRegistrationError(f"API key is not active - current status: {key_status}")
        
        logger.info(f"Successfully retrieved SSM parameter: {parameter_name}")
        return api_key_data
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        
        logger.error(f"SSM ClientError retrieving parameter - Code: {error_code}, Message: {error_message}")
        
        if error_code == 'ParameterNotFound':
            logger.warning(f"SSM parameter not found: {parameter_name}")
            return None
        elif error_code in ['AccessDeniedException', 'UnauthorizedException']:
            raise DeveloperRegistrationError("Insufficient permissions to retrieve SSM parameter")
        else:
            raise DeveloperRegistrationError(f"Failed to retrieve SSM parameter: {error_message}")
    
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in SSM parameter {parameter_name}: {e}")
        raise DeveloperRegistrationError("Invalid API key data format in SSM parameter")
    
    except RevokedKeyError:
        # Re-raise revoked key errors without wrapping
        raise
    
    except DeveloperRegistrationError:
        # Re-raise registration errors without wrapping
        raise
    
    except Exception as e:
        logger.exception(f"Unexpected error retrieving SSM parameter: {parameter_name}")
        raise DeveloperRegistrationError(f"Unexpected error retrieving API key: {str(e)}")

def validate_authenticated_context(event: Dict[str, Any], required_studio_id: str = None, required_game_id: str = None) -> Dict[str, str]:
    """
    Validate authenticated context from API Gateway authorizer.
    
    Args:
        event: Lambda event containing authorizer context
        required_studio_id: Required studio ID (optional validation)
        required_game_id: Required game ID (optional validation)
    
    Returns:
        Authenticated context data
    
    Raises:
        DeveloperRegistrationError: If authentication validation fails
    """
    
    # Extract authorizer context
    auth_context = event.get('requestContext', {}).get('authorizer', {})
    
    if not auth_context:
        logger.error("No authorizer context found in request")
        raise DeveloperRegistrationError("Authentication required - no authorizer context found")
    
    # Extract required fields from context
    authenticated_studio_id = auth_context.get('studioId')
    authenticated_game_id = auth_context.get('gameId')
    studio_name = auth_context.get('studioName')
    game_title = auth_context.get('gameTitle')
    contact_email = auth_context.get('contactEmail')
    permissions = auth_context.get('permissions', '').split(',') if auth_context.get('permissions') else []
    key_status = auth_context.get('keyStatus')  # SECURITY FIX: Extract key status
    
    if not authenticated_studio_id:
        logger.error("No authenticated studio ID found in authorizer context")
        raise DeveloperRegistrationError("Authentication required - invalid studio credentials")
    
    if not authenticated_game_id:
        logger.error("No authenticated game ID found in authorizer context")
        raise DeveloperRegistrationError("Authentication required - invalid game credentials")
    
    # SECURITY FIX: Re-validate key status from SSM (don't trust cached authorizer context)
    # This ensures revoked/backup keys are rejected even if authorizer cache is stale
    try:
        ssm_parameter_name = construct_ssm_parameter_name(
            SSM_PARAMETER_PREFIX,
            authenticated_studio_id,
            authenticated_game_id
        )
        current_key_data = retrieve_api_key_from_ssm(ssm_parameter_name)
        
        if not current_key_data:
            logger.error(f"API key not found in SSM: {ssm_parameter_name}")
            raise DeveloperRegistrationError("API key not found or has been revoked")
        
        # CRITICAL: Validate that the API key from the request matches the current active key in SSM
        # This prevents old/backup keys from being accepted even if they have the same studioId/gameId
        request_api_key = auth_context.get('apiKey')
        current_api_key = current_key_data.get('apiKey')
        
        if request_api_key and current_api_key and request_api_key != current_api_key:
            logger.error(f"API key mismatch: request key does not match current active key")
            raise DeveloperRegistrationError("API key is not current - please use the latest API key")
        
        # Validate current status from SSM (not from cached authorizer context)
        current_status = current_key_data.get('status', 'unknown')
        if current_status != 'active':
            logger.error(f"API key status is not active: {current_status}")
            raise DeveloperRegistrationError(f"API key is not active - current status: {current_status}")
            
    except RevokedKeyError:
        # Re-raise revoked key errors
        raise
    except DeveloperRegistrationError:
        # Re-raise registration errors
        raise
    except Exception as e:
        logger.error(f"Failed to validate key status from SSM: {str(e)}")
        raise DeveloperRegistrationError("Failed to validate API key status")
    
    # Validate against required IDs if provided
    if required_studio_id and authenticated_studio_id != required_studio_id:
        logger.warning(f"Studio ID mismatch: authenticated={authenticated_studio_id}, required={required_studio_id}")
        raise DeveloperRegistrationError("Studio ID mismatch - you can only access your own studio's data")
    
    if required_game_id and authenticated_game_id != required_game_id:
        logger.warning(f"Game ID mismatch: authenticated={authenticated_game_id}, required={required_game_id}")
        raise DeveloperRegistrationError("Game ID mismatch - you can only access your own game's data")
    
    logger.info(f"Authentication validated for studio: {authenticated_studio_id}, game: {authenticated_game_id} with active key")
    
    return {
        'studioId': authenticated_studio_id,
        'gameId': authenticated_game_id,
        'studioName': studio_name or '',
        'gameTitle': game_title or '',
        'contactEmail': contact_email or '',
        'permissions': permissions
    }

@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
@tracer.capture_lambda_handler
@handle_errors
@log_execution
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    Handle developer registration and API key management using SSM Parameter Store
    
    Endpoints:
    - POST /developer/register - Register new developer (update existing SSM parameter)
    - POST /developer/regenerate-key - Regenerate API key
    - GET /developer/info - Get developer information
    - PUT /developer/revoke - Revoke API key
    """

    # Log the resolved environment variables
    logger.info(f"Lambda Handler Start: ENVIRONMENT: '{ENVIRONMENT}' -- SSM_PARAMETER_PREFIX: '{SSM_PARAMETER_PREFIX}' -- API_ENDPOINT: '{API_ENDPOINT}'")
    logger.info(f"Full event: {event}")

    try:
        # Parse request, Robust HTTP method detection
        http_method = event.get('httpMethod')
        if not http_method:
            request_context = event.get('requestContext', {})
            if request_context:
                # For API Gateway REST API
                http_method = request_context.get('httpMethod')
                if not http_method:
                    # For API Gateway HTTP API (v2)
                    http_context = request_context.get('http', {})
                    if http_context:
                        http_method = http_context.get('method')

        # Default to GET for info endpoints, POST for others
        if not http_method:
            path = event.get('path', event.get('rawPath', ''))
            http_method = 'GET' if 'info' in path else 'POST'

        path = event.get('path', event.get('rawPath', ''))
        body = event.get('body', '{}')

        # Method + path at INFO; full request body at DEBUG (avoids logging
        # request bodies to CloudWatch at INFO — L2).
        logger.info(f"Request: http_method={http_method}, path={path}")
        logger.debug(f"Request body: {body}")

        # Handle request data based on HTTP method
        if http_method == 'GET':
            # GET requests don't have a body, data comes from query parameters
            request_data = {}
            studio_name_for_log = 'N/A (GET request)'
        else:
            # POST/PUT/DELETE requests have body data
            if isinstance(body, str):
                parsed_body = json.loads(body) if body else {}
            else:
                parsed_body = body if body is not None else {}
            
            # Extract from devRegRequest wrapper if present, otherwise use direct format for backward compatibility
            if 'devRegRequest' in parsed_body:
                request_data = parsed_body['devRegRequest']
                logger.info("Request data extracted from devRegRequest wrapper")
            else:
                request_data = parsed_body
                logger.info("Request data using direct format (backward compatibility)")
            
            studio_name_for_log = request_data.get('studioName', 'unknown') if request_data else 'unknown'
        
        logger.info("Processing developer registration request", extra={
            "method": http_method,
            "path": path,
            "studio_name": studio_name_for_log,
            "environment": ENVIRONMENT
        })
        
        # Route to appropriate handler
        if http_method == 'POST' and 'register' in path:
            return handle_developer_registration(request_data, event)
        elif http_method == 'POST' and 'regenerate-key' in path:
            return handle_key_regeneration(request_data, event)
        elif http_method == 'GET' and 'info' in path:
            return handle_developer_info(event)
        elif http_method == 'PUT' and 'revoke' in path:
            return handle_key_revocation(request_data, event)
        else:
            return wrap_response(404, {
                'error': 'Endpoint not found',
                'availableEndpoints': [
                    'POST /developer/register',
                    'POST /developer/regenerate-key',
                    'GET /developer/info',
                    'PUT /developer/revoke'
                ]
            })
    
    except json.JSONDecodeError:
        logger.error("Invalid JSON in request body")
        return wrap_response(400, {'error': 'Invalid JSON in request body'})
    except RevokedKeyError as e:
        logger.warning(f"Revoked key access attempt: {str(e)}")
        return wrap_response(403, {
            'error': 'Access Denied',
            'message': str(e),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    except DeveloperRegistrationError as e:
        logger.warning(f"Registration validation error: {str(e)}")
        return wrap_response(400, {'error': str(e)})
    except Exception as e:
        logger.error(f"Unexpected error in developer registration: {str(e)}")
        metrics.add_metric(name="RegistrationErrors", unit=MetricUnit.Count, value=1)
        return wrap_response(500, {
            'error': 'Internal Server Error',
            'message': str(e),
            'requestId': context.aws_request_id,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

def handle_developer_registration(request_data: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle developer registration with double authentication and complete refresh
    """

    logger.info(f"handle_developer_registration called")
    logger.info(f"Using SSM_PARAMETER_PREFIX: '{SSM_PARAMETER_PREFIX}', ENVIRONMENT: '{ENVIRONMENT}'")

    # Authentication - Re-validate authorizer context against SSM
    try:
        auth_context = validate_authenticated_context(event)
        authenticated_studio_id = auth_context['studioId']
        authenticated_game_id = auth_context['gameId']
        logger.info(f"Double authentication successful: {authenticated_studio_id}/{authenticated_game_id}")
    except DeveloperRegistrationError as e:
        logger.error(f"Double authentication failed: {str(e)}")
        return wrap_response(401, {
            'error': 'Authentication required',
            'message': str(e),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

    # Validate and sanitize the NEW registration data
    validated_data = validate_registration_request(request_data)
    logger.info(f"New registration data validated: {validated_data}")

    # Use NEW data for business logic (ignore authenticated context for business logic)
    new_studio_id = validated_data['studioId']    # Generated from NEW studioName
    new_game_id = validated_data['gameId']        # Generated from NEW gameTitle

    logger.info(f"Processing registration for NEW studio/game: {new_studio_id}/{new_game_id}")
    logger.info(f"Authenticated context was: {authenticated_studio_id}/{authenticated_game_id} (used only for auth)")

    # Prepare for clean overwrite - identify old SSM parameter
    old_ssm_parameter_name = construct_ssm_parameter_name(
        SSM_PARAMETER_PREFIX,
        authenticated_studio_id,  # Old studio ID from auth context
        authenticated_game_id     # Old game ID from auth context
    )

    # Create NEW SSM parameter name
    new_ssm_parameter_name = construct_ssm_parameter_name(
        SSM_PARAMETER_PREFIX,
        new_studio_id,    # NEW studio ID
        new_game_id       # NEW game ID
    )

    logger.info(f"Old SSM parameter: {old_ssm_parameter_name}")
    logger.info(f"New SSM parameter: {new_ssm_parameter_name}")

    # Check if we're overwriting the same parameter or creating a new one
    is_same_parameter = (old_ssm_parameter_name == new_ssm_parameter_name)
    logger.info(f"Same parameter path: {is_same_parameter}")

    try:
        # Generate fresh API key for NEW studio/game
        api_key = generate_api_key(
            validated_data['studioName'],  # NEW studio name
            new_game_id,                   # NEW game ID
            environment=ENVIRONMENT
        )
        logger.info(f"Generated fresh API key for {new_studio_id}/{new_game_id}")

        # Get rate limit from environment or SSM configuration
        try:
            current_rate_limit = int(get_config_value('environment_config', 'current_rate_limit'))
        except (EnvironmentConfigurationError, ValueError):
            current_rate_limit = int(DEFAULT_RATE_LIMIT)

        # Create NEW registration data
        api_key_data = {
            'apiKey': api_key,
            'studioId': new_studio_id,                          # NEW studio ID
            'gameId': new_game_id,                              # NEW game ID
            'studioName': validated_data['studioName'],         # NEW studio name
            'gameTitle': validated_data['gameTitle'],           # NEW game title
            'gameGenre': validated_data['gameGenre'],
            'contactEmail': validated_data['contactEmail'],
            'permissions': ['read', 'write'],
            'rateLimit': current_rate_limit,
            'status': 'active',
            'environment': ENVIRONMENT,
            'createdAt': datetime.now(timezone.utc).isoformat(),
            'lastUpdated': datetime.now(timezone.utc).isoformat(),
            'registrationDate': datetime.now(timezone.utc).isoformat(),
            'lastKeyRotation': datetime.now(timezone.utc).isoformat(),
            'keyRotationSchedule': 90,  # days
            'previousRegistration': f"{authenticated_studio_id}/{authenticated_game_id}",  # Audit trail
            'registrationMethod': 'complete_refresh'
        }

        logger.info(f"Creating NEW registration in SSM for environment '{ENVIRONMENT}': {new_studio_id}/{new_game_id}")

        # BEFORE creating new API key, deactivate all existing active keys
        try:
            logger.info("Deactivating all existing active API keys for single-tenant system...")

            # Get all existing API key parameters
            paginator = ssm.get_paginator('get_parameters_by_path')
            for page in paginator.paginate(
                Path=f"{SSM_PARAMETER_PREFIX}/api-keys/",
                Recursive=True,
                WithDecryption=True,
                MaxResults=10
            ):
                for parameter in page.get('Parameters', []):
                    try:
                        existing_key_data = json.loads(parameter['Value'])
                        if existing_key_data.get('status') == 'active':
                            # Mark as inactive
                            existing_key_data['status'] = 'inactive'
                            existing_key_data['deactivatedAt'] = datetime.now(timezone.utc).isoformat()
                            existing_key_data['lastUpdated'] = datetime.now(timezone.utc).isoformat()

                            # Update the parameter
                            ssm.put_parameter(
                                Name=parameter['Name'],
                                Value=json.dumps(existing_key_data),
                                Type='SecureString',
                                Overwrite=True
                            )
                            logger.info(f"Deactivated existing API key: {parameter['Name']}")
                    except Exception as e:
                        logger.warning(f"Failed to deactivate parameter {parameter['Name']}: {str(e)}")

        except Exception as e:
            logger.warning(f"Failed to deactivate existing keys: {str(e)}")

        # Write NEW registration to SSM
        description = f"API key for {validated_data['studioName']} - {validated_data['gameTitle']} ({ENVIRONMENT})"
        store_api_key_in_ssm(new_ssm_parameter_name, api_key_data, description, backup_old_key=False)

        logger.info(f"NEW registration stored successfully: {new_ssm_parameter_name}")

        # Clean up - Delete old SSM parameter if different from new one
        if not is_same_parameter:
            try:
                logger.info(f"Deleting old SSM parameter: {old_ssm_parameter_name}")
                ssm.delete_parameter(Name=old_ssm_parameter_name)
                logger.info(f"Old SSM parameter deleted successfully")
            except ClientError as e:
                if e.response['Error']['Code'] == 'ParameterNotFound':
                    logger.warning(f"Old SSM parameter not found (already deleted): {old_ssm_parameter_name}")
                else:
                    logger.error(f"Error deleting old SSM parameter: {str(e)}")
                    # Don't fail the registration if cleanup fails
        else:
            logger.info(f"Same parameter path - no cleanup needed")
        
        # Update authorizer environment variable AFTER cleanup for high-performance authorization (10,000 TPS)
        # This ensures only active parameters are in the environment variable
        update_authorizer_parameter_names()

        metrics.add_metric(name="DeveloperRegistrations", unit=MetricUnit.Count, value=1)
        logger.info(f"Using API endpoint: {API_ENDPOINT}")

        # Flush authorizer cache to immediately invalidate old API keys
        flush_authorizer_cache()

        # Return success response with NEW API key
        return wrap_response(201, {
            'success': True,
            'message': 'Developer registration completed successfully',
            'registration': {
                'studioId': new_studio_id,              # NEW studio ID
                'gameId': new_game_id,                  # NEW game ID
                'studioName': validated_data['studioName'],
                'gameTitle': validated_data['gameTitle'],
                # 'apiKey': api_key,                      # SECURITY: API key NOT returned in response - retrieve from SSM Parameter Store
                'permissions': ['read', 'write'],
                'rateLimit': current_rate_limit,
                'environment': ENVIRONMENT,
                'registrationDate': api_key_data['registrationDate']
            },
            'usage': {
                'apiEndpoint': API_ENDPOINT,
                'documentation': f'https://your-docs-url.com/{ENVIRONMENT}',
                'supportEmail': 'support@your-domain.com'
            },
            'changes': {
                'previousRegistration': f"{authenticated_studio_id}/{authenticated_game_id}",
                'newRegistration': f"{new_studio_id}/{new_game_id}",
                'parameterUpdated': new_ssm_parameter_name,
                'oldParameterDeleted': old_ssm_parameter_name if not is_same_parameter else None
            },
            'note': 'Registration completely refreshed. Use the NEW API key for all subsequent requests. Old API key is no longer valid.'
        })

    except Exception as e:
        logger.error(f"Error during registration refresh: {str(e)}")
        logger.exception("Full exception details:")
        raise DeveloperRegistrationError("Failed to complete registration refresh")

def handle_key_regeneration(request_data: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle API key regeneration using SSM Parameter Store with backup
    """
    logger.info("=== Starting key regeneration process ===")
    logger.debug(f"Request data: {json.dumps(request_data, default=str)}")

    required_fields = ['studioId', 'gameId', 'contactEmail']
    missing_fields = [field for field in required_fields if not request_data.get(field)]

    if missing_fields:
        logger.error(f"Missing required fields: {missing_fields}")
        raise DeveloperRegistrationError(f"Missing required fields: {', '.join(missing_fields)}")

    # Validate input characters for studioId and gameId
    try:
        validate_input_characters("studioId", request_data['studioId'])
        validate_input_characters("gameId", request_data['gameId'])
        
        # Validate email format
        email = request_data['contactEmail']
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            raise DeveloperRegistrationError("Email contains invalid characters. Only letters, numbers, dots, underscores, percent signs, plus signs, and hyphens are allowed in email addresses")
    except DeveloperRegistrationError as e:
        logger.error(f"Input validation failed: {str(e)}")
        raise

    # Validate authenticated context
    try:
        auth_context = validate_authenticated_context(
            event, 
            required_studio_id=request_data['studioId'],
            required_game_id=request_data['gameId']
        )
    except DeveloperRegistrationError as e:
        logger.error(f"Authentication validation failed: {str(e)}")
        return wrap_response(401, {
            'error': 'Authentication required',
            'message': str(e),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

    # Log environment variables being used
    logger.info(f"Using SSM_PARAMETER_PREFIX: '{SSM_PARAMETER_PREFIX}', ENVIRONMENT: '{ENVIRONMENT}'")

    try:
        # Get existing registration from SSM with detailed logging
        ssm_parameter_name = construct_ssm_parameter_name(
            SSM_PARAMETER_PREFIX,
            request_data['studioId'],
            request_data['gameId']
        )
        
        logger.info(f"Retrieving existing registration from SSM: {ssm_parameter_name}")
        
        existing_data = retrieve_api_key_from_ssm(ssm_parameter_name)

        if not existing_data:
            logger.warning(f"Developer registration not found for {request_data['studioId']}/{request_data['gameId']}")
            return wrap_response(404, {'error': 'Developer registration not found'})

        logger.debug(f"Found existing registration: {json.dumps(existing_data, default=str)}")

        # Verify contact email matches with detailed logging
        stored_email = existing_data.get('contactEmail', '')
        provided_email = request_data['contactEmail']
        logger.info(f"Email comparison - Stored: '{stored_email}', Provided: '{provided_email}'")

        if stored_email != provided_email:
            logger.warning(f"Email mismatch for key regeneration: stored='{stored_email}', provided='{provided_email}'")
            return wrap_response(403, {'error': 'Contact email does not match registration'})

        # Generate new API key with logging
        logger.info("Generating new API key...")
        new_api_key = generate_api_key(
            existing_data['studioName'],
            existing_data['gameId'],
            environment=ENVIRONMENT
        )
        logger.info(f"Generated new API key: {new_api_key[:20]}...")  # Log partial key for security

        # Update API key data
        updated_data = existing_data.copy()
        updated_data.update({
            'apiKey': new_api_key,
            'lastKeyRotation': datetime.now(timezone.utc).isoformat(),
            'lastUpdated': datetime.now(timezone.utc).isoformat(),
            'environment': ENVIRONMENT
        })

        # Store updated data in SSM with backup
        logger.info("Updating SSM parameter...")
        try:
            description = f"API key for {existing_data['studioName']} - {existing_data['gameTitle']} (regenerated in {ENVIRONMENT})"
            store_api_key_in_ssm(ssm_parameter_name, updated_data, description, backup_old_key=True)
            
            # Update authorizer environment variable for high-performance authorization (10,000 TPS)
            update_authorizer_parameter_names()
            
            logger.info("SSM parameter updated successfully")

        except Exception as ssm_error:
            logger.error(f"SSM parameter update failed: {str(ssm_error)}")
            logger.error(f"SSM error type: {type(ssm_error).__name__}")
            raise DeveloperRegistrationError(f"Failed to update API key: {str(ssm_error)}")

        logger.info(f"API key regenerated successfully for: {request_data['studioId']}/{request_data['gameId']}")
        metrics.add_metric(name="KeyRegenerations", unit=MetricUnit.Count, value=1)

        # Flush authorizer cache to immediately invalidate old API key
        flush_authorizer_cache()

        return wrap_response(200, {
            'success': True,
            'message': 'API key regenerated successfully',
            # 'newApiKey': new_api_key,                # SECURITY: API key NOT returned in response - retrieve from SSM Parameter Store
            'environment': ENVIRONMENT,
            'regenerationDate': datetime.now(timezone.utc).isoformat(),
            'note': 'New API key stored in SSM Parameter Store. Retrieve using studioId and gameId.'
        })

    except DeveloperRegistrationError:
        # Re-raise our custom errors
        raise
    except Exception as e:
        logger.error(f"Unexpected error in key regeneration: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.exception("Full stack trace:")
        raise DeveloperRegistrationError(f"Key regeneration failed: {str(e)}")

def handle_developer_info(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle developer information retrieval from SSM Parameter Store
    """

    logger.info("Inside handle_developer_info(), starting...")

    # Add defensive null check
    if event is None:
        logger.error("Event parameter is None in handle_developer_info")
        return wrap_response(500, {'error': 'Internal server error - invalid event data'})

    # Extract studio and game ID from query parameters
    query_params = event.get('queryStringParameters')
    if query_params is None:
        logger.error("queryStringParameters is None in event")
        return wrap_response(400, {'error': 'Missing query parameters'})

    studio_id = query_params.get('studioId')
    game_id = query_params.get('gameId')

    if not studio_id or not game_id:
        return wrap_response(400, {'error': 'Missing required query parameters: studioId, gameId'})

    # Validate input characters for studioId and gameId
    try:
        validate_input_characters("studioId", studio_id)
        validate_input_characters("gameId", game_id)
    except DeveloperRegistrationError as e:
        logger.error(f"Input validation failed in developer info: {str(e)}")
        return wrap_response(400, {'error': str(e)})

    # Validate authenticated context
    try:
        auth_context = validate_authenticated_context(
            event, 
            required_studio_id=studio_id,
            required_game_id=game_id
        )
    except DeveloperRegistrationError as e:
        logger.error(f"Authentication validation failed: {str(e)}")
        return wrap_response(401, {
            'error': 'Authentication required',
            'message': str(e),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

    logger.info("Inside handle_developer_info(), about to retrieve from SSM")

    try:
        # Get registration data from SSM
        ssm_parameter_name = construct_ssm_parameter_name(
            SSM_PARAMETER_PREFIX,
            studio_id,
            game_id
        )
        
        registration_data = retrieve_api_key_from_ssm(ssm_parameter_name)
        
        if not registration_data:
            return wrap_response(404, {'error': 'Developer registration not found'})
        
        # Return info without API key
        return wrap_response(200, {
            'studioId': registration_data['studioId'],
            'gameId': registration_data['gameId'],
            'studioName': registration_data['studioName'],
            'gameTitle': registration_data['gameTitle'],
            'gameGenre': registration_data.get('gameGenre', ''),
            'permissions': registration_data['permissions'],
            'rateLimit': int(registration_data['rateLimit']),
            'status': registration_data['status'],
            'registrationDate': registration_data.get('registrationDate', registration_data.get('createdAt', '')),
            'lastKeyRotation': registration_data.get('lastKeyRotation', ''),
            'environment': registration_data['environment']
        })
        
    except Exception as e:
        logger.error(f"Error retrieving developer info: {str(e)}")
        return wrap_response(500, {'error': 'Failed to retrieve developer information'})

def handle_key_revocation(request_data: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle API key revocation using SSM Parameter Store with backup
    """

    required_fields = ['studioId', 'gameId', 'contactEmail']
    missing_fields = [field for field in required_fields if not request_data.get(field)]
    
    if missing_fields:
        raise DeveloperRegistrationError(f"Missing required fields: {', '.join(missing_fields)}")
    
    # Validate input characters for studioId and gameId
    try:
        validate_input_characters("studioId", request_data['studioId'])
        validate_input_characters("gameId", request_data['gameId'])
        
        # Validate email format
        email = request_data['contactEmail']
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            raise DeveloperRegistrationError("Email contains invalid characters. Only letters, numbers, dots, underscores, percent signs, plus signs, and hyphens are allowed in email addresses")
    except DeveloperRegistrationError as e:
        logger.error(f"Input validation failed in key revocation: {str(e)}")
        raise
    
    # Validate authenticated context
    try:
        auth_context = validate_authenticated_context(
            event, 
            required_studio_id=request_data['studioId'],
            required_game_id=request_data['gameId']
        )
    except DeveloperRegistrationError as e:
        logger.error(f"Authentication validation failed: {str(e)}")
        return wrap_response(401, {
            'error': 'Authentication required',
            'message': str(e),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    try:
        # Get existing registration from SSM
        ssm_parameter_name = construct_ssm_parameter_name(
            SSM_PARAMETER_PREFIX,
            request_data['studioId'],
            request_data['gameId']
        )
        
        existing_data = retrieve_api_key_from_ssm(ssm_parameter_name)
        
        if not existing_data:
            return wrap_response(404, {'error': 'Developer registration not found'})
        
        if existing_data['contactEmail'] != request_data['contactEmail']:
            return wrap_response(403, {'error': 'Contact email does not match registration'})
        
        # Update status to revoked
        revoked_data = existing_data.copy()
        revoked_data.update({
            'status': 'revoked',
            'revokedAt': datetime.now(timezone.utc).isoformat(),
            'lastUpdated': datetime.now(timezone.utc).isoformat(),
            'permissions': [],  # Remove all permissions
            'rateLimit': 0,     # Set rate limit to 0
            'environment': ENVIRONMENT
        })
        
        # Store revoked data in SSM with backup
        description = f"API key for {existing_data['studioName']} - {existing_data['gameTitle']} (revoked in {ENVIRONMENT})"
        store_api_key_in_ssm(ssm_parameter_name, revoked_data, description, backup_old_key=True)
        
        # Update authorizer environment variable for high-performance authorization (10,000 TPS)
        update_authorizer_parameter_names()
        
        logger.info(f"API key revoked for: {request_data['studioId']}/{request_data['gameId']} in {ENVIRONMENT}")
        metrics.add_metric(name="KeyRevocations", unit=MetricUnit.Count, value=1)
        
        # Flush authorizer cache to immediately invalidate revoked API key
        flush_authorizer_cache()
        
        return wrap_response(200, {
            'success': True,
            'message': 'API key revoked successfully',
            'environment': ENVIRONMENT,
            'revocationDate': datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error revoking API key: {str(e)}")
        raise DeveloperRegistrationError("Failed to revoke API key")

async def cleanup_resources():
    """
    Cleanup function for Lambda container reuse optimization.
    """
    global thread_pool, _ssm_config_cache
    
    cleanup_tasks = []
    
    if thread_pool:
        try:
            thread_pool.shutdown(wait=False)
            thread_pool = None
        except Exception as e:
            logger.warning(f"Error shutting down thread pool: {str(e)}")
    
    # Clear SSM config cache
    _ssm_config_cache = None
    
    if cleanup_tasks:
        try:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            logger.info("Successfully cleaned up all resources")
        except Exception as e:
            logger.warning(f"Error during cleanup: {str(e)}")

# Register cleanup for Lambda container lifecycle
import atexit
atexit.register(lambda: run_async(cleanup_resources()))