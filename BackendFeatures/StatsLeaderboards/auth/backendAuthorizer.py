# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
backendAuthorizer.py -- Backend API Gateway Lambda Authorizer
SSM Parameter Store Based Authentication, SSM Parameter Store as the single source of truth
"""

import json
import os
import time
import hashlib
import boto3
import hmac
import threading
from typing import Dict, Any, Optional, Tuple
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.exceptions import ClientError

# Initialize AWS Lambda Powertools
logger = Logger(service="backend-authorizer")
tracer = Tracer(service="backend-authorizer")

# AWS clients with optimized configuration
ssm = boto3.client('ssm', config=boto3.session.Config(
    max_pool_connections=10,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
))

lambda_client = boto3.client('lambda', config=boto3.session.Config(
    max_pool_connections=5,
    retries={'max_attempts': 3, 'mode': 'adaptive'}
))

# Environment variables with validation
SSM_PARAMETER_PREFIX = os.environ.get('SSM_PARAMETER_PREFIX', '/game-statsleaderboards-dev')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'dev')
AWS_REGION_NAME = os.environ.get('AWS_REGION_NAME', 'us-west-2')
API_KEY_PARAMETER_NAMES = os.environ.get('API_KEY_PARAMETER_NAMES', '')  # JSON array of parameter names
FUNCTION_NAME = os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '')  # AWS sets this automatically

# Validate required environment variables
if not SSM_PARAMETER_PREFIX:
    raise EnvironmentError("SSM_PARAMETER_PREFIX environment variable is required")

logger.info(f"Authorizer initialized with SSM_PARAMETER_PREFIX: {SSM_PARAMETER_PREFIX}")

# Parse API key parameter names from environment variable (for 10,000 TPS performance)
_env_parameter_names = []
if API_KEY_PARAMETER_NAMES:
    try:
        _env_parameter_names = json.loads(API_KEY_PARAMETER_NAMES)
        logger.info(f"Loaded {len(_env_parameter_names)} parameter names from environment variable (10,000 TPS path)")
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse API_KEY_PARAMETER_NAMES environment variable, will use fallback discovery")
        _env_parameter_names = []

# Per-container SSM caching with TTL for key rotation support
# Caches ALL API keys from SSM for 60 seconds to eliminate throttling during bursts
# while still allowing key rotation to propagate within 1 minute
_ssm_parameters_cache = None
_ssm_cache_timestamp = 0
_ssm_cache_lock = threading.Lock()
_ssm_cache_ttl = 60  # 60 seconds - balances throttling prevention with key rotation speed

# Per-API-key cache for backward compatibility (secondary cache)
# This provides additional caching for individual key lookups
api_key_cache = {}
cache_ttl = 300  # 5 minutes - longer TTL for individual keys
max_cache_size = 100  # Prevent memory bloat

class AuthorizationError(Exception):
    """Custom exception for authorization failures with detailed error codes"""
    def __init__(self, message: str, error_code: str = "AUTHORIZATION_FAILED"):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)

def generate_policy(principal_id: str, effect: str, resource: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Generate IAM policy for API Gateway with enhanced context support
    
    Args:
        principal_id: Unique identifier for the principal
        effect: 'Allow' or 'Deny'
        resource: Resource ARN
        context: Additional context to pass to Lambda functions
    
    Returns:
        API Gateway authorizer response
    """
    
    # Generate a more permissive resource ARN pattern for API Gateway
    # Convert specific resource ARN to wildcard pattern to avoid authorization issues
    if resource and 'execute-api' in resource:
        # Extract the base API ARN and use wildcard for method/path
        # From: arn:aws:execute-api:region:account:api-id/stage/method/path
        # To:   arn:aws:execute-api:region:account:api-id/stage/*/*
        arn_parts = resource.split('/')
        if len(arn_parts) >= 2:
            base_arn = arn_parts[0]  # arn:aws:execute-api:region:account:api-id
            stage = arn_parts[1]     # stage (e.g., 'dev')
            # Use wildcard pattern for method and path
            resource_pattern = f"{base_arn}/{stage}/*/*"
            logger.info(f"Converted specific resource ARN to wildcard pattern: {resource} -> {resource_pattern}")
            resource = resource_pattern
    
    policy = {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Action': 'execute-api:Invoke',
                    'Effect': effect,
                    'Resource': resource
                }
            ]
        }
    }
    
    # Add context if provided (API Gateway has string-only context values)
    if context:
        # Ensure all context values are strings
        string_context = {}
        for key, value in context.items():
            if isinstance(value, (list, dict)):
                string_context[key] = json.dumps(value)
            else:
                string_context[key] = str(value)
        
        policy['context'] = string_context
    
    return policy

def extract_api_key_from_headers(headers: Dict[str, str]) -> Optional[str]:
    """
    Extract API key from various header formats with comprehensive support
    
    Priority Order:
    1. Authorization header (Bearer, ApiKey, Direct) - takes precedence
    2. X-Api-Key header - fallback for CORS backward compatibility
    
    Supported Authorization formats:
    - Authorization: Bearer <api_key>
    - Authorization: ApiKey <api_key>  
    - Authorization: <api_key> (direct/legacy)
    
    CORS Backward Compatibility:
    - Authorization: (blank/empty) + X-Api-Key: <api_key> → uses X-Api-Key
    - No Authorization header + X-Api-Key: <api_key> → uses X-Api-Key
    
    Args:
        headers: Request headers (case-insensitive)
    
    Returns:
        API key if found, None otherwise
    """
    
    # Normalize header keys to lowercase for case-insensitive lookup
    normalized_headers = {k.lower(): v for k, v in headers.items()}
    
    # PRIORITY 1: Try Authorization header first
    auth_header = normalized_headers.get('authorization')
    
    if auth_header is not None:  # Authorization header exists
        auth_header_stripped = auth_header.strip()
        
        if auth_header_stripped:  # Non-empty Authorization header
            auth_parts = auth_header_stripped.split()
            
            # Handle "Bearer <token>" and "ApiKey <token>" formats
            if len(auth_parts) == 2:
                auth_type, token = auth_parts
                auth_type_lower = auth_type.lower()
                
                if auth_type_lower == 'bearer':
                    logger.debug("API key found in Authorization Bearer header")
                    return token.strip()
                elif auth_type_lower == 'apikey':
                    logger.debug("API key found in Authorization ApiKey header")
                    return token.strip()
            
            # Handle direct Authorization header (legacy support)
            # Only if it doesn't start with known auth types to avoid conflicts
            if not auth_header_stripped.lower().startswith(('bearer ', 'basic ', 'apikey ', 'digest ')):
                logger.debug("API key found in direct Authorization header")
                return auth_header_stripped
        
        # If Authorization header exists but is empty/blank, fall through to X-Api-Key
        # This supports CORS backward compatibility: Authorization: "" + X-Api-Key: <valid>
        if not auth_header_stripped:
            logger.debug("Authorization header is blank, checking X-Api-Key for CORS compatibility")
    
    # PRIORITY 2: Fallback to X-Api-Key header (CORS backward compatibility)
    # This handles:
    # 1. No Authorization header + X-Api-Key header
    # 2. Blank Authorization header + X-Api-Key header
    api_key = normalized_headers.get('x-api-key')
    if api_key:
        api_key_stripped = api_key.strip()
        if api_key_stripped:
            logger.debug("API key found in X-Api-Key header (fallback/CORS compatibility)")
            return api_key_stripped
    
    logger.debug("No API key found in headers")
    return None

def load_ssm_parameters_once() -> Dict[str, Dict[str, Any]]:
    """
    Load all API key parameters from SSM with 60-second caching.
    This eliminates SSM throttling during bursts while allowing key rotation within 1 minute.
    
    OPTIMIZATION: Since we typically have only 1 active API key, we use GetParameter (10,000 TPS)
    instead of GetParametersByPath (100 TPS) for 100x better throughput during cold start bursts.
    
    Returns:
        Dictionary mapping API keys to their metadata
    """
    global _ssm_parameters_cache, _ssm_cache_timestamp
    
    current_time = time.time()
    
    # Fast path: Cache is fresh (less than 60 seconds old)
    if _ssm_parameters_cache is not None and (current_time - _ssm_cache_timestamp) < _ssm_cache_ttl:
        logger.debug(f"Using cached SSM parameters (age: {int(current_time - _ssm_cache_timestamp)}s)")
        return _ssm_parameters_cache
    
    # Thread-safe loading
    with _ssm_cache_lock:
        # Double-check after acquiring lock
        if _ssm_parameters_cache is not None and (current_time - _ssm_cache_timestamp) < _ssm_cache_ttl:
            return _ssm_parameters_cache
        
        cache_age = int(current_time - _ssm_cache_timestamp) if _ssm_cache_timestamp > 0 else None
        if cache_age:
            logger.info(f"Refreshing SSM parameters (cache expired after {cache_age}s)")
        else:
            logger.info("Loading SSM parameters (first time in this container)")
        
        try:
            parameters_map = {}
            active_key_count = 0
            
            # CRITICAL OPTIMIZATION: Use GetParameter (10,000 TPS) to avoid DescribeParameters bottleneck
            # 
            # Problem: DescribeParameters has only 10 TPS limit (NOT affected by high-throughput mode)
            # Solution: Get parameter names from Lambda environment variable (0 SSM calls for discovery)
            #
            # Throughput comparison with high-throughput mode enabled:
            # - DescribeParameters: 10 TPS (NOT affected) → bottleneck with 100+ cold starts
            # - GetParametersByPath: 100 TPS → better but still throttles at scale  
            # - GetParameter: 10,000 TPS → optimal, 100x better than GetParametersByPath
            #
            # Three-tier approach (in order of preference):
            # 1. Environment variable API_KEY_PARAMETER_NAMES (0 SSM calls, 10,000 TPS for values)
            # 2. GetParametersByPath fallback (100 TPS - for backward compatibility)
            # 3. Error if no parameters found
            #
            # Environment variable format: JSON array of parameter names
            # Example: ["/game-statsleaderboards-dev/api-keys/studio1-game1"]
            #
            # Updated by:
            # - CDK deployment (initial setup)
            # - Developer registration Lambda (when keys are created/rotated)
            
            parameter_names = []
            
            # TIER 1: Try environment variable (OPTIMAL - 0 SSM calls for discovery)
            if _env_parameter_names:
                parameter_names = _env_parameter_names
                logger.info(f"✓ Using {len(parameter_names)} parameter names from environment variable (10,000 TPS path)")
            
            # TIER 2: Fall back to GetParametersByPath (100 TPS - backward compatibility)
            else:
                logger.warning("API_KEY_PARAMETER_NAMES not set, falling back to GetParametersByPath (100 TPS)")
                logger.warning("⚠️  For optimal performance, set API_KEY_PARAMETER_NAMES environment variable")
                
                paginator = ssm.get_paginator('get_parameters_by_path')
                
                for page in paginator.paginate(
                    Path=f"{SSM_PARAMETER_PREFIX}/api-keys/",
                    Recursive=False,
                    WithDecryption=False,  # Just get names, we'll fetch values with GetParameter
                    MaxResults=10
                ):
                    for parameter in page.get('Parameters', []):
                        parameter_names.append(parameter.get('Name', ''))
                
                logger.info(f"✓ Loaded {len(parameter_names)} parameter names via GetParametersByPath (100 TPS fallback)")
                
                # SELF-HEALING: Update our own environment variable for future cold starts (10,000 TPS)
                if parameter_names and FUNCTION_NAME:
                    try:
                        logger.info(f"Self-healing: Updating own environment variable for 10,000 TPS performance")
                        
                        # Get current configuration
                        response = lambda_client.get_function_configuration(
                            FunctionName=FUNCTION_NAME
                        )
                        
                        current_env = response.get('Environment', {}).get('Variables', {})
                        current_env['API_KEY_PARAMETER_NAMES'] = json.dumps(parameter_names)
                        
                        # Update configuration
                        lambda_client.update_function_configuration(
                            FunctionName=FUNCTION_NAME,
                            Environment={'Variables': current_env}
                        )
                        
                        logger.info(f"✅ Self-healing complete: Updated env var with {len(parameter_names)} parameter(s)")
                        logger.info("   Next cold start will use 10,000 TPS path automatically")
                        
                    except ClientError as e:
                        error_code = e.response['Error']['Code']
                        logger.warning(f"Self-healing failed: {error_code} (will retry on next cold start)")
                        # Don't fail authorization - this is an optimization
                    except Exception as e:
                        logger.warning(f"Self-healing failed: {str(e)} (will retry on next cold start)")
                        # Don't fail authorization - this is an optimization
            
            # Now fetch each parameter using GetParameter (10,000 TPS each)
            logger.debug(f"Fetching {len(parameter_names)} parameters using GetParameter (10,000 TPS per call)")
            
            for parameter_name in parameter_names:
                try:
                    response = ssm.get_parameter(
                        Name=parameter_name,
                        WithDecryption=True
                    )
                    
                    parameter = response.get('Parameter', {})
                    parameter_value = parameter.get('Value', '')
                    
                    logger.debug(f"Loading parameter: {parameter_name}")
                    
                    # Parse parameter JSON
                    try:
                        key_data = json.loads(parameter_value)
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON in SSM parameter {parameter_name}: {str(e)}")
                        continue
                    except Exception as e:
                        logger.error(f"Error processing parameter {parameter_name}: {str(e)}")
                        continue
                    
                    # Validate parameter structure
                    if not isinstance(key_data, dict):
                        logger.warning(f"Invalid parameter structure in {parameter_name}: not a dictionary")
                        continue
                    
                    stored_api_key = key_data.get('apiKey')
                    key_status = key_data.get('status', 'unknown')
                    
                    if not stored_api_key:
                        logger.warning(f"Missing apiKey in parameter {parameter_name}")
                        continue
                    
                    # Count active keys for security check
                    if key_status == 'active':
                        active_key_count += 1
                    
                    # Validate required fields
                    required_fields = ['studioId', 'gameId', 'studioName', 'gameTitle', 'contactEmail']
                    missing_fields = [field for field in required_fields if not key_data.get(field)]
                    
                    if missing_fields:
                        logger.warning(f"Missing required fields in parameter {parameter_name}: {missing_fields}")
                        continue
                    
                    # Store in cache map
                    parameters_map[stored_api_key] = key_data
                    logger.debug(f"Cached API key from {parameter_name} with status: {key_status}")
                
                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    logger.error(f"Failed to fetch parameter {parameter_name}: {error_code}")
                    # Continue to next parameter instead of failing completely
                    continue
                except Exception as e:
                    logger.error(f"Unexpected error fetching parameter {parameter_name}: {str(e)}")
                    continue
            
            # Security check: Ensure only one active API key exists
            if active_key_count > 1:
                logger.error(f"SECURITY ALERT: {active_key_count} active API keys found - system should have only one")
                raise AuthorizationError("System configuration error: multiple active keys detected", "MULTIPLE_ACTIVE_KEYS")
            
            # Update cache with fresh data and timestamp
            _ssm_parameters_cache = parameters_map
            _ssm_cache_timestamp = current_time
            
            logger.info(f"✓ SSM parameters loaded successfully: {len(parameters_map)} API keys cached, {active_key_count} active (TTL: {_ssm_cache_ttl}s)")
            return parameters_map
        
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            
            logger.error(f"SSM ClientError during parameter loading - Code: {error_code}, Message: {error_message}")
            
            if error_code == 'AccessDeniedException':
                raise AuthorizationError("Insufficient permissions to access SSM parameters", "SSM_ACCESS_DENIED")
            elif error_code in ['ParameterNotFound', 'InvalidParameterName']:
                logger.warning(f"SSM parameter path not found: {SSM_PARAMETER_PREFIX}/api-keys/")
                # Cache empty result to avoid repeated SSM calls
                _ssm_parameters_cache = {}
                _ssm_cache_timestamp = current_time
                return {}
            else:
                raise AuthorizationError(f"SSM service error: {error_message}", "SSM_SERVICE_ERROR")
        
        except Exception as e:
            logger.error(f"Unexpected error loading SSM parameters: {str(e)}")
            raise AuthorizationError(f"Internal error during SSM loading: {str(e)}", "INTERNAL_ERROR")

def get_api_key_from_ssm(api_key: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve and validate API key from cached SSM parameters.
    Uses two-level caching:
    1. Per-API-key cache (5 min TTL) - Fast path for repeated validations
    2. Per-container SSM cache (60 sec TTL) - Eliminates SSM calls during bursts
    
    Args:
        api_key: The API key to validate
    
    Returns:
        API key data if valid and active, None otherwise
    """
    
    # Generate cache key (hash for security)
    cache_key = f"api_key_{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"
    current_time = time.time()
    
    # LEVEL 1: Check per-API-key cache first (fastest path)
    if cache_key in api_key_cache:
        cached_data, cache_time = api_key_cache[cache_key]
        if current_time - cache_time < cache_ttl:
            # Validate cached key status is 'active'
            if cached_data and cached_data.get('status') != 'active':
                logger.warning(f"Cached key has non-active status: {cached_data.get('status')} - forcing fresh lookup")
                del api_key_cache[cache_key]
            else:
                logger.debug("API key found in per-key cache with active status")
                return cached_data
        else:
            # Remove expired entry
            del api_key_cache[cache_key]
    
    try:
        # LEVEL 2: Load from SSM with 60-second container cache (eliminates SSM throttling)
        parameters_map = load_ssm_parameters_once()
        
        # Look up API key in cached parameters using cryptographically secure comparison
        # Use hmac.compare_digest to prevent timing attacks
        key_data = None
        for stored_api_key, stored_key_data in parameters_map.items():
            if hmac.compare_digest(stored_api_key, api_key):
                key_data = stored_key_data
                break
        
        if not key_data:
            logger.debug("API key not found in cached SSM parameters")
            # Cache negative result to prevent repeated lookups
            api_key_cache[cache_key] = (None, current_time)
            return None
        
        # Check if key is active
        key_status = key_data.get('status', 'unknown')
        if key_status != 'active':
            logger.warning(f"API key found but status is '{key_status}' (not active)")
            # Cache negative result
            api_key_cache[cache_key] = (None, current_time)
            return None
        
        # Cache successful result in per-key cache
        api_key_cache[cache_key] = (key_data, current_time)
        
        logger.info(f"Valid API key authenticated for studio: {key_data.get('studioId')}, game: {key_data.get('gameId')}")
        return key_data
    
    except AuthorizationError:
        # Re-raise authorization errors
        raise
    
    except Exception as e:
        logger.error(f"Unexpected error during API key validation: {str(e)}")
        raise AuthorizationError(f"Internal error during authentication: {str(e)}", "INTERNAL_ERROR")

def validate_api_key_data(key_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and normalize API key data structure
    
    Args:
        key_data: Raw API key data from SSM
    
    Returns:
        Validated and normalized authentication context
    
    Raises:
        AuthorizationError: If data validation fails
    """
    
    try:
        # Extract and validate required fields
        studio_id = key_data.get('studioId', '').strip()
        game_id = key_data.get('gameId', '').strip()
        studio_name = key_data.get('studioName', '').strip()
        game_title = key_data.get('gameTitle', '').strip()
        contact_email = key_data.get('contactEmail', '').strip()
        
        if not all([studio_id, game_id, studio_name, game_title, contact_email]):
            missing_fields = []
            if not studio_id: missing_fields.append('studioId')
            if not game_id: missing_fields.append('gameId')
            if not studio_name: missing_fields.append('studioName')
            if not game_title: missing_fields.append('gameTitle')
            if not contact_email: missing_fields.append('contactEmail')
            
            raise AuthorizationError(f"Missing required fields in API key data: {', '.join(missing_fields)}", "INVALID_KEY_DATA")
        
        # Validate and normalize permissions
        permissions = key_data.get('permissions', ['read'])
        if not isinstance(permissions, list):
            permissions = ['read']
        
        # Ensure permissions are valid
        valid_permissions = ['read', 'write', 'admin']
        permissions = [p for p in permissions if p in valid_permissions]
        if not permissions:
            permissions = ['read']  # Default fallback
        
        # Validate and normalize rate limit
        rate_limit = key_data.get('rateLimit', 1000)
        try:
            rate_limit = int(rate_limit)
            if rate_limit <= 0:
                rate_limit = 1000
        except (ValueError, TypeError):
            rate_limit = 1000
        
        # Create normalized authentication context
        auth_context = {
            'user_id': f"{studio_id}_{game_id}",
            'auth_type': 'api_key',
            'studio_id': studio_id,
            'game_id': game_id,
            'studio_name': studio_name,
            'game_title': game_title,
            'contact_email': contact_email,
            'permissions': permissions,
            'rate_limit': rate_limit,
            'environment': key_data.get('environment', ENVIRONMENT),
            'status': key_data.get('status', 'active')
        }
        
        logger.debug(f"API key data validated successfully for {studio_id}/{game_id}")
        return auth_context
        
    except AuthorizationError:
        raise
    except Exception as e:
        logger.error(f"Error validating API key data: {str(e)}")
        raise AuthorizationError(f"Invalid API key data format: {str(e)}", "INVALID_KEY_DATA")

@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    Lambda authorizer for backend (developer) API routes.
    
    Validates the StudioAPI Key from the Authorization header against SSM
    Parameter Store and provides comprehensive context to downstream Lambda
    functions.
    
    This authorizer handles backend routes only (/developer/*, /leaderboards/config/*,
    /leaderboards/configs, /leaderboards/admin/*). Player routes use a separate
    authorizer — see auth/playerAuthorizer.py.
    
    Args:
        event: API Gateway authorizer event
        context: Lambda context
    
    Returns:
        API Gateway authorizer response with policy and context
    """
    
    try:
        # Extract request information
        method_arn = event.get('methodArn', '*')
        headers = event.get('headers', {})
        request_context = event.get('requestContext', {})
        
        logger.info("Processing authorization request", extra={
            "method_arn": method_arn,
            "request_id": request_context.get('requestId', 'unknown'),
            "source_ip": request_context.get('identity', {}).get('sourceIp', 'unknown'),
            "user_agent": headers.get('User-Agent', 'unknown')
        })
        
        # Extract API key from headers
        api_key = extract_api_key_from_headers(headers)
        
        if not api_key:
            logger.warning("No API key found in request headers")
            return generate_policy(principal_id="no-api-key", effect='Deny', resource=method_arn)
        
        # Validate API key against SSM Parameter Store
        try:
            key_data = get_api_key_from_ssm(api_key)
            
            if not key_data:
                logger.warning("Invalid or inactive API key provided")
                return generate_policy(principal_id="invalid-api-key", effect='Deny', resource=method_arn)
            
            # Validate and normalize key data
            auth_context = validate_api_key_data(key_data)
            
            # Generate successful authorization policy with comprehensive context
            policy = generate_policy(
                principal_id=auth_context['user_id'],
                effect='Allow',
                resource=method_arn,
                context={
                    'authType': auth_context['auth_type'],
                    'studioId': auth_context['studio_id'],
                    'gameId': auth_context['game_id'],
                    'studioName': auth_context['studio_name'],
                    'gameTitle': auth_context['game_title'],
                    'contactEmail': auth_context['contact_email'],
                    'permissions': ','.join(auth_context['permissions']),
                    'rateLimit': str(auth_context['rate_limit']),
                    'environment': auth_context['environment'],
                    'userId': auth_context['user_id'],
                    'keyStatus': auth_context['status']
                }
            )
            
            logger.info("Authorization successful", extra={
                "principal_id": auth_context['user_id'],
                "studio_id": auth_context['studio_id'],
                "game_id": auth_context['game_id'],
                "permissions": auth_context['permissions'],
                "rate_limit": auth_context['rate_limit']
            })
            
            return policy
            
        except AuthorizationError as e:
            logger.error(f"Authorization failed: {e.message}", extra={
                "error_code": e.error_code,
                "method_arn": method_arn
            })
            
            return generate_policy(
                principal_id=f"auth-error-{e.error_code.lower()}",
                effect='Deny',
                resource=method_arn
            )
    
    except Exception as e:
        logger.error(f"Unexpected authorizer error: {str(e)}", extra={
            "error_type": type(e).__name__,
            "method_arn": event.get('methodArn', 'unknown'),
            "request_id": context.aws_request_id
        })
        
        # Deny access on any unexpected error for security
        return generate_policy(
            principal_id="system-error",
            effect='Deny',
            resource=event.get('methodArn', '*')
        )